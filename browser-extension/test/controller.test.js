import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createController, MONITOR_ALARM, DEADLINE_ALARM } from '../src/controller.js';
import { initialState } from '../src/engine.js';
import { CERGY_URL } from '../src/model.js';

const STORE = 'cergyLocalV1', BASE = Date.parse('2026-10-27T00:00:00.000Z');
const copy = value => value === undefined ? undefined : structuredClone(value);
const listing = (overrides = {}) => ({
  offer_id: 'TEST-OFFER', url: 'https://trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-OFFER',
  title: 'TEST — Résidence locale fictive', city: 'Cergy', housing_type: 'chambre', rent_eur: 350, ...overrides
});
function observation(now, { offers = [listing()], audience = 'PUBLIC_ONLY', status = 'COMPLETE', confirmed = true, reason } = {}) {
  return { schema_version: 1, source_url: CERGY_URL, zone: 'Cergy', audience,
    audience_confirmed: confirmed, observed_at: new Date(now).toISOString(), status,
    pagination_complete: status === 'COMPLETE', total_count: status === 'UNKNOWN' ? null : offers.length,
    ...(reason ? { reason } : {}), offers: offers.map(offer => ({ ...offer, audience })) };
}
function harness({ publicMode = true, seed } = {}) {
  let now = BASE, sequence = 0, nextTab = 100, notificationsFail = false;
  const stored = seed ? { [STORE]: copy(seed) } : {};
  if (!seed && publicMode) { stored[STORE] = initialState(); stored[STORE].config.require_authenticated = false; }
  const tabMap = new Map([[8, { id: 8, url: 'https://example.invalid/TEST-unrelated-tab' }]]);
  const alarmMap = new Map();
  const calls = { created: [], reloaded: [], updated: [], sent: [], notices: [], noticeAttempts: [] };
  const api = {
    runtime: { id: 'TEST-EXTENSION', getURL: name => `chrome-extension://TEST-EXTENSION/${name}` },
    storage: { local: {
      get: async key => ({ [key]: copy(stored[key]) }),
      set: async value => { for (const [key, entry] of Object.entries(value)) stored[key] = copy(entry); }
    } },
    alarms: {
      get: async name => copy(alarmMap.get(name)),
      create: async (name, options) => { alarmMap.set(name, copy(options)); },
      clear: async name => alarmMap.delete(name)
    },
    tabs: {
      // Deliberately no query API: enumerating unrelated tabs is not part of this controller.
      get: async id => { if (!tabMap.has(id)) throw new Error('TEST tab missing'); return copy(tabMap.get(id)); },
      create: async options => { const tab = { id: nextTab++, ...copy(options) }; calls.created.push(copy(options)); tabMap.set(tab.id, tab); return copy(tab); },
      reload: async id => { assert.equal(typeof id, 'number', 'reload must always name a dedicated tab'); calls.reloaded.push(id); },
      update: async (id, options) => { calls.updated.push({ id, options: copy(options) }); return { ...copy(tabMap.get(id)), ...options }; },
      sendMessage: async (id, message) => { calls.sent.push({ id, message: copy(message) }); }
    },
    notifications: { create: async (id, options) => {
      const attempt = { id, options: copy(options) }; calls.noticeAttempts.push(attempt);
      if (notificationsFail) throw new Error('TEST notification API failure');
      calls.notices.push(attempt); return id;
    } }
  };
  const options = { clock: () => now, uuid: () => `TEST-cycle-${++sequence}` };
  const controller = createController(api, options);
  const ui = { id: api.runtime.id, url: api.runtime.getURL('popup/index.html') };
  const state = () => copy(stored[STORE]);
  const content = () => ({ id: api.runtime.id, tab: copy(tabMap.get(state().tabId)) });
  const result = (snapshot = observation(now), sender = content(), cycle = state().inFlight?.id) =>
    controller.message({ type: 'RESULTS', cycle_id: cycle, snapshot }, sender);
  return { api, controller, ui, calls, tabMap, alarmMap, state, result, content,
    now: () => now, advance: milliseconds => { now += milliseconds; },
    failNotifications: value => { notificationsFail = Boolean(value); },
    restarted: () => createController(api, options) };
}

