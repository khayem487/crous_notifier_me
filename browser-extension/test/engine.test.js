import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialState, applySnapshot } from '../src/engine.js';
import { CERGY_URL, offerKey } from '../src/model.js';

// All offers, identities and times here are TEST data. No portal or profile is read.
const BASE_TIME = Date.parse('2026-10-27T00:00:00.000Z');
const iso = offset => new Date(BASE_TIME + offset).toISOString();
const offer = (id = 'TEST-001', overrides = {}) => ({
  offer_id: id, url: `https://trouverunlogement.lescrous.fr/tools/47/accommodations/${id}`,
  title: `TEST — Résidence ${id}`, city: null, housing_type: null, rent_eur: 350,
  ...overrides
});
const snapshot = ({ offers = [offer()], audience = 'PUBLIC_ONLY', offset = 0, status = 'COMPLETE',
  confirmed = true, reason } = {}) => ({
  schema_version: 1, source_url: CERGY_URL, zone: 'Cergy', audience,
  audience_confirmed: confirmed, observed_at: iso(offset), status,
  pagination_complete: status === 'COMPLETE', total_count: status === 'UNKNOWN' ? null : offers.length,
  ...(reason ? { reason } : {}), offers: offers.map(value => ({ ...value, audience }))
});
const apply = (state, observation, extra = {}) => applySnapshot(state, observation, { now: BASE_TIME + 3600000, ...extra });

test('a newly observed unknown housing type is a candidate, not a confirmed room; duplicates do not re-alert', () => {
  const first = apply(initialState(), snapshot());
  assert.equal(first.notices.length, 1);
  assert.equal(first.notices[0].confirmed, false);
  assert.equal(first.notices[0].kind, 'NEW');
  const repeated = apply(first.state, snapshot({ offset: 300000 }));
  assert.equal(repeated.notices.length, 0);
  assert.equal(repeated.state.events.filter(event => event.kind === 'NEW').length, 1);
});

test('an explicit room and Cergy address confirm the candidate, while Individuel cannot do so', () => {
  const confirmed = apply(initialState(), snapshot({ offers: [offer('TEST-ROOM', { city: 'Cergy', housing_type: 'chambre' })] }));
  assert.equal(confirmed.notices[0].confirmed, true);
  const occupationOnly = apply(initialState(), snapshot({ offers: [offer('TEST-OCCUPATION', { city: 'Cergy', housing_type: null })] }));
  assert.equal(occupationOnly.notices[0].confirmed, false);
  const studio = apply(initialState(), snapshot({ offers: [offer('TEST-STUDIO', { city: 'Cergy', housing_type: 'studio' })] }));
  assert.equal(studio.notices.length, 0);
  assert.equal(Object.values(studio.state.records).length, 1, 'non-room information stays available for manual review');
});

test('a changed rent range is one price event and the same range never re-alerts', () => {
  const ranged = offer('TEST-RANGE', { rent_eur: null, rent_min_eur: 350, rent_max_eur: 420 });
  const first = apply(initialState(), snapshot({ offers: [ranged] }));
  const changed = apply(first.state, snapshot({ offers: [{ ...ranged, rent_max_eur: 450 }], offset: 300000 }));
  assert.equal(changed.notices.length, 1);
  assert.equal(changed.notices[0].kind, 'PRICE_CHANGED');
  const same = apply(changed.state, snapshot({ offers: [{ ...ranged, rent_max_eur: 450 }], offset: 600000 }));
  assert.equal(same.notices.length, 0);
});

test('a candidate becoming an explicit Cergy room produces one MATCH_CONFIRMED alert', () => {
  const first = apply(initialState(), snapshot({ offers: [offer('TEST-ROOM-PENDING')] }));
  assert.equal(first.notices[0].confirmed, false);
  const confirmed = apply(first.state, snapshot({ offset: 300000,
    offers: [offer('TEST-ROOM-PENDING', { city: 'Cergy', housing_type: 'chambre' })] }));
  assert.equal(confirmed.notices.length, 1);
  assert.equal(confirmed.notices[0].kind, 'MATCH_CONFIRMED');
  assert.equal(confirmed.notices[0].confirmed, true);
  const repeated = apply(confirmed.state, snapshot({ offset: 600000,
    offers: [offer('TEST-ROOM-PENDING', { city: 'Cergy', housing_type: 'chambre' })] }));
  assert.equal(repeated.notices.length, 0);
});

test('meaningful listing details change independently of prices and remain deduplicated', () => {
  const originalOffer = offer('TEST-DETAILS', { city: 'Cergy', housing_type: 'chambre',
    surface_m2: 12, available_from: '2026-11-01', residence: 'TEST — Résidence initiale' });
  const changes = [
    { title: 'TEST — Titre corrigé' }, { residence: 'TEST — Autre résidence' },
    { surface_m2: 14 }, { available_from: '2026-11-15' },
    { housing_type: 'chambre individuelle' }, { city: 'Paris' }
  ];
  for (const change of changes) {
    const first = apply(initialState(), snapshot({ offers: [originalOffer] }));
    const updatedOffer = { ...originalOffer, ...change };
    const changed = apply(first.state, snapshot({ offset: 300000, offers: [updatedOffer] }));
    assert.equal(changed.state.events[0].kind, 'DETAILS_CHANGED', JSON.stringify(change));
    if (!change.city) assert.equal(changed.notices[0]?.kind, 'DETAILS_CHANGED', JSON.stringify(change));
    else assert.equal(changed.notices.length, 0, 'an explicitly different city is not a matching alert');
    const repeated = apply(changed.state, snapshot({ offset: 600000, offers: [updatedOffer] }));
    assert.equal(repeated.notices.length, 0, 'a changed observed_at alone is not a new detail change');
    assert.equal(repeated.state.events.length, changed.state.events.length);
  }
});

