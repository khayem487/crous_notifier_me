export const ORIGIN = 'https://trouverunlogement.lescrous.fr';
export const CERGY_URL = ORIGIN + '/tools/47/search?bounds=1.9999694_49.0564525_2.0911198_49.017998&locationName=Cergy';
export const DEFAULT_CONFIG = Object.freeze({zone: 'Cergy', housing_type: 'chambre', move_in: 'ASAP',
  max_rent_eur: null, source_url: CERGY_URL, interval_seconds: 300, require_authenticated: true});
const TOP = ['schema_version','source_url','zone','audience','audience_confirmed','observed_at','status',
  'pagination_complete','total_count','reason','offers'];
const OFFER = ['offer_id','url','title','city','housing_type','rent_eur','rent_min_eur','rent_max_eur',
  'surface_m2','residence','available_from','observed_at','audience'];
const AUDIENCES = ['PUBLIC_ONLY','AUTHENTICATED'];
export function text(value) { return String(value || '').replace(/\s+/g, ' ').trim(); }
export function normal(value) { return text(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
function object(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(key => !allowed.includes(key))) throw new Error('Champs non reconnus ou objet invalide.');
}
function label(value, max = 400, nullable = true) {
  if (value == null && nullable) return null;
  if (typeof value !== 'string' || !value.trim() || value.length > max ||
      /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) throw new Error('Texte invalide.');
  return text(value);
}
function number(value, nullable = true) {
  if (value == null && nullable) return null;
  if (!['number','string'].includes(typeof value) || String(value).trim() === '') throw new Error('Nombre invalide.');
  const result = Number(value);
  if (!Number.isFinite(result) || result < 0 || result > 1000000) throw new Error('Nombre invalide.');
  return result;
}
export function instant(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
      !Number.isFinite(Date.parse(value))) throw new Error('Horodatage avec fuseau requis.');
  const [year,month,day] = value.slice(0,10).split('-').map(Number);
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year,month,0)).getUTCDate() ||
      Date.parse(value) > Date.now() + 300000) throw new Error('Date invalide ou située dans le futur.');
  return new Date(value).toISOString();
}
export function officialUrl(value, kind = 'offer') {
  if (typeof value !== 'string' || value.length > 2048 || /[\u0000-\u0020]/.test(value)) throw new Error('Lien officiel invalide.');
  const url = new URL(value);
  if (url.origin !== ORIGIN || url.username || url.password || url.port || url.hash) throw new Error('Lien hors du domaine autorisé.');
  if (kind === 'search') {
    const expected = new URL(CERGY_URL);
    if (url.pathname !== expected.pathname || [...url.searchParams].length !== 2 ||
        [...expected.searchParams].some(([key,val]) => url.searchParams.get(key) !== val))
      throw new Error('Seule la recherche Cergy configurée est autorisée.');
    return CERGY_URL;
  }
  if (!/^\/tools\/[A-Za-z0-9_-]+\/accommodations\/[A-Za-z0-9_-]+$/.test(url.pathname) || url.search)
    throw new Error('Lien de fiche officiel sans jeton requis.');
  return url.href;
}
export function validateConfig(input = {}) {
  object(input, Object.keys(DEFAULT_CONFIG));
  const value = {...DEFAULT_CONFIG, ...input};
  if (value.zone !== 'Cergy' || value.housing_type !== 'chambre' || value.move_in !== 'ASAP')
    throw new Error('Cette version suit Cergy, préférence chambre, entrée dès que possible.');
  value.source_url = officialUrl(value.source_url, 'search');
  if (typeof value.require_authenticated !== 'boolean') throw new Error('Mode connecté ou public explicite requis.');
  if (!Number.isInteger(value.interval_seconds) || value.interval_seconds < 300 || value.interval_seconds > 86400)
    throw new Error('Intervalle requis : 300 à 86 400 secondes.');
  value.max_rent_eur = number(value.max_rent_eur);
  if (value.max_rent_eur !== null && value.max_rent_eur <= 0) throw new Error('Budget positif ou vide requis.');
  return value;
}
export function validateSnapshot(input) {
  object(input, TOP);
  if (input.schema_version !== 1 || input.zone !== 'Cergy' || !AUDIENCES.includes(input.audience) ||
      typeof input.audience_confirmed !== 'boolean' || typeof input.pagination_complete !== 'boolean' ||
      !['COMPLETE','PARTIAL','UNKNOWN'].includes(input.status)) throw new Error('Contrat de relevé v1 invalide.');
  if (input.audience === 'AUTHENTICATED' && !input.audience_confirmed)
    throw new Error('Le périmètre connecté doit être explicitement confirmé.');
  const source_url = officialUrl(input.source_url, 'search');
  const observed_at = instant(input.observed_at);
  if (!Array.isArray(input.offers) || input.offers.length > 1000) throw new Error('Liste de fiches invalide.');
  const urls = new Set(), ids = new Set();
  const offers = input.offers.map(raw => {
    object(raw, OFFER);
    const url = officialUrl(raw.url);
    const id = new URL(url).pathname.split('/').pop();
    if (raw.offer_id != null && raw.offer_id !== id) throw new Error('Identifiant incompatible avec la fiche.');
    if (urls.has(url) || ids.has(id)) throw new Error('Fiche dupliquée dans le relevé.');
    urls.add(url); ids.add(id);
    if (raw.audience != null && raw.audience !== input.audience) throw new Error('Périmètre de fiche incohérent.');
    const offer = {offer_id: id, url, title: label(raw.title, 300, false),
      city: label(raw.city, 100), housing_type: label(raw.housing_type, 100),
      rent_eur: number(raw.rent_eur), rent_min_eur: number(raw.rent_min_eur), rent_max_eur: number(raw.rent_max_eur),
      surface_m2: number(raw.surface_m2), residence: label(raw.residence, 300),
      available_from: raw.available_from ?? null, observed_at: instant(raw.observed_at || observed_at), audience: input.audience};
    if (offer.rent_min_eur != null && offer.rent_max_eur != null && offer.rent_min_eur > offer.rent_max_eur)
      throw new Error('Fourchette de prix inversée.');
    if (offer.rent_eur != null && ((offer.rent_min_eur != null && offer.rent_min_eur !== offer.rent_eur) ||
        (offer.rent_max_eur != null && offer.rent_max_eur !== offer.rent_eur)))
      throw new Error('Prix exact et fourchette contradictoires.');
    if (offer.available_from !== null && (!/^\d{4}-\d{2}-\d{2}$/.test(offer.available_from) ||
        !Number.isFinite(Date.parse(offer.available_from + 'T00:00:00Z')) ||
        new Date(offer.available_from + 'T00:00:00Z').toISOString().slice(0,10) !== offer.available_from))
      throw new Error('Date de disponibilité invalide.');
    return offer;
  });
  const count = input.total_count;
  if (input.reason != null && (typeof input.reason !== 'string' || !/^[A-Z][A-Z0-9_]{0,99}$/.test(input.reason)))
    throw new Error('Code de motif invalide.');
  if (count !== null && (!Number.isInteger(count) || count < 0 || count > 100000)) throw new Error('Compteur invalide.');
  if (input.status === 'UNKNOWN' && (offers.length || count !== null || input.pagination_complete))
    throw new Error('Un relevé inconnu ne peut pas contenir de résultat fiable.');
  if (input.status === 'COMPLETE' && (!input.audience_confirmed || !input.pagination_complete ||
      count === null || count !== offers.length)) throw new Error('Relevé complet incohérent.');
  return {schema_version: 1, source_url, zone: 'Cergy', audience: input.audience,
    audience_confirmed: input.audience_confirmed, observed_at, status: input.status,
    pagination_complete: input.pagination_complete, total_count: count,
    ...(input.reason ? {reason: label(input.reason, 100, false)} : {}), offers};
}
export function offerKey(offer, audience = offer.audience) { return audience + '|' + officialUrl(offer.url); }
export function roomConfirmed(offer) { return /^chambre(?:\s|$)/.test(normal(offer.housing_type)); }
export function matchOffer(offer, config) {
  if (offer.city && normal(offer.city) !== 'cergy') return false;
  const cheapest = offer.rent_eur ?? offer.rent_min_eur;
  return config.max_rent_eur === null || cheapest === null || cheapest <= config.max_rent_eur;
}