test('a failed result notification remains pending in persistent outbox instead of being marked delivered', async () => {
  const h = harness(); h.failNotifications(true);
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  assert.equal(h.state().outbox.length, 1);
  assert.equal(h.state().outbox[0].kind, 'RESULTS');
  assert.ok(h.state().outbox[0].attempts >= 1);
  assert.equal(h.state().alertsBlocked, true);
  assert.ok(h.state().notificationError);
  assert.equal(h.state().delivered.length, 0);
  assert.equal(h.state().authNoticeActive, false);
  assert.equal(h.calls.notices.length, 0);
  assert.equal(h.state().lastReliable.PUBLIC_ONLY.offers.length, 1, 'notification failure must not discard the observation');
});

test('a restarted worker retries a pending alert once, persists acknowledgement, then never replays it', async () => {
  const h = harness(); h.failNotifications(true);
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  const pending = copy(h.state().outbox[0]);
  const sourceCallsBefore = { created: h.calls.created.length, reloaded: h.calls.reloaded.length };
  h.failNotifications(false);
  await h.restarted().startup();
  assert.equal(h.state().outbox.length, 0);
  assert.equal(h.state().alertsBlocked, false);
  assert.equal(h.calls.notices.length, 1);
  assert.equal(h.state().delivered.length, 1);
  assert.equal(h.state().delivered[0].id, pending.id);
  assert.equal(h.state().delivered[0].key, pending.key);
  assert.ok(Date.parse(h.state().delivered[0].delivered_at));
  assert.ok(h.calls.noticeAttempts.every(attempt => attempt.id === pending.id), 'retries must reuse the persisted native notification id');
  await h.restarted().startup();
  await h.controller.message({ type: 'RETRY_ALERTS' }, h.ui);
  assert.equal(h.calls.notices.length, 1, 'persisted successful acknowledgement must survive a second worker instance');
  assert.equal(h.calls.created.length, sourceCallsBefore.created);
  assert.equal(h.calls.reloaded.length, sourceCallsBefore.reloaded);
});

test('failed auth alerts stay unique across repeated Start and become active only after delivery succeeds', async () => {
  const h = harness({ publicMode: false }); h.failNotifications(true);
  await h.controller.message({ type: 'START' }, h.ui);
  h.tabMap.get(100).url = 'https://example.invalid/TEST-SSO-sign-in';
  await h.controller.tabUpdated(100, { status: 'complete' }, copy(h.tabMap.get(100)));
  await h.controller.message({ type: 'START' }, h.ui);
  await h.controller.message({ type: 'START' }, h.ui);
  assert.equal(h.state().authPaused, true);
  assert.equal(h.state().authNoticeActive, false, 'a rejected OS notification has not been delivered');
  assert.equal(h.state().alertsBlocked, true);
  assert.equal(h.state().outbox.length, 1);
  assert.equal(h.state().outbox[0].kind, 'AUTH');
  assert.equal(h.state().delivered.length, 0);
  assert.equal(h.calls.notices.length, 0);
  h.failNotifications(false);
  await h.controller.message({ type: 'RETRY_ALERTS' }, h.ui);
  assert.equal(h.state().outbox.length, 0);
  assert.equal(h.state().delivered.length, 1);
  assert.equal(h.state().delivered[0].kind, 'AUTH');
  assert.equal(h.state().authNoticeActive, true);
  assert.equal(h.state().authPaused, true, 'retrying the alert must not resume navigation or the login flow');
  assert.equal(h.calls.notices.length, 1);
  await h.restarted().startup();
  assert.equal(h.calls.notices.length, 1);
  assert.deepEqual(h.calls.reloaded, []);
  assert.equal(h.calls.created.length, 1);
});