test('a later observed_at alone never creates a details alert', () => {
  const first = apply(initialState(), snapshot());
  const later = apply(first.state, snapshot({ offset: 300000 }));
  assert.equal(later.notices.length, 0);
  assert.deepEqual(later.state.events, first.state.events);
});

test('a different payload at the same timestamp cannot overwrite or remove the saved observation', () => {
  const first = apply(initialState(), snapshot());
  for (const conflict of [snapshot({ offers: [] }), snapshot({ offers: [offer('TEST-001', { rent_eur: 999 })] })]) {
    const repeatedTimestamp = apply(first.state, conflict);
    assert.equal(repeatedTimestamp.notices.length, 0);
    assert.deepEqual(repeatedTimestamp.state.records, first.state.records);
    assert.deepEqual(repeatedTimestamp.state.lastReliable, first.state.lastReliable);
  }
});

test('UNKNOWN and PARTIAL observations preserve the reliable baseline and do not imply removals', () => {
  const first = apply(initialState(), snapshot());
  const key = Object.keys(first.state.records)[0];
  const unknown = apply(first.state, snapshot({ status: 'UNKNOWN', offers: [], offset: 300000, reason: 'SOURCE_UNAVAILABLE' }));
  assert.equal(unknown.state.records[key].present, true);
  assert.deepEqual(unknown.state.lastReliable, first.state.lastReliable);
  assert.equal(unknown.notices.length, 0);
  const partial = apply(unknown.state, snapshot({ status: 'PARTIAL', offers: [], offset: 600000 }));
  assert.equal(partial.state.records[key].present, true);
  assert.deepEqual(partial.state.lastReliable, first.state.lastReliable);
  assert.equal(partial.state.events.some(event => event.kind === 'NO_LONGER_LISTED'), false);
});

test('a complete public zero removes only public listings, never authenticated listings', () => {
  const first = apply(initialState(), snapshot());
  const withAuth = apply(first.state, snapshot({ audience: 'AUTHENTICATED', offers: [offer('TEST-AUTH')], offset: 1000 }));
  const emptiedPublic = apply(withAuth.state, snapshot({ offers: [], offset: 300000 }));
  const publicRow = emptiedPublic.state.records[offerKey(offer(), 'PUBLIC_ONLY')];
  const authRow = emptiedPublic.state.records[offerKey(offer('TEST-AUTH'), 'AUTHENTICATED')];
  assert.equal(publicRow.present, false);
  assert.equal(authRow.present, true);
  assert.equal(emptiedPublic.state.lastReliable.AUTHENTICATED.offers.length, 1);
  assert.equal(emptiedPublic.state.events.filter(event => event.kind === 'NO_LONGER_LISTED').length, 1);
  const returned = apply(emptiedPublic.state, snapshot({ offset: 600000 }));
  assert.equal(returned.notices[0].kind, 'NEW', 'a genuine return after complete disappearance is a new observation');
});

test('imports update the local history without producing native alert candidates', () => {
  const imported = apply(initialState(), snapshot(), { origin: 'IMPORT' });
  assert.equal(imported.notices.length, 0);
  assert.equal(Object.values(imported.state.records)[0].origin, 'IMPORT');
  assert.equal(imported.state.history[0].origin, 'IMPORT');
  assert.equal(imported.state.events[0].origin, 'IMPORT');
});

test('an older import is archived without reverting a more recent rent or reliable count', () => {
  const current = apply(initialState(), snapshot({ offers: [offer('TEST-001', { rent_eur: 390 })], offset: 600000 }));
  const older = apply(current.state, snapshot({ offers: [], offset: 0 }), { origin: 'IMPORT' });
  assert.equal(Object.values(older.state.records)[0].present, true);
  assert.equal(Object.values(older.state.records)[0].offer.rent_eur, 390);
  assert.deepEqual(older.state.lastReliable, current.state.lastReliable);
  assert.equal(older.state.history.length, 2);
  assert.equal(older.notices.length, 0);
});

test('a foreign city or a price above an explicit budget does not trigger a matching candidate alert', () => {
  const state = initialState(); state.config.max_rent_eur = 400;
  const result = apply(state, snapshot({ offers: [
    offer('TEST-PARIS', { city: 'Paris', housing_type: 'chambre' }),
    offer('TEST-CERGY-EXPENSIVE', { city: 'Cergy', housing_type: 'chambre', rent_eur: 500 })
  ] }));
  assert.equal(result.notices.length, 0);
  assert.equal(Object.keys(result.state.records).length, 2);
});

test('an unconfirmed public audience cannot produce native notification candidates', () => {
  const result = apply(initialState(), snapshot({ status: 'PARTIAL', confirmed: false }));
  assert.equal(result.notices.length, 0);
  assert.equal(Object.keys(result.state.records).length, 1);
});

test('invalid complete snapshots are rejected without mutating saved history', () => {
  const original = apply(initialState(), snapshot()).state;
  const before = structuredClone(original);
  assert.throws(() => apply(original, { ...snapshot(), total_count: 9 }));
  assert.deepEqual(original, before);
});
