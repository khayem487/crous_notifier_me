import {CERGY_URL, ORIGIN, normal, text, officialUrl, validateSnapshot} from './model.js';
function visible(el) {
  if (!el || el.closest('script,style,template,noscript,[hidden],[aria-hidden="true"]')) return false;
  for (let node = el; node?.nodeType === 1; node = node.parentElement) {
    if (node.style?.display === 'none' || ['hidden','collapse'].includes(node.style?.visibility)) return false;
    const computed = node.ownerDocument?.defaultView?.getComputedStyle?.(node);
    if (computed && (computed.display === 'none' || ['hidden','collapse'].includes(computed.visibility))) return false;
  }
  return true;
}
function safeText(root) {
  function collect(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1 || !visible(node) || node.matches('script,style,template,noscript,nav,header,footer')) return '';
    return [...node.childNodes].map(collect).join(' ');
  }
  return text(collect(root));
}
function money(raw) {
  const matches = [...raw.matchAll(/(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(?:€|euros?)/gi)]
    .map(m => Number(m[1].replace(/[\s\u00a0\u202f]/g, '').replace(',', '.'))).filter(Number.isFinite);
  if (matches.length === 1) return {rent_eur: matches[0], rent_min_eur: null, rent_max_eur: null};
  if (matches.length === 2 && matches[0] <= matches[1])
    return matches[0] === matches[1] ? {rent_eur: matches[0], rent_min_eur: null, rent_max_eur: null}
      : {rent_eur: null, rent_min_eur: matches[0], rent_max_eur: matches[1]};
  return {rent_eur: null, rent_min_eur: null, rent_max_eur: null};
}
export function parseDocument(document, {url = CERGY_URL, now = new Date().toISOString()} = {}) {
  officialUrl(url, 'search');
  const main = document.querySelector('main');
  // An actual header logout item can be inside a collapsed account menu. A DOM
  // template or a contradictory login marker must never establish authentication.
  const navText = [...document.querySelectorAll('header a,header button,.fr-header a,.fr-header button,[role="banner"] a,[role="banner"] button')]
    .filter(el => !el.closest('script,style,template,noscript')).map(el => normal(el.textContent));
  const logout = navText.some(t => /^(?:se deconnecter|deconnexion)$/.test(t));
  const login = navText.some(t => /^(?:se connecter|connexion|me connecter|identification|s.identifier)$/.test(t));
  const authenticated = logout && !login, publicConfirmed = login && !logout;
  const base = {schema_version: 1, source_url: CERGY_URL, zone: 'Cergy',
    audience: authenticated ? 'AUTHENTICATED' : 'PUBLIC_ONLY', audience_confirmed: authenticated || publicConfirmed,
    observed_at: now, status: 'UNKNOWN', pagination_complete: false, total_count: null, offers: []};
  const unknown = reason => validateSnapshot({...base, status: 'UNKNOWN', reason});
  if (login && logout) return unknown('AUDIENCE_CONFLICT');
  if (!main) {
    const shell = normal(safeText(document.body));
    // The official portal can return a bare 500 page without its normal main.
    // A price or an error string inside a listing is never an error shell.
    if (/^50[0234]$/.test(shell) || /^50[0234]\s*[-–:]\s*(?:erreur interne|service indisponible)\b/.test(shell))
      return unknown('SOURCE_UNAVAILABLE');
    return unknown('RESULTS_NOT_READY');
  }
  if ([...main.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible))
    return unknown('RESULTS_NOT_READY');
  const body = safeText(main), normalized = normal(body);
  if (/session (?:a )?expire|vous devez vous reconnecter|connectez.vous pour (?:consulter|acceder)|identifi(?:ez|er).vous/.test(normalized) ||
      (main.querySelector('input[type="password"]') && /connexion|identifi|dossier social/.test(normalized)))
    return unknown('AUTH_REQUIRED');
  if (/captcha|verifiez que vous etes|verification de securite|acces refuse/.test(normalized)) return unknown('ACCESS_REQUIRES_USER');
  if (/maintenance|erreur technique|erreur interne|service (?:est )?indisponible|une erreur est survenue/.test(normalized))
    return unknown('SOURCE_UNAVAILABLE');
  const headings = [...main.querySelectorAll('h1,h2,h3,[role="status"]')].filter(visible).map(el => normal(el.textContent));
  const zero = headings.some(t => /\baucun logement trouve pour cergy\b/.test(t));
  const counts = [...new Set(headings.flatMap(t => [...t.matchAll(/\b(\d+)\s+logements?\s+(?:trouves?|disponibles?)\s+(?:pour|a)\s+cergy\b/g)].map(m => Number(m[1]))))];
  if (counts.length > 1) return unknown('CONTRADICTORY_RESULT_COUNTS');
  const offers = [], seen = new Set(); let invalid = false;
  for (const card of [...main.querySelectorAll('.fr-card')].filter(visible)) {
    const link = card.querySelector('.fr-card__title a');
    if (!link) continue;
    let official;
    try { official = officialUrl(new URL(link.getAttribute('href') || '', ORIGIN).href); } catch { invalid = true; continue; }
    if (seen.has(official)) continue;
    seen.add(official);
    const cardText = safeText(card);
    const title = text(link.textContent);
    if (!title) { invalid = true; continue; }
    const address = text(card.querySelector('.fr-card__desc')?.textContent);
    const badge = [...card.querySelectorAll('p.fr-badge')].filter(visible).map(el => text(el.textContent)).join(' ');
    const explicitType = cardText.match(/type de logement\s*:\s*(chambre(?: individuelle)?|studio|appartement)\b/i);
    const exactSurface = cardText.match(/\bsurface\s*:\s*(\d+(?:[.,]\d+)?)\s*m[²2](?!\s*(?:a|à|-|–|et)\s*\d)/i);
    offers.push({offer_id: new URL(official).pathname.split('/').pop(), url: official, title,
      residence: title, city: /\b\d{5}\s+Cergy\b/i.test(address) ? 'Cergy' : null,
      housing_type: explicitType ? normal(explicitType[1]) : null, ...money(badge),
      surface_m2: exactSurface ? Number(exactSurface[1].replace(',', '.')) : null,
      available_from: null, observed_at: now, audience: base.audience});
  }
  if (zero && (offers.length || (counts[0] || 0) > 0)) return unknown('CONTRADICTORY_RESULTS');
  const count = zero ? 0 : (counts[0] ?? null);
  const next = [...main.querySelectorAll('.fr-pagination a,.fr-pagination button,nav[aria-label*="agination"] a,nav[aria-label*="agination"] button')]
    .filter(visible).some(el => !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true' &&
      (/suivant|next/i.test(el.textContent + ' ' + (el.getAttribute('title') || '') + ' ' + (el.getAttribute('aria-label') || '')) ||
       el.classList.contains('fr-pagination__link--next') || el.getAttribute('rel') === 'next' ||
       (/^\d+$/.test(text(el.textContent)) && Number(text(el.textContent)) > 1 && el.getAttribute('aria-current') !== 'page')) &&
      (el.hasAttribute('href') || el.tagName === 'BUTTON'));
  const complete = base.audience_confirmed && count !== null && count === offers.length && !next && !invalid;
  if (!offers.length && count === null) return unknown('RESULTS_NOT_RECOGNISED');
  return validateSnapshot({...base, status: complete ? 'COMPLETE' : 'PARTIAL',
    pagination_complete: complete, total_count: count, offers,
    reason: complete ? (zero ? 'OFFICIAL_EMPTY_SEARCH' : 'VISIBLE_RESULTS_COMPLETE') : 'VISIBLE_RESULTS_PARTIAL'});
}