test('TEST_ALERT is clearly labelled TEST and sends no portal read, navigation or real housing result', async () => {
  const h = harness({ publicMode: false });
  await h.controller.message({ type: 'TEST_ALERT' }, h.ui);
  assert.equal(h.calls.notices.length, 1);
  const shown = h.calls.notices[0].options;
  assert.match(shown.title + ' ' + shown.message, /TEST/);
  assert.match(shown.message, /aucun logement|aucune offre/i);
  assert.equal(h.state().delivered[0].kind, 'TEST');
  assert.equal(h.state().outbox.length, 0);
  assert.equal(h.state().enabled, false);
  assert.deepEqual(h.state().records, {});
  assert.deepEqual(h.state().history, []);
  assert.deepEqual(h.calls.created, []);
  assert.deepEqual(h.calls.reloaded, []);
  assert.deepEqual(h.calls.sent, []);
});

test('startup is local and Start owns one background search tab without inspecting unrelated tabs', async () => {
  const h = harness();
  await h.controller.startup();
  assert.equal(h.calls.created.length, 0);
  assert.equal(h.calls.reloaded.length, 0);
  await h.controller.message({ type: 'START' }, h.ui);
  assert.deepEqual(h.calls.created, [{ url: CERGY_URL, active: false }]);
  assert.equal(h.alarmMap.get(MONITOR_ALARM).periodInMinutes, 5);
  assert.equal(h.calls.sent.length, 0, 'creation must wait for the new document before requesting a DOM read');
  await h.controller.message({ type: 'CONTENT_READY' }, h.content());
  assert.equal(h.calls.sent[0].id, 100);
  assert.equal(h.tabMap.get(8).url, 'https://example.invalid/TEST-unrelated-tab');
});

test('a reload cannot read the old document before the next content-ready or complete event', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  assert.equal(h.calls.sent.length, 0);
  await h.controller.message({ type: 'CONTENT_READY' }, h.content());
  assert.equal(h.calls.sent.length, 1);
  await h.result();
  h.advance(300000);
  await h.controller.alarm(MONITOR_ALARM);
  assert.deepEqual(h.calls.reloaded, [100]);
  assert.equal(h.calls.sent.length, 1, 'a newly allocated cycle must not request stale pre-reload HTML');
  await h.controller.tabUpdated(8, { status: 'complete' }, { id: 8, url: CERGY_URL });
  assert.equal(h.calls.sent.length, 1, 'unrelated completion must not authorise the read');
  await h.controller.tabUpdated(100, { status: 'complete' }, copy(h.tabMap.get(100)));
  assert.equal(h.calls.sent.length, 2);
  assert.equal(h.calls.sent[1].message.cycle_id, h.state().inFlight.id);
});

test('overlapping manual and scheduled checks share the persisted cycle rather than reloading twice', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  const cycle = h.state().inFlight.id;
  await Promise.all([
    h.controller.message({ type: 'CHECK_NOW' }, h.ui), h.controller.alarm(MONITOR_ALARM)
  ]);
  assert.equal(h.state().inFlight.id, cycle);
  assert.equal(h.calls.created.length, 1);
  assert.equal(h.calls.reloaded.length, 0);
});

test('native alerts are deduplicated and a later actual price change produces one new alert', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  const cycle = h.state().inFlight.id, sender = h.content();
  await h.result();
  assert.equal(h.calls.notices.length, 1);
  assert.equal((await h.result(observation(h.now()), sender, cycle)).ignored, true);
  assert.equal(h.calls.notices.length, 1);
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM); await h.result();
  assert.deepEqual(h.calls.reloaded, [100]);
  assert.equal(h.calls.notices.length, 1, 'same listing must not alert every cycle');
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM);
  await h.result(observation(h.now(), { offers: [listing({ rent_eur: 390 })] }));
  assert.equal(h.calls.notices.length, 2);
  assert.match(h.calls.notices[1].options.title, /prix modifié/);
});

test('a new worker instance keeps notification deduplication across a normal restart', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  const restarted = h.restarted();
  await restarted.startup();
  h.advance(300000); await restarted.alarm(MONITOR_ALARM);
  await restarted.message({ type: 'RESULTS', cycle_id: h.state().inFlight.id,
    snapshot: observation(h.now()) }, h.content());
  assert.equal(h.calls.notices.length, 1, 'restart must not make the same listing appear new again');
  assert.deepEqual(h.calls.reloaded, [100]);
});

