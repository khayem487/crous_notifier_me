import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseHTML } from 'linkedom';
import { parseDocument } from '../src/parser.js';
import { CERGY_URL, validateSnapshot } from '../src/model.js';

const NOW = '2026-10-26T20:00:00.000Z';
const fixture = name => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8');
function parse(html) {
  const { document } = parseHTML(html);
  return parseDocument(document, { url: CERGY_URL, now: NOW });
}

test('explicit public zero is complete without turning navigation or JSON examples into offers', () => {
  const snapshot = parse(fixture('empty-public-cergy.html'));
  assert.equal(snapshot.status, 'COMPLETE');
  assert.equal(snapshot.audience, 'PUBLIC_ONLY');
  assert.equal(snapshot.audience_confirmed, true);
  assert.equal(snapshot.pagination_complete, true);
  assert.equal(snapshot.total_count, 0);
  assert.deepEqual(snapshot.offers, []);
  assert.equal(Date.parse(snapshot.observed_at), Date.parse(NOW));
  validateSnapshot(snapshot);
});

test('two visible cards preserve a price range and do not equate Individuel with a room', () => {
  const snapshot = parse(fixture('two-offers-public.html'));
  assert.equal(snapshot.status, 'COMPLETE');
  assert.equal(snapshot.total_count, 2);
  assert.equal(snapshot.offers.length, 2);
  const alpha = snapshot.offers.find(offer => offer.url.endsWith('/TEST-ALPHA-001'));
  const beta = snapshot.offers.find(offer => offer.url.endsWith('/TEST-BETA-002'));
  assert.ok(alpha && beta);
  assert.equal(alpha.rent_eur, null, 'a range is not an exact price');
  assert.equal(Number(alpha.rent_min_eur), 350.50);
  assert.equal(Number(alpha.rent_max_eur), 420);
  assert.equal(alpha.housing_type, null, 'occupation mode says nothing about the housing type');
  assert.equal(alpha.city, 'Cergy');
  assert.equal(beta.housing_type?.toLowerCase(), 'chambre', 'only the explicit type label confirms this fixture room');
  assert.equal(Number(beta.rent_eur), 480);
  assert.equal(Number(beta.surface_m2), 12);
  validateSnapshot(snapshot);
});

test('a visible next-page control prevents treating the current cards as a complete search', () => {
  const html = fixture('two-offers-public.html').replace('</main>', `
    <nav class="fr-pagination" aria-label="Pagination"><ul class="fr-pagination__list">
      <li><a class="fr-pagination__link fr-pagination__link--next" href="?page=2">Page suivante</a></li>
    </ul></nav></main>`);
  const snapshot = parse(html);
  assert.equal(snapshot.status, 'PARTIAL');
  assert.equal(snapshot.pagination_complete, false);
  assert.equal(snapshot.offers.length, 2, 'known visible offers remain useful despite incomplete pagination');
});

test('an enabled next-page button is also pagination, even without an anchor href', () => {
  const html = fixture('two-offers-public.html').replace('</main>', `
    <nav class="fr-pagination" aria-label="Pagination">
      <button class="fr-pagination__link fr-pagination__link--next" type="button">Page suivante</button>
    </nav></main>`);
  const snapshot = parse(html);
  assert.equal(snapshot.status, 'PARTIAL');
  assert.equal(snapshot.pagination_complete, false);
});

test('a CSS-hidden offer is not a visible listing or proof of a complete result', () => {
  const html = fixture('two-offers-public.html').replace(
    '<article class="fr-card">', '<article class="fr-card" style="display:none">');
  const snapshot = parse(html);
  assert.equal(snapshot.status, 'PARTIAL');
  assert.equal(snapshot.offers.length, 1);
  assert.equal(snapshot.offers[0].offer_id, 'TEST-BETA-002');
});

test('a mismatching visible result count is partial, never a proof of removal', () => {
  const html = fixture('two-offers-public.html').replace('2 logements trouvés', '8 logements trouvés');
  const snapshot = parse(html);
  assert.equal(snapshot.status, 'PARTIAL');
  assert.equal(snapshot.total_count, 8);
  assert.equal(snapshot.offers.length, 2);
});

test('an authentication/DSE gate is unknown and requires a normal manual sign-in', () => {
  const snapshot = parse(fixture('auth-required.html'));
  assert.equal(snapshot.status, 'UNKNOWN');
  assert.equal(snapshot.reason, 'AUTH_REQUIRED');
  assert.deepEqual(snapshot.offers, []);
  assert.equal(snapshot.total_count, null);
  assert.notEqual(snapshot.audience, 'AUTHENTICATED');
});

test('maintenance cannot replace the reliable result with a zero', () => {
  const snapshot = parse(fixture('maintenance.html'));
  assert.equal(snapshot.status, 'UNKNOWN');
  assert.equal(snapshot.total_count, null);
  assert.deepEqual(snapshot.offers, []);
});

test('a foreign accommodation link is not retained or counted as a complete search', () => {
  const html = fixture('two-offers-public.html').replace(
    'href="/tools/47/accommodations/TEST-ALPHA-001"',
    'href="https://example.invalid/tools/47/accommodations/TEST-ALPHA-001"');
  const snapshot = parse(html);
  assert.notEqual(snapshot.status, 'COMPLETE');
  assert.ok(snapshot.offers.every(offer => new URL(offer.url).hostname === 'trouverunlogement.lescrous.fr'));
  assert.ok(!snapshot.offers.some(offer => offer.url.includes('example.invalid')));
});

test('a disconnected page cannot claim an authenticated audience without an explicit account signal', () => {
  const snapshot = parse(fixture('two-offers-public.html'));
  assert.equal(snapshot.audience, 'PUBLIC_ONLY');
  const authenticated = parse(fixture('two-offers-public.html').replace('Se connecter', 'Se déconnecter'));
  assert.equal(authenticated.audience, 'AUTHENTICATED');
  assert.equal(authenticated.audience_confirmed, true);
});

test('logout text inside an offer card is not evidence that the current session is authenticated', () => {
  const html = fixture('two-offers-public.html').replace(
    '<p>Occupation : Individuel</p>', '<p>Occupation : Individuel</p><button type="button">Se déconnecter</button>');
  const snapshot = parse(html);
  assert.equal(snapshot.audience, 'PUBLIC_ONLY');
  assert.equal(snapshot.audience_confirmed, true, 'only the real header login signal confirms the public scope');
});

test('a zero headline contradicted by visible cards is not complete or an empty result', () => {
  const snapshot = parse(fixture('two-offers-public.html').replace(
    '2 logements trouvés pour Cergy', 'Aucun logement trouvé pour Cergy'));
  assert.notEqual(snapshot.status, 'COMPLETE');
  assert.notEqual(snapshot.total_count, 0);
});
