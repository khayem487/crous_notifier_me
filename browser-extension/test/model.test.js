import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CERGY_URL, validateSnapshot, validateConfig, DEFAULT_CONFIG } from '../src/model.js';

const NOW = '2026-10-26T20:00:00.000Z';
const knownOffer = () => ({
  offer_id: 'TEST-001', url: 'https://trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-001',
  title: 'TEST — Résidence fictive', city: null, housing_type: null, rent_eur: null,
  rent_min_eur: 350, rent_max_eur: 420, surface_m2: null, residence: 'TEST — Résidence fictive',
  available_from: null, observed_at: NOW, audience: 'PUBLIC_ONLY'
});
const snapshot = () => ({
  schema_version: 1, source_url: CERGY_URL, zone: 'Cergy', audience: 'PUBLIC_ONLY',
  audience_confirmed: true, observed_at: NOW, status: 'COMPLETE',
  pagination_complete: true, total_count: 1, offers: [knownOffer()]
});

test('default criteria are Cergy, room preference, 300 seconds and no assumed budget', () => {
  const config = validateConfig({});
  assert.equal(config.zone, 'Cergy');
  assert.equal(config.source_url, CERGY_URL);
  assert.equal(config.interval_seconds, 300);
  assert.equal(config.max_rent_eur, null);
  assert.equal(DEFAULT_CONFIG.housing_type, 'chambre');
  assert.equal(config.require_authenticated, true, 'normal monitoring must use the confirmed DSE session');
  assert.equal(validateConfig({ require_authenticated: false }).require_authenticated, false, 'public mode is an explicit opt-in');
});

test('configuration rejects an aggressive interval and unexpected settings', () => {
  assert.throws(() => validateConfig({ interval_seconds: 60 }));
  assert.throws(() => validateConfig({ interval_seconds: NaN }));
  assert.throws(() => validateConfig({ max_rent_eur: -1 }));
  assert.throws(() => validateConfig({ password: 'TEST-NOT-A-REAL-SECRET' }));
});

test('snapshot v1 round trip preserves scope, ranges and unknown housing facts', () => {
  const validated = validateSnapshot(JSON.parse(JSON.stringify(snapshot())));
  assert.equal(validated.schema_version, 1);
  assert.equal(validated.status, 'COMPLETE');
  assert.equal(validated.audience, 'PUBLIC_ONLY');
  assert.equal(validated.offers[0].housing_type, null);
  assert.equal(Number(validated.offers[0].rent_min_eur), 350);
  assert.equal(Number(validated.offers[0].rent_max_eur), 420);
});

test('unrecognised snapshot or offer fields are rejected instead of storing hidden data', () => {
  assert.throws(() => validateSnapshot({ ...snapshot(), cookies: 'TEST-COOKIE' }));
  const withHtml = snapshot();
  withHtml.offers[0].html = '<div>TEST ONLY</div>';
  assert.throws(() => validateSnapshot(withHtml));
});

test('unknown schema versions, unaudited dates and unconfirmed authenticated scopes are rejected', () => {
  assert.throws(() => validateSnapshot({ ...snapshot(), schema_version: 2 }));
  assert.throws(() => validateSnapshot({ ...snapshot(), observed_at: 'yesterday' }));
  const unconfirmed = { ...snapshot(), audience: 'AUTHENTICATED', audience_confirmed: false };
  unconfirmed.offers[0].audience = 'AUTHENTICATED';
  assert.throws(() => validateSnapshot(unconfirmed));
});

test('foreign hosts, embedded credentials and token parameters never enter saved offers', () => {
  for (const url of [
    'https://example.invalid/tools/47/accommodations/TEST-001',
    'http://trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-001',
    'https://TEST:TEST@trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-001',
    'https://trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-001?token=TEST'
  ]) {
    const input = snapshot(); input.offers[0].url = url;
    assert.throws(() => validateSnapshot(input), url);
  }
});

test('unknown observations cannot smuggle offers into a reliable result', () => {
  assert.throws(() => validateSnapshot({ ...snapshot(), status: 'UNKNOWN', total_count: null }));
});

test('incomplete pagination or a different result count cannot remain COMPLETE', () => {
  assert.throws(() => validateSnapshot({ ...snapshot(), pagination_complete: false }));
  assert.throws(() => validateSnapshot({ ...snapshot(), total_count: 3 }));
});

test('price ranges are ordered and cannot be passed off as an unrelated exact rent', () => {
  const reversed = snapshot(); reversed.offers[0].rent_min_eur = 420; reversed.offers[0].rent_max_eur = 350;
  assert.throws(() => validateSnapshot(reversed));
  const invalid = snapshot(); invalid.offers[0].rent_eur = 'NaN';
  assert.throws(() => validateSnapshot(invalid));
});

test('impossible calendar dates cannot pass the snapshot contract by normalising into another month', () => {
  const invalidAvailability = snapshot(); invalidAvailability.offers[0].available_from = '2026-02-30';
  assert.throws(() => validateSnapshot(invalidAvailability));
  assert.throws(() => validateSnapshot({ ...snapshot(), observed_at: '2026-02-30T12:00:00Z' }));
});