test('default monitoring pauses for a public result instead of treating it as the connected DSE result', async () => {
  const h = harness({ publicMode: false });
  await h.controller.message({ type: 'START' }, h.ui);
  await h.result();
  assert.equal(h.state().authPaused, true);
  assert.equal(h.state().status, 'AUTH_REQUIRED');
  assert.equal(h.alarmMap.has(MONITOR_ALARM), false);
  assert.equal(h.calls.notices.length, 1);
  assert.equal(h.state().delivered[0].kind, 'AUTH');
  await h.controller.alarm(MONITOR_ALARM);
  assert.equal(h.calls.reloaded.length, 0);
});

test('confirmed authenticated results satisfy the normal default monitoring mode', async () => {
  const h = harness({ publicMode: false });
  await h.controller.message({ type: 'START' }, h.ui);
  await h.result(observation(h.now(), { audience: 'AUTHENTICATED' }));
  assert.equal(h.state().authPaused, false);
  assert.equal(h.state().status, 'COMPLETE');
  assert.equal(h.state().delivered[0].kind, 'RESULTS');
});

test('losing the DSE session cannot erase previously authenticated offers using a public zero', async () => {
  const h = harness({ publicMode: false });
  await h.controller.message({ type: 'START' }, h.ui);
  await h.result(observation(h.now(), { audience: 'AUTHENTICATED' }));
  const authenticatedRows = Object.entries(h.state().records).filter(([, row]) => row.offer.audience === 'AUTHENTICATED');
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM);
  await h.result(observation(h.now(), { offers: [], audience: 'PUBLIC_ONLY' }));
  assert.equal(h.state().authPaused, true);
  for (const [key] of authenticatedRows) assert.equal(h.state().records[key].present, true);
  assert.equal(h.state().lastReliable.AUTHENTICATED.offers.length, 1);
  assert.equal(h.state().delivered.filter(notice => notice.kind === 'RESULTS').length, 1);
  assert.equal(h.state().delivered.filter(notice => notice.kind === 'AUTH').length, 1);
});

test('a dedicated tab redirected to SSO is never reloaded or navigated automatically', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  h.tabMap.get(100).url = 'https://example.invalid/TEST-SSO-sign-in';
  await h.controller.tabUpdated(100, { status: 'complete' }, copy(h.tabMap.get(100)));
  assert.equal(h.state().authPaused, true);
  assert.equal(h.calls.notices.length, 1);
  h.advance(600000);
  await h.controller.alarm(MONITOR_ALARM);
  await h.controller.message({ type: 'CHECK_NOW' }, h.ui);
  await h.controller.message({ type: 'START' }, h.ui);
  assert.equal(h.calls.created.length, 1);
  assert.deepEqual(h.calls.reloaded, []);
  assert.deepEqual(h.calls.updated, []);
  assert.equal(h.calls.notices.length, 1, 'the same unresolved login condition should only alert once');
});

test('a DSE gate returned by the monitored origin also pauses with one manual-auth alert', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  await h.result(observation(h.now(), { status: 'UNKNOWN', offers: [], confirmed: false, reason: 'AUTH_REQUIRED' }));
  assert.equal(h.state().authPaused, true);
  assert.equal(h.state().status, 'AUTH_REQUIRED');
  assert.equal(h.state().delivered[0].kind, 'AUTH');
  assert.equal(h.alarmMap.has(DEADLINE_ALARM), false);
});

test('worker restart preserves an in-flight lease and the 300-second cooldown', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  const cycle = h.state().inFlight.id;
  const restarted = h.restarted();
  await restarted.startup();
  await restarted.message({ type: 'CHECK_NOW' }, h.ui);
  assert.equal(h.state().inFlight.id, cycle);
  assert.equal(h.calls.created.length, 1);
  assert.equal(h.calls.reloaded.length, 0);
  h.advance(46000); await restarted.startup();
  assert.equal(h.state().inFlight, null);
  assert.equal(h.state().status, 'UNKNOWN');
  await restarted.message({ type: 'CHECK_NOW' }, h.ui);
  assert.equal(h.calls.reloaded.length, 0, 'a lost lease must not reset the cooldown');
  h.advance(254000); await restarted.alarm(MONITOR_ALARM);
  assert.deepEqual(h.calls.reloaded, [100]);
});

