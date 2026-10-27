// Isolated-world content script. No cookie, credential, fetch, form or application action.
(async () => {
  if (location.origin !== 'https://trouverunlogement.lescrous.fr' || location.pathname !== '/tools/47/search') return;
  const {parseDocument} = await import(chrome.runtime.getURL('src/parser.js'));
  const {officialUrl} = await import(chrome.runtime.getURL('src/model.js'));
  const {controlMessage} = await import(chrome.runtime.getURL('src/controls.js'));
  try { officialUrl(location.href, 'search'); } catch { return; }
  const host = document.createElement('aside');
  host.id = 'crous-personal-monitor-widget';
  host.setAttribute('aria-label','Suivi CROUS local');
  const shadow = host.attachShadow({mode:'closed'});
  const style = document.createElement('style');
  style.textContent = ':host{position:fixed;right:16px;bottom:16px;z-index:2147483000;font:12px Segoe UI,system-ui,sans-serif;color:#243349}section{width:286px;padding:13px;border:1px solid #c7d9ec;border-radius:12px;background:#fff;box-shadow:0 6px 25px #203c5925}header{display:flex;justify-content:space-between;align-items:center}strong{font-size:12px}p{line-height:1.45;margin:8px 0;color:#526b85}.buttons{display:flex;gap:5px;flex-wrap:wrap}button{font:11px Segoe UI,system-ui,sans-serif;border:1px solid #cbd9e8;border-radius:6px;background:#edf3fa;color:#214d85;padding:6px 8px;cursor:pointer}button:disabled{opacity:.5;cursor:default}button:focus-visible{outline:3px solid #6eafff}.warn{color:#8a4e0b}[hidden]{display:none!important}';
  shadow.append(style);
  const panel = document.createElement('section'), heading = document.createElement('header');
  const title = document.createElement('strong'); title.textContent = 'Mon suivi CROUS · local';
  const reduce = document.createElement('button'); reduce.textContent='Réduire'; reduce.setAttribute('aria-expanded','true');
  heading.append(title,reduce);
  const status = document.createElement('p'); status.setAttribute('role','status'); status.textContent='État local en cours de lecture…';
  const body = document.createElement('div'), audience = document.createElement('p'), alerts = document.createElement('p');
  const buttons = document.createElement('div'); buttons.className='buttons';
  const controls = {};
  async function act(action,event) {
    const message = controlMessage(action,event); if (!message) return;
    const answer = await chrome.runtime.sendMessage(message).catch(()=>({error:'Suivi local indisponible.'}));
    if (answer?.error) {status.textContent=answer.error;return;}
    if (answer?.view) show(answer.view);
  }
  for (const [action,label] of [['START','Suivre cet onglet'],['STOP','Pause'],['TEST','Tester l’alerte'],['GET_STATUS','Actualiser l’état']]) {
    const button=document.createElement('button');button.textContent=label;
    button.addEventListener('click',event=>act(action,event));controls[action]=button;buttons.append(button);
  }
  function show(view) {
    const labels={STOPPED:'En pause',CHECKING:'Lecture en cours',COMPLETE:'Relevé complet',PARTIAL:'Page courante seulement',
      AUTH_REQUIRED:'Connexion ou accès à vérifier',UNKNOWN:'Résultat inconnu'};
    status.textContent=(labels[view.status]||'État local')+(view.owned?' · cet onglet est suivi':' · onglet non suivi');
    audience.textContent=(view.audience==='AUTHENTICATED'?'Connecté confirmé':view.audience==='PUBLIC_ONLY'?'Couverture publique':'Périmètre non vérifié')+
      (view.checked_at?' · '+new Date(view.checked_at).toLocaleString('fr-FR'):' · aucun contrôle');
    alerts.textContent=view.alertsBlocked?'Alertes bloquées · '+view.pendingAlerts+' en attente. Ouvre le suivi pour réessayer.':
      'Alerte TEST locale possible ; aucune candidature automatique.';
    alerts.className=view.alertsBlocked?'warn':'';
    controls.START.disabled=view.owned&&view.enabled&&!view.authPaused;
    controls.STOP.disabled=!view.owned||!view.enabled;
  }
  reduce.addEventListener('click',event=>{
    if(!event.isTrusted)return;body.hidden=!body.hidden;
    reduce.textContent=body.hidden?'Ouvrir':'Réduire';reduce.setAttribute('aria-expanded',String(!body.hidden));
  });
  body.append(audience,alerts,buttons);panel.append(heading,status,body);shadow.append(panel);document.body.append(host);
  function viewFromState(state) {
    return {owned:true,enabled:state.enabled,authPaused:state.authPaused,status:state.status,
      audience:state.lastSnapshot?.audience||null,checked_at:state.lastSnapshot?.observed_at||null,
      alertsBlocked:state.alertsBlocked,pendingAlerts:state.outbox?.length||0};
  }
  let active = null;
  function end() {
    if (!active) return;
    active.observer.disconnect(); clearTimeout(active.timer); clearTimeout(active.deadline); active = null;
  }
  function read(cycleId) {
    if (active?.id === cycleId) return;
    end();
    const observer = new MutationObserver(schedule);
    active = {id: cycleId, observer, timer: null, deadline: null};
    function submit(force = false) {
      if (!active || active.id !== cycleId) return;
      let snapshot;
      try { snapshot = parseDocument(document, {url: location.href}); } catch { return; }
      if (!force && snapshot.status === 'UNKNOWN' && /^RESULTS_NOT_/.test(snapshot.reason || '')) return;
      end();
      chrome.runtime.sendMessage({type: 'RESULTS', cycle_id: cycleId, snapshot}).then(answer=>{
        if(answer?.state)show(viewFromState(answer.state));
        else if(answer?.error)status.textContent=answer.error;
      }).catch(() => {});
    }
    function schedule() {
      if (!active) return;
      clearTimeout(active.timer); active.timer = setTimeout(() => submit(), 1000);
    }
    observer.observe(document.documentElement, {childList: true, subtree: true, characterData: true});
    active.deadline = setTimeout(() => submit(true), 25000);
    schedule();
  }
  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === 'CANCEL_READ') { end(); return; }
    if (message?.type === 'READ_RESULTS' && typeof message.cycle_id === 'string') read(message.cycle_id);
  });
  const answer = await chrome.runtime.sendMessage({type: 'CONTENT_READY'}).catch(() => null);
  if (answer?.cycle_id) read(answer.cycle_id);
  await act('GET_STATUS');
})();
