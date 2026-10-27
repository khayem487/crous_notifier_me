import {CERGY_URL, ORIGIN, validateConfig, validateSnapshot, officialUrl} from './model.js';
import {initialState, applySnapshot} from './engine.js';
export const MONITOR_ALARM = 'cergy-monitor-v1', DEADLINE_ALARM = 'cergy-deadline-v1';
const STORE_KEY = 'cergyLocalV1', LEASE_MS = 45000;
export function createController(api, {clock = () => Date.now(), uuid = () => crypto.randomUUID()} = {}) {
  let tail = Promise.resolve();
  const serial = action => { const next = tail.then(action); tail = next.catch(() => {}); return next; };
  async function load() {
    const saved = (await api.storage.local.get(STORE_KEY))[STORE_KEY];
    if (saved == null) return initialState();
    if (saved.version !== 1 || !saved.records || !Array.isArray(saved.history) || !Array.isArray(saved.events))
      throw new Error('Stockage local incompatible. Aucune donnée n’a été réinitialisée.');
    saved.config = validateConfig(saved.config);
    saved.outbox ||= []; saved.delivered ||= []; saved.alertsBlocked ??= false; saved.notificationError ??= null;
    return saved;
  }
  const save = state => api.storage.local.set({[STORE_KEY]: state});
  const uiSender = sender => sender?.id === api.runtime.id && !sender.tab &&
    String(sender.url || '').startsWith(api.runtime.getURL('popup/'));
  const searchUrl = value => { try { return officialUrl(value, 'search') === CERGY_URL; } catch { return false; } };
  const pageView = (state, tabId) => ({
    owned:state.tabId === tabId,enabled:state.enabled,authPaused:state.authPaused,status:state.status,
    audience:state.lastSnapshot?.audience || null,audience_confirmed:state.lastSnapshot?.audience_confirmed || false,
    checked_at:state.lastSnapshot?.observed_at || null,alertsBlocked:state.alertsBlocked,
    pendingAlerts:state.outbox.length,notificationError:state.notificationError || null
  });
  const pageSender = sender => sender?.id === api.runtime.id && Number.isInteger(sender.tab?.id) &&
    sender.frameId === 0 && sender.origin === ORIGIN &&
    typeof sender.documentId === 'string' && sender.documentId.length > 0 &&
    searchUrl(sender.tab.url) && searchUrl(sender.url);
  async function queueNotice(state, kind, title, message, key) {
    if (state.outbox.some(item => item.key === key) || state.delivered.some(item => item.key === key)) return;
    state.outbox.push({id: 'cergy-' + uuid(), key, kind, title, message: message.slice(0,300),
      created_at: new Date(clock()).toISOString(), attempts: 0, last_attempt_at: null});
    await save(state);
  }
  async function flushOutbox(state) {
    // Bounded retries on startup, a normal cycle or an explicit button; no retry loop.
    for (const item of state.outbox.slice(0,3)) {
      item.attempts++; item.last_attempt_at = new Date(clock()).toISOString();
      await save(state);
      try {
        await api.notifications.create(item.id, {type:'basic',iconUrl:api.runtime.getURL('icons/icon128.png'),
          title:item.title,message:item.message});
      } catch {
        state.alertsBlocked = true;
        state.notificationError = 'La notification n’a pas été créée. Vérifie les notifications Edge/Windows puis Réessayer.';
        await save(state); return;
      }
      state.outbox = state.outbox.filter(pending => pending.id !== item.id);
      state.delivered.unshift({id:item.id,key:item.key,kind:item.kind,delivered_at:new Date(clock()).toISOString()});
      state.delivered = state.delivered.slice(0,300);
      if (item.kind === 'AUTH') state.authNoticeActive = true;
      state.alertsBlocked = false; state.notificationError = null;
      await save(state);
    }
  }
  async function alarms(state) {
    if (state.enabled && !state.authPaused) {
      if (!await api.alarms.get(MONITOR_ALARM))
        await api.alarms.create(MONITOR_ALARM, {periodInMinutes: state.config.interval_seconds / 60});
    } else await api.alarms.clear(MONITOR_ALARM);
  }
  async function pauseForUser(state, reason) {
    state.authPaused = true; state.inFlight = null; state.status = 'AUTH_REQUIRED';
    state.message = reason;
    if (!state.authNoticeActive) {
      if (!state.outbox.some(item => item.kind === 'AUTH'))
        await queueNotice(state,'AUTH','CROUS · vérification manuelle nécessaire',
          'La surveillance est en pause. Termine la connexion dans le site officiel, puis clique Reprendre.',
          'auth-' + uuid());
    }
    await flushOutbox(state);
    await api.alarms.clear(DEADLINE_ALARM); await alarms(state); await save(state);
    return state;
  }
  async function sendRead(state) {
    if (!state.inFlight || state.tabId == null) return;
    await api.tabs.sendMessage(state.tabId, {type: 'READ_RESULTS', cycle_id: state.inFlight.id}).catch(() => {});
  }
  async function notify(state, notices) {
    if (!notices.length) return;
    const changed = notices.filter(n => n.kind === 'PRICE_CHANGED').length;
    const title = notices.length === 1 && notices[0].kind === 'MATCH_CONFIRMED' ? 'CROUS · chambre indiquée à Cergy'
      : changed ? 'CROUS · prix modifié'
        : notices.every(n => n.kind === 'DETAILS_CHANGED') ? 'CROUS · fiche mise à jour' : 'CROUS · nouvelle fiche à vérifier';
    const message = notices.length === 1 ? notices[0].title + (notices[0].confirmed ? ' · chambre indiquée à Cergy' : ' · type ou ville à vérifier')
      : notices.length + ' fiches nouvelles ou modifiées, dont ' + changed + ' changement(s) de prix. Ouvre le suivi pour vérifier.';
    const key = 'results-' + JSON.stringify(notices.map(item => [item.key,item.kind,item.observed_at]));
    await queueNotice(state,'RESULTS',title,message,key);
    await flushOutbox(state);
  }
  async function run(state, manual = false, currentDocument = null) {
    const now = clock();
    await flushOutbox(state);
    if (state.authPaused) return state;
    if (!manual && !state.enabled) return state;
    if (state.inFlight && state.inFlight.deadline > now) {
      state.message = 'Une vérification est déjà en cours.'; await save(state); return state;
    }
    let tab = null;
    if (state.tabId != null) tab = await api.tabs.get(state.tabId).catch(() => null);
    if (tab && !searchUrl(tab.url))
      return pauseForUser(state, 'L’onglet a quitté la recherche : termine la connexion ou reviens à Cergy, puis Reprendre. Aucun rechargement forcé.');
    if (state.nextAllowedAt > now) {
      state.message = 'Prochain contrôle possible à ' + new Date(state.nextAllowedAt).toLocaleTimeString('fr-FR') + '.';
      await save(state); return state;
    }
    const id = uuid();
    state.nextAllowedAt = now + state.config.interval_seconds * 1000;
    state.inFlight = {id, startedAt: now, deadline: now + LEASE_MS};
    state.status = 'CHECKING'; state.message = 'Lecture de la recherche dans l’onglet dédié…';
    await save(state);
    await api.alarms.create(DEADLINE_ALARM, {when: now + LEASE_MS});
    try {
      if (tab && currentDocument) {
        if (currentDocument.id) state.inFlight.documentId = currentDocument.id;
        await save(state); await sendRead(state);
      } else if (tab) {
        // Always name the extension-owned tab; never reload the active/unspecified tab.
        await api.tabs.reload(state.tabId);
      } else {
        tab = await api.tabs.create({url: CERGY_URL, active: false});
        state.tabId = tab.id; await save(state);
      }
      // The previous document may still be alive after tabs.reload resolves.
      // Only CONTENT_READY or the new navigation's complete event can trigger READ_RESULTS.
    } catch {
      state.inFlight = null; state.status = 'UNKNOWN'; state.message = 'Onglet inaccessible ; dernier relevé fiable conservé.';
      await api.alarms.clear(DEADLINE_ALARM); await save(state);
    }
    return state;
  }
  async function accept(state, message, sender) {
    if (sender?.id !== api.runtime.id || sender.tab?.id !== state.tabId || !searchUrl(sender.tab.url) ||
        !state.inFlight || message.cycle_id !== state.inFlight.id) return {ignored: true};
    if (state.inFlight.documentId && sender.documentId !== state.inFlight.documentId) return {ignored: true};
    if (clock() > state.inFlight.deadline) return {ignored: true};
    const snapshot = validateSnapshot(message.snapshot);
    if (Date.parse(snapshot.observed_at) < state.inFlight.startedAt - 5000 || Date.parse(snapshot.observed_at) > clock() + 300000)
      throw new Error('Le relevé ne correspond pas au cycle courant.');
    state.inFlight = null;
    const result = applySnapshot(state, snapshot, {now: clock()});
    state = result.state;
    await api.alarms.clear(DEADLINE_ALARM);
    // An explicit error shell cannot establish whether the session expired.
    // Preserve the authenticated history and retry only at the normal cadence.
    if (snapshot.status === 'UNKNOWN' && snapshot.reason === 'SOURCE_UNAVAILABLE') {
      state.message = 'Erreur du portail CROUS ; session non évaluée, dernières fiches conservées. Nouvel essai au prochain contrôle.';
      await save(state); return {state};
    }
    if (state.config.require_authenticated && (snapshot.audience !== 'AUTHENTICATED' || !snapshot.audience_confirmed))
      return {state: await pauseForUser(state, 'Couverture publique ou non confirmée : le profil connecté n’a pas été vérifié. Connecte-toi normalement, puis Reprendre.')};
    if (snapshot.reason === 'AUTH_REQUIRED' || snapshot.reason === 'ACCESS_REQUIRES_USER')
      return {state: await pauseForUser(state, 'Session ou accès à vérifier ; surveillance suspendue sans toucher à la connexion.')};
    state.authNoticeActive = false;
    state.outbox = state.outbox.filter(item => item.kind !== 'AUTH');
    await save(state); await notify(state, result.notices);
    return {state};
  }
  async function messageImpl(message, sender) {
    if (!message || typeof message !== 'object') throw new Error('Message invalide.');
    const state = await load();
    if (message.type === 'CONTENT_CONTROL') {
      if (!pageSender(sender) || Object.keys(message).some(key => !['type','action'].includes(key)) ||
          !['GET_STATUS','START','STOP','TEST'].includes(message.action))
        throw new Error('Commande de page non autorisée.');
      if (message.action === 'GET_STATUS') return {view:pageView(state,sender.tab.id)};
      if (message.action === 'TEST') {
        await queueNotice(state,'TEST','TEST local · suivi CROUS',
          'Ceci est un test de notification local. Aucun logement détecté, aucune candidature et aucune requête CROUS.',
          'test-' + uuid());
        await flushOutbox(state); return {view:pageView(state,sender.tab.id)};
      }
      if (message.action === 'STOP') {
        if (state.tabId !== sender.tab.id) throw new Error('Cet onglet n’est pas celui suivi.');
        state.enabled = false; state.inFlight = null; state.status = 'STOPPED';
        state.message = 'Surveillance en pause depuis le badge local.';
        await api.tabs.sendMessage(state.tabId,{type:'CANCEL_READ'}).catch(() => {});
        await api.alarms.clear(DEADLINE_ALARM); await alarms(state); await save(state);
        return {view:pageView(state,sender.tab.id)};
      }
      // Explicit trusted badge click adopts this exact Cergy search tab. It does
      // not navigate another tab or read an old document during a reload.
      if (state.tabId !== sender.tab.id) {
        if (state.tabId != null) await api.tabs.sendMessage(state.tabId,{type:'CANCEL_READ'}).catch(() => {});
        state.inFlight = null; await api.alarms.clear(DEADLINE_ALARM);
      }
      state.tabId = sender.tab.id; state.enabled = true; state.authPaused = false;
      await save(state); await alarms(state);
      const next = await run(state,false,{id:sender.documentId || null});
      return {view:pageView(next,sender.tab.id)};
    }
    if (message.type === 'CONTENT_READY') {
      if (sender?.id !== api.runtime.id || sender.tab?.id !== state.tabId || !searchUrl(sender.tab.url)) return {ignored: true};
      if (state.inFlight) {
        if (sender.documentId) {state.inFlight.documentId = sender.documentId; await save(state);}
        await sendRead(state);
      }
      return {cycle_id: state.inFlight?.id || null};
    }
    if (message.type === 'RESULTS') return accept(state, message, sender);
    if (!uiSender(sender)) throw new Error('Commande non autorisée.');
    if (message.type === 'GET_STATE') return {state};
    if (message.type === 'RETRY_ALERTS') {await flushOutbox(state); return {state};}
    if (message.type === 'TEST_ALERT') {
      await queueNotice(state,'TEST','TEST local · suivi CROUS',
        'Ceci est un test de notification local. Aucun logement détecté, aucune candidature et aucune requête CROUS.',
        'test-' + uuid());
      await flushOutbox(state); return {state};
    }
    if (message.type === 'SAVE_CONFIG') {
      state.config = validateConfig(message.config); await api.alarms.clear(MONITOR_ALARM);
      await alarms(state); await save(state); return {state};
    }
    if (message.type === 'STOP') {
      if (state.tabId != null) await api.tabs.sendMessage(state.tabId, {type:'CANCEL_READ'}).catch(() => {});
      state.enabled = false; state.authPaused = false; state.inFlight = null; state.status = 'STOPPED';
      state.message = 'Surveillance en pause. Les résultats et l’historique restent conservés.';
      await api.alarms.clear(DEADLINE_ALARM); await alarms(state); await save(state); return {state};
    }
    if (message.type === 'START') {
      state.enabled = true; state.authPaused = false;
      await alarms(state); await save(state);
      return {state: await run(state)};
    }
    if (message.type === 'CHECK_NOW') return {state: await run(state, true)};
    if (message.type === 'OPEN_SEARCH') {
      const tab = state.tabId == null ? null : await api.tabs.get(state.tabId).catch(() => null);
      if (tab && searchUrl(tab.url)) await api.tabs.update(tab.id, {active: true});
      else {
        const opened = await api.tabs.create({url: CERGY_URL, active: true});
        state.tabId = opened.id; await save(state);
      }
      return {state};
    }
    if (message.type === 'OPEN_OFFER') {
      const url = officialUrl(message.url);
      if (!Object.values(state.records).some(row => row.offer.url === url)) throw new Error('Fiche absente de l’historique.');
      await api.tabs.create({url, active: true}); return {state};
    }
    if (message.type === 'IMPORT_SNAPSHOT') {
      const result = applySnapshot(state, message.snapshot, {origin: 'IMPORT', now: clock()});
      await save(result.state); return {state: result.state};
    }
    throw new Error('Commande inconnue.');
  }
  return {
    message: (message, sender) => serial(() => messageImpl(message, sender)),
    startup: () => serial(async () => {
      const state = await load();
      // A persisted cycle is bounded; a restarted worker neither duplicates it nor resets the cooldown.
      if (state.inFlight && state.inFlight.deadline <= clock()) {
        state.inFlight = null; state.status = 'UNKNOWN'; state.message = 'Lecture interrompue ; dernier relevé fiable conservé.';
        await save(state);
      }
      await alarms(state);
      await flushOutbox(state);
      return state;
    }),
    browserStartup: () => serial(async () => {
      const state = await load(); state.tabId = null; state.inFlight = null;
      await save(state); await alarms(state);
    }),
    alarm: name => serial(async () => {
      const state = await load();
      if (name === DEADLINE_ALARM && state.inFlight && state.inFlight.deadline <= clock()) {
        state.inFlight = null; state.status = 'UNKNOWN'; state.message = 'Délai de lecture dépassé ; aucun zéro n’est déduit.';
        await save(state); return;
      }
      if (name === MONITOR_ALARM) await run(state);
    }),
    tabUpdated: (id, info, tab) => serial(async () => {
      const state = await load();
      if (id !== state.tabId || !state.inFlight || info.status !== 'complete') return;
      if (!searchUrl(tab.url)) await pauseForUser(state, 'Connexion à terminer dans l’onglet officiel ; aucun rechargement automatique.');
      else await sendRead(state);
    })
  };
}