test('a timed-out read preserves the last reliable records and never synthesises an empty result', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  const baseline = h.state().lastReliable;
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM);
  h.advance(46000); await h.controller.alarm(DEADLINE_ALARM);
  assert.equal(h.state().status, 'UNKNOWN');
  assert.deepEqual(h.state().lastReliable, baseline);
  assert.equal(Object.values(h.state().records)[0].present, true);
  assert.equal(h.calls.notices.length, 1);
});

test('unknown pages, wrong cycles and messages from another tab cannot overwrite a reliable result', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui);
  assert.equal((await h.result(observation(h.now()), { id: h.api.runtime.id, tab: { id: 8, url: CERGY_URL } })).ignored, true);
  assert.equal((await h.result(observation(h.now()), h.content(), 'TEST-wrong-cycle')).ignored, true);
  await h.result();
  const baseline = h.state().lastReliable;
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM);
  await h.result(observation(h.now(), { status: 'UNKNOWN', offers: [], reason: 'SOURCE_UNAVAILABLE' }));
  assert.deepEqual(h.state().lastReliable, baseline);
  assert.equal(Object.values(h.state().records)[0].present, true);
});

test('Stop keeps history while rejecting a late result from the cancelled cycle', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  h.advance(300000); await h.controller.alarm(MONITOR_ALARM);
  const cycle = h.state().inFlight.id, sender = h.content(), before = h.state();
  await h.controller.message({ type: 'STOP' }, h.ui);
  assert.equal((await h.result(observation(h.now(), { offers: [] }), sender, cycle)).ignored, true);
  assert.equal(h.state().enabled, false);
  assert.deepEqual(h.state().records, before.records);
  assert.deepEqual(h.state().history, before.history);
  assert.equal(h.alarmMap.has(MONITOR_ALARM), false);
});

test('import is local and silent, and an official offer link never becomes the monitored search tab', async () => {
  const h = harness();
  await h.controller.message({ type: 'IMPORT_SNAPSHOT', snapshot: observation(h.now()) }, h.ui);
  assert.equal(h.calls.created.length, 0);
  assert.equal(h.calls.notices.length, 0);
  assert.equal(h.state().enabled, false);
  await assert.rejects(h.controller.message({ type: 'OPEN_OFFER', url: 'https://example.invalid/TEST' }, h.ui));
  await h.controller.message({ type: 'START' }, h.ui);
  const searchTab = h.state().tabId;
  await h.controller.message({ type: 'OPEN_OFFER', url: listing().url }, h.ui);
  assert.equal(h.state().tabId, searchTab);
  assert.equal(h.calls.created[1].url, listing().url);
  assert.equal(h.calls.created[1].active, true);
});

test('configuration and browser lifecycle operations never inspect or reload unrelated tabs', async () => {
  const h = harness();
  await h.controller.message({ type: 'START' }, h.ui); await h.result();
  const before = h.state();
  await h.controller.message({ type: 'SAVE_CONFIG', config: { require_authenticated: false, interval_seconds: 600 } }, h.ui);
  assert.equal(h.alarmMap.get(MONITOR_ALARM).periodInMinutes, 10);
  await h.controller.browserStartup();
  assert.equal(h.state().tabId, null);
  assert.equal(h.state().inFlight, null);
  assert.deepEqual(h.state().records, before.records);
  assert.equal(h.calls.created.length, 1);
  assert.deepEqual(h.calls.reloaded, []);
  await assert.rejects(h.controller.message({ type: 'STOP' }, { id: 'OTHER-TEST', url: h.ui.url }));
  assert.equal(h.tabMap.get(8).url, 'https://example.invalid/TEST-unrelated-tab');
});

