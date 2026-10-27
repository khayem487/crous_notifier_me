import {DEFAULT_CONFIG, validateSnapshot, offerKey, matchOffer, roomConfirmed} from './model.js';
export function initialState() {
  return {version: 1, config: {...DEFAULT_CONFIG}, enabled: false, authPaused: false, authNoticeActive: false,
    tabId: null, inFlight: null, nextAllowedAt: 0, status: 'STOPPED', message: 'Surveillance en pause.',
    records: {}, history: [], events: [], lastSnapshot: null, lastReliable: {}, lastApplied: {},
    outbox: [], delivered: [], alertsBlocked: false, notificationError: null};
}
const prices = offer => JSON.stringify([offer.rent_eur, offer.rent_min_eur, offer.rent_max_eur]);
const facts = offer => JSON.stringify([offer.title,offer.city,offer.housing_type,offer.surface_m2,offer.residence,offer.available_from]);
const confirmed = offer => roomConfirmed(offer) && offer.city === 'Cergy';
export function applySnapshot(previous, raw, {origin = 'LIVE', now = Date.now()} = {}) {
  const snapshot = validateSnapshot(raw), state = structuredClone(previous);
  const notices = [], scope = snapshot.audience, stamp = Date.parse(snapshot.observed_at);
  state.history.unshift({observed_at: snapshot.observed_at, status: snapshot.status, audience: scope,
    total_count: snapshot.total_count, visible_count: snapshot.offers.length, reason: snapshot.reason || '', origin});
  state.history = state.history.slice(0, 300);
  // Imported older files remain in the history, without rolling back current results.
  if (stamp <= (state.lastApplied[scope] || 0) || stamp > now + 300000) {
    state.message = 'Relevé archivé ; il ne remplace pas un résultat plus récent.';
    return {state, notices};
  }
  state.lastSnapshot = snapshot;
  if (snapshot.status === 'UNKNOWN') {
    state.status = snapshot.reason === 'AUTH_REQUIRED' || snapshot.reason === 'ACCESS_REQUIRES_USER' ? 'AUTH_REQUIRED' : 'UNKNOWN';
    state.message = state.status === 'AUTH_REQUIRED' ? 'Reconnexion ou intervention nécessaire dans le site officiel.'
      : 'Résultat inconnu ; les dernières fiches fiables restent conservées.';
    return {state, notices};
  }
  state.lastApplied[scope] = stamp;
  if (snapshot.status === 'COMPLETE') state.lastReliable[scope] = snapshot;
  const seen = new Set();
  for (const offer of snapshot.offers) {
    const key = offerKey(offer, scope), old = state.records[key];
    seen.add(key);
    const kind = !old || !old.present ? 'NEW'
      : confirmed(offer) && !confirmed(old.offer) ? 'MATCH_CONFIRMED'
      : prices(old.offer) !== prices(offer) ? 'PRICE_CHANGED'
      : facts(old.offer) !== facts(offer) ? 'DETAILS_CHANGED' : null;
    state.records[key] = {offer, present: true, first_seen: old?.first_seen || snapshot.observed_at,
      last_seen: snapshot.observed_at, origin, removed_at: null};
    if (kind) {
      const event = {kind, key, title: offer.title, url: offer.url, audience: scope,
        observed_at: snapshot.observed_at, origin};
      state.events.unshift(event);
      // Unknown housing types remain candidates for manual verification, never confirmed rooms.
      const candidate = !offer.housing_type || roomConfirmed(offer);
      if (origin === 'LIVE' && snapshot.audience_confirmed && candidate && matchOffer(offer, state.config))
        notices.push({...event, confirmed: roomConfirmed(offer) && offer.city === 'Cergy'});
    }
  }
  if (snapshot.status === 'COMPLETE') {
    for (const [key, row] of Object.entries(state.records)) {
      if (row.offer.audience === scope && row.present && !seen.has(key)) {
        row.present = false; row.removed_at = snapshot.observed_at;
        state.events.unshift({kind: 'NO_LONGER_LISTED', key, title: row.offer.title, url: row.offer.url,
          audience: scope, observed_at: snapshot.observed_at, origin});
      }
    }
  }
  state.events = state.events.slice(0, 500);
  // Bound old, inactive records without discarding the current visible results.
  const inactive = Object.entries(state.records).filter(([,row]) => !row.present)
    .sort((a,b) => Date.parse(b[1].last_seen) - Date.parse(a[1].last_seen));
  for (const [key] of inactive.slice(1000)) delete state.records[key];
  state.status = snapshot.status;
  state.message = origin === 'IMPORT' ? 'Import local validé ; aucune notification ni requête réseau déclenchée.'
    : snapshot.status === 'COMPLETE' ? 'Relevé complet pour ce périmètre, à cet instant.'
      : 'Résultats partiels : seules les fiches de la page courante sont couvertes ; aucune disparition n’est déduite.';
  return {state, notices};
}