test('unrecognised local storage versions are rejected without resetting the saved state', async () => {
  const seed = initialState(); seed.version = 2;
  const h = harness({ seed });
  await assert.rejects(h.controller.startup(), /incompatible/);
  assert.equal(h.state().version, 2);
  assert.equal(h.calls.created.length, 0);
  assert.equal(h.calls.notices.length, 0);
});

function badgeSender(h, id = 41, overrides = {}) {
  h.tabMap.set(id, {id,url:CERGY_URL});
  return {id:h.api.runtime.id,tab:copy(h.tabMap.get(id)),frameId:0,
    origin:new URL(CERGY_URL).origin,url:CERGY_URL,documentId:`TEST-badge-document-${id}`,...overrides};
}
const badge = (action, extras = {}) => ({type:'CONTENT_CONTROL',action,...extras});

test('badge status returns narrow metadata without exposing listings, configuration or archived snapshots', async () => {
  const h = harness();
  await h.controller.message({type:'IMPORT_SNAPSHOT',snapshot:observation(h.now())},h.ui);
  const before=h.state(), answer=await h.controller.message(badge('GET_STATUS'),badgeSender(h));
  assert.deepEqual(Object.keys(answer),['view']);
  assert.deepEqual(Object.keys(answer.view).sort(),[
    'owned','enabled','authPaused','status','audience','audience_confirmed','checked_at',
    'alertsBlocked','pendingAlerts','notificationError'
  ].sort());
  assert.equal(answer.view.owned,false);
  assert.equal(JSON.stringify(answer).includes('TEST-OFFER'),false);
  assert.equal(JSON.stringify(answer).includes('records'),false);
  assert.deepEqual(h.state(),before,'reading badge status must not modify local history');
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.deepEqual(h.calls.sent,[]);assert.deepEqual(h.calls.notices,[]);
});

test('badge controls reject foreign origins, extension ids, child frames and mismatching page locations', async () => {
  const h=harness(), valid=badgeSender(h), before=h.state();
  const invalid=[
    {...valid,id:'TEST-OTHER-EXTENSION'}, {...valid,frameId:1}, {...valid,tab:undefined},
    {...valid,tab:{id:'41',url:CERGY_URL}},
    {...valid,origin:'https://example.invalid'},
    {...valid,url:'https://example.invalid/TEST'},
    {...valid,tab:{id:41,url:'https://example.invalid/TEST'}},
    {...valid,url:CERGY_URL.replace('locationName=Cergy','locationName=Paris')},
    {...valid,url:CERGY_URL.replace('/tools/47/search','/tools/47/accommodations/TEST')}
  ];
  for(const sender of invalid)
    await assert.rejects(h.controller.message(badge('START'),sender),/non autorisée/);
  for(const message of [badge('IMPORT_SNAPSHOT'),badge('START',{config:{require_authenticated:false}}),
    badge('GET_STATUS',{snapshot:observation(h.now())}),badge(undefined)])
    await assert.rejects(h.controller.message(message,valid),/non autorisée/);
  assert.deepEqual(h.state(),before);
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.deepEqual(h.calls.sent,[]);assert.deepEqual(h.calls.notices,[]);
});

test('badge commands fail closed when browser source metadata is absent or invalid', async () => {
  const h=harness(),valid=badgeSender(h),before=h.state();
  const invalid=[
    {...valid,frameId:undefined},{...valid,frameId:null},
    {...valid,url:undefined},{...valid,url:null},{...valid,url:''},
    {...valid,origin:undefined},{...valid,origin:null},{...valid,origin:''},
    {...valid,documentId:undefined},{...valid,documentId:null},
    {...valid,documentId:''},{...valid,documentId:42}
  ];
  for(const sender of invalid) {
    for(const action of ['GET_STATUS','START','STOP','TEST'])
      await assert.rejects(h.controller.message(badge(action),sender),/non autorisée/);
  }
  assert.deepEqual(h.state(),before);
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.deepEqual(h.calls.sent,[]);assert.deepEqual(h.calls.notices,[]);
});

test('badge Start adopts only the explicit current Cergy document without reload or creation', async () => {
  const h=harness({publicMode:false}), sender=badgeSender(h);
  const answer=await h.controller.message(badge('START'),sender);
  assert.equal(answer.view.owned,true);assert.equal(answer.view.enabled,true);
  assert.equal(h.state().tabId,41);assert.equal(h.state().status,'CHECKING');
  assert.equal(h.state().config.require_authenticated,true,'badge cannot downgrade the authenticated requirement');
  assert.equal(h.state().inFlight.documentId,sender.documentId);
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.deepEqual(h.calls.updated,[]);
  assert.deepEqual(h.calls.sent,[{id:41,message:{type:'READ_RESULTS',cycle_id:h.state().inFlight.id}}]);
  assert.equal(h.alarmMap.get(MONITOR_ALARM).periodInMinutes,5);
  assert.equal(h.tabMap.get(8).url,'https://example.invalid/TEST-unrelated-tab');
  const wrongDocument={...sender,documentId:'TEST-stale-document'};
  assert.equal((await h.result(observation(h.now(),{audience:'AUTHENTICATED'}),wrongDocument)).ignored,true);
  await h.result(observation(h.now(),{audience:'AUTHENTICATED'}),sender);
  assert.equal(h.state().status,'COMPLETE');assert.equal(h.state().authPaused,false);
});

test('badge Pause cannot stop another Cergy tab, and own Pause cancels a read without dropping history', async () => {
  const h=harness(), owner=badgeSender(h), other=badgeSender(h,42);
  await h.controller.message(badge('START'),owner);
  await h.result(observation(h.now()),owner);
  h.advance(300000);await h.controller.alarm(MONITOR_ALARM);
  const before=h.state(), cycle=before.inFlight.id;
  await assert.rejects(h.controller.message(badge('STOP'),other),/pas celui suivi/);
  assert.deepEqual(h.state(),before);
  const answer=await h.controller.message(badge('STOP'),owner);
  assert.equal(answer.view.enabled,false);assert.equal(h.state().status,'STOPPED');
  assert.equal(h.state().inFlight,null);
  assert.deepEqual(h.state().records,before.records);assert.deepEqual(h.state().history,before.history);
  assert.equal(h.alarmMap.has(MONITOR_ALARM),false);assert.equal(h.alarmMap.has(DEADLINE_ALARM),false);
  assert.deepEqual(h.calls.sent.at(-1),{id:41,message:{type:'CANCEL_READ'}});
  assert.equal((await h.result(observation(h.now(),{offers:[]}),owner,cycle)).ignored,true);
});

test('switching the badge-owned tab cancels its old read but preserves the persistent cooldown', async () => {
  const h=harness(), first=badgeSender(h), next=badgeSender(h,42);
  await h.controller.message(badge('START'),first);
  const cycle=h.state().inFlight.id, nextAllowedAt=h.state().nextAllowedAt;
  await h.controller.message(badge('START'),next);
  assert.equal(h.state().tabId,42);assert.equal(h.state().inFlight,null);
  assert.equal(h.state().nextAllowedAt,nextAllowedAt);
  assert.deepEqual(h.calls.sent.at(-1),{id:41,message:{type:'CANCEL_READ'}});
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.equal((await h.result(observation(h.now()),first,cycle)).ignored,true);
  h.advance(300000);await h.controller.alarm(MONITOR_ALARM);
  assert.deepEqual(h.calls.reloaded,[42]);
  assert.equal(h.tabMap.get(8).url,'https://example.invalid/TEST-unrelated-tab');
});

test('badge Test makes one clearly labelled local notification without source requests or navigation', async () => {
  const h=harness({publicMode:false});
  const answer=await h.controller.message(badge('TEST'),badgeSender(h));
  assert.equal(h.calls.notices.length,1);
  const shown=h.calls.notices[0].options;
  assert.match(shown.title,/TEST/);assert.match(shown.message,/aucun logement|aucune offre/i);
  assert.equal(answer.view.pendingAlerts,0);assert.equal(answer.view.alertsBlocked,false);
  assert.equal(h.state().enabled,false);assert.equal(h.state().tabId,null);
  assert.deepEqual(h.state().records,{});assert.deepEqual(h.state().history,[]);
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
  assert.deepEqual(h.calls.updated,[]);assert.deepEqual(h.calls.sent,[]);
});

test('failed badge Test remains visible as a pending local alert and never claims delivery', async () => {
  const h=harness();h.failNotifications(true);
  const sender=badgeSender(h),answer=await h.controller.message(badge('TEST'),sender);
  assert.equal(answer.view.alertsBlocked,true);assert.equal(answer.view.pendingAlerts,1);
  assert.equal(h.state().outbox[0].kind,'TEST');assert.equal(h.state().delivered.length,0);
  const status=await h.controller.message(badge('GET_STATUS'),sender);
  assert.equal(status.view.pendingAlerts,1);assert.equal(status.view.alertsBlocked,true);
  assert.deepEqual(h.calls.created,[]);assert.deepEqual(h.calls.reloaded,[]);
});

test('badge Start preserves the authenticated-only gate when a public zero is read', async () => {
  const h=harness({publicMode:false}),sender=badgeSender(h);
  await h.controller.message(badge('START'),sender);
  await h.result(observation(h.now(),{offers:[],audience:'PUBLIC_ONLY'}),sender);
  const answer=await h.controller.message(badge('GET_STATUS'),sender);
  assert.equal(answer.view.status,'AUTH_REQUIRED');assert.equal(answer.view.authPaused,true);
  assert.equal(answer.view.audience,'PUBLIC_ONLY');assert.equal(h.alarmMap.has(MONITOR_ALARM),false);
  assert.equal(h.state().delivered[0].kind,'AUTH');assert.deepEqual(h.calls.reloaded,[]);
});

test('an explicit source outage remains UNKNOWN without falsely pausing the authenticated session or erasing prior offers', async () => {
  const h=harness({publicMode:false});
  await h.controller.message({type:'START'},h.ui);
  await h.result(observation(h.now(),{audience:'AUTHENTICATED'}));
  const reliable=h.state().lastReliable, records=h.state().records, noticeCount=h.calls.notices.length;
  h.advance(300000);await h.controller.alarm(MONITOR_ALARM);
  const nextAllowedAt=h.state().nextAllowedAt;
  assert.deepEqual(h.calls.reloaded,[100]);
  await h.result(observation(h.now(),{
    offers:[],status:'UNKNOWN',audience:'PUBLIC_ONLY',confirmed:false,reason:'SOURCE_UNAVAILABLE'
  }));
  assert.equal(h.state().status,'UNKNOWN');assert.equal(h.state().authPaused,false);
  assert.equal(h.state().enabled,true);assert.equal(h.state().inFlight,null);
  assert.deepEqual(h.state().lastReliable,reliable);assert.deepEqual(h.state().records,records);
  assert.equal(h.state().lastSnapshot.reason,'SOURCE_UNAVAILABLE');
  assert.equal(h.state().nextAllowedAt,nextAllowedAt);
  assert.equal(h.state().config.require_authenticated,true);
  assert.equal(h.state().outbox.some(item=>item.kind==='AUTH'),false);
  assert.equal(h.state().delivered.some(item=>item.kind==='AUTH'),false);
  assert.equal(h.calls.notices.length,noticeCount,'an outage must not generate a misleading lost-session alert');
  assert.equal(h.alarmMap.get(MONITOR_ALARM).periodInMinutes,5);
  assert.equal(h.alarmMap.has(DEADLINE_ALARM),false);
  await h.controller.alarm(MONITOR_ALARM);
  assert.deepEqual(h.calls.reloaded,[100],'outage retry must preserve the 300-second cooldown');
  h.advance(300000);await h.controller.alarm(MONITOR_ALARM);
  assert.deepEqual(h.calls.reloaded,[100,100]);
  assert.equal(h.state().status,'CHECKING');assert.equal(h.state().authPaused,false);
});
