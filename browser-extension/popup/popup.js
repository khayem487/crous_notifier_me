import {validateConfig, roomConfirmed, matchOffer} from '../src/model.js';
import {initialState, applySnapshot} from '../src/engine.js';
const $ = id => document.getElementById(id);
const preview = !globalThis.chrome?.runtime?.id;
let current = initialState(), page = 'offers';
const stateLabels = {STOPPED: 'En pause', CHECKING: 'Vérification…', COMPLETE: 'Relevé complet',
  PARTIAL: 'Résultat partiel', AUTH_REQUIRED: 'Reconnexion nécessaire', UNKNOWN: 'À vérifier'};
const scopeName = scope => scope === 'AUTHENTICATED' ? 'Connecté confirmé' : 'Public';
const date = value => value ? new Date(value).toLocaleString('fr-FR', {dateStyle:'short',timeStyle:'short'}) : 'Jamais';
const euro = value => new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR'}).format(value);
function element(tag, content, className = '') {
  const el = document.createElement(tag); el.textContent = content; if (className) el.className = className; return el;
}
function error(message) { $('error').hidden = !message; $('error').textContent = message || ''; }
async function command(type, extra = {}) {
  error('');
  try {
    if (preview) throw new Error('Aperçu TEST uniquement : aucune action sur le portail.');
    const result = await chrome.runtime.sendMessage({type, ...extra});
    if (result?.error) throw new Error(result.error);
    if (result?.state) { current = result.state; render(); }
    return result;
  } catch (err) { error(err.message || 'Erreur locale.'); return null; }
}
function renderOffers() {
  const holder = $('offers'); holder.replaceChildren();
  const kind = $('kind-filter').value, scope = $('scope-filter').value;
  const rows = Object.values(current.records).filter(row => row.present && matchOffer(row.offer,current.config))
    .filter(row => scope === 'ALL' || row.offer.audience === scope)
    .filter(row => kind === 'ALL' || (kind === 'CONFIRMED' ? roomConfirmed(row.offer) && row.offer.city === 'Cergy'
      : !row.offer.housing_type || !row.offer.city))
    .sort((a,b) => Date.parse(b.last_seen) - Date.parse(a.last_seen));
  $('offer-count').textContent = rows.length + ' fiche(s) conservée(s) correspondant à l’affichage. Dernière observation, pas une garantie actuelle.';
  if (!rows.length) holder.append(element('p','Aucune fiche dans cet affichage. Un résultat public vide ne préjuge pas des offres accessibles avec ton dossier DSE.','empty'));
  for (const row of rows.slice(0,100)) {
    const offer = row.offer, card = element('article','', 'card');
    card.append(element('span',scopeName(offer.audience),'tag'));
    const confirmed = roomConfirmed(offer) && offer.city === 'Cergy';
    card.append(element('span',confirmed ? 'Chambre indiquée · Cergy' : 'Type ou ville à vérifier','tag ' + (confirmed ? 'good' : 'review')));
    if (row.origin === 'IMPORT') card.append(element('span','Import local','tag'));
    card.append(element('h3', offer.title));
    card.append(element('p',offer.city || 'Ville exacte à confirmer sur la fiche.'));
    const price = offer.rent_eur != null ? euro(offer.rent_eur)
      : offer.rent_min_eur != null && offer.rent_max_eur != null ? euro(offer.rent_min_eur) + ' – ' + euro(offer.rent_max_eur)
        : offer.rent_min_eur != null ? 'À partir de ' + euro(offer.rent_min_eur) : 'Loyer non précisé';
    card.append(element('p',price,'price'));
    card.append(element('p',(offer.housing_type || 'Type non confirmé') + ' · ' +
      (offer.surface_m2 != null ? offer.surface_m2 + ' m² indiqués' : 'Surface à vérifier')));
    card.append(element('p','Entrée : ' + (offer.available_from || 'date à confirmer') + ' · vu le ' + date(row.last_seen),'muted'));
    const open = element('button','Ouvrir la fiche officielle ↗'); open.addEventListener('click',()=>command('OPEN_OFFER',{url:offer.url}));
    open.disabled = preview; card.append(open); holder.append(card);
  }
}
function renderHistory() {
  $('history').replaceChildren(); $('events').replaceChildren();
  for (const row of current.history.slice(0,40)) {
    const node = element('div','','log-row');
    node.append(element('p',date(row.observed_at) + ' · ' + scopeName(row.audience)));
    node.append(element('p',(stateLabels[row.status] || row.status) + ' · ' + row.visible_count + ' fiche(s) visible(s)' +
      (row.total_count !== null ? ' / compteur ' + row.total_count : '') + (row.origin === 'IMPORT' ? ' · import' : '')));
    $('history').append(node);
  }
  if (!current.history.length) $('history').append(element('p','Aucun contrôle enregistré.','empty'));
  const eventLabels = {NEW:'Nouvelle fiche',PRICE_CHANGED:'Prix affiché modifié',MATCH_CONFIRMED:'Chambre à Cergy indiquée après vérification',
    DETAILS_CHANGED:'Informations de la fiche modifiées',NO_LONGER_LISTED:'Plus listée dans un relevé complet'};
  for (const row of current.events.slice(0,40)) {
    const node = element('div','','log-row');
    node.append(element('p',(eventLabels[row.kind] || row.kind) + ' · ' + scopeName(row.audience)));
    node.append(element('p',row.title));
    node.append(element('p',date(row.observed_at) + (row.origin === 'IMPORT' ? ' · import' : ''),'muted'));
    $('events').append(node);
  }
  if (!current.events.length) $('events').append(element('p','Pas de changement de fiche enregistré.','empty'));
}
function render() {
  $('status').textContent = stateLabels[current.status] || current.status; $('status').dataset.state = current.status;
  $('message').textContent = current.message;
  $('alerts-state').textContent = current.alertsBlocked
    ? 'Alertes bloquées · ' + current.outbox.length + ' en attente. ' + current.notificationError
    : current.outbox.length ? current.outbox.length + ' alerte(s) en attente de création.'
      : 'Alertes locales : aucune alerte en attente.';
  $('alerts-state').className = current.alertsBlocked ? 'error' : 'fine';
  $('test-alert').disabled = preview;
  $('retry-alerts').disabled = preview || !current.outbox.length;
  $('scope').textContent = current.lastSnapshot ? scopeName(current.lastSnapshot.audience) +
    (current.lastSnapshot.audience_confirmed ? '' : ' · périmètre non confirmé') : 'Pas de relevé';
  $('checked').textContent = 'Dernier relevé : ' + date(current.lastSnapshot?.observed_at);
  const reliable = Object.values(current.lastReliable).sort((a,b)=>Date.parse(b.observed_at)-Date.parse(a.observed_at))[0];
  $('reliable').textContent = reliable ? 'Dernier relevé complet : ' + date(reliable.observed_at) + ' · ' + scopeName(reliable.audience) : '';
  $('budget-chip').textContent = current.config.max_rent_eur == null ? 'Sans plafond' : 'Budget : ' + euro(current.config.max_rent_eur);
  $('start').textContent = current.authPaused ? 'Reprendre après connexion' : current.enabled ? 'Surveillance activée' : 'Démarrer';
  $('start').disabled = preview || (current.enabled && !current.authPaused);
  $('check').disabled = preview || current.authPaused || !!current.inFlight;
  $('stop').disabled = preview || (!current.enabled && !current.inFlight);
  $('open-search').disabled = preview;
  $('export').disabled = preview || !current.lastSnapshot;
  $('backup').disabled = preview;
  $('import-file').disabled = preview;
  if (!document.activeElement?.closest('#settings-form')) {
    $('budget').value = current.config.max_rent_eur ?? '';
    $('interval').value = String(current.config.interval_seconds);
    $('audience-mode').value = current.config.require_authenticated ? 'AUTHENTICATED' : 'PUBLIC_ONLY';
  }
  for (const name of ['offers','history','settings']) {
    $(name + '-page').hidden = name !== page; $('tab-' + name).setAttribute('aria-pressed',String(name === page));
  }
  renderOffers(); renderHistory();
}
for (const name of ['offers','history','settings']) $('tab-' + name).addEventListener('click',()=>{page=name;render();});
$('kind-filter').addEventListener('change',renderOffers); $('scope-filter').addEventListener('change',renderOffers);
for (const [id,type] of [['start','START'],['stop','STOP'],['check','CHECK_NOW'],['open-search','OPEN_SEARCH']])
  $(id).addEventListener('click',()=>command(type));
$('test-alert').addEventListener('click',()=>command('TEST_ALERT'));
$('retry-alerts').addEventListener('click',()=>command('RETRY_ALERTS'));
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  try { await command('SAVE_CONFIG',{config:validateConfig({...current.config,
    max_rent_eur:$('budget').value.trim() || null,interval_seconds:Number($('interval').value),
    require_authenticated:$('audience-mode').value === 'AUTHENTICATED'})}); } catch (err) {error(err.message);}
});
function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href=url; link.download=filename; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('export').addEventListener('click',()=>{
  if (!current.lastSnapshot) return;
  downloadJson(current.lastSnapshot,'crous-cergy-snapshot-v1-' + new Date().toISOString().slice(0,10) + '.json');
});
$('backup').addEventListener('click',()=>{
  downloadJson({schema:'crous-cergy-browser-backup',schema_version:1,exported_at:new Date().toISOString(),
    config:current.config,records:current.records,history:current.history,events:current.events,
    last_snapshot:current.lastSnapshot,last_reliable:current.lastReliable,
    pending_notifications:current.outbox,delivered_notifications:current.delivered},
    'crous-cergy-backup-' + new Date().toISOString().slice(0,10) + '.json');
});
$('import-file').addEventListener('change',async event=>{
  const file = event.target.files?.[0]; if (!file) return;
  try {
    if (file.size > 512000) throw new Error('Fichier limité à 500 Ko.');
    await command('IMPORT_SNAPSHOT',{snapshot:JSON.parse(await file.text())});
  } catch (err) {error(err.message || 'Fichier JSON invalide.');}
  event.target.value='';
});
if (preview) {
  $('preview-banner').hidden=false;
  const now = new Date().toISOString();
  current = applySnapshot(current,{schema_version:1,source_url:current.config.source_url,zone:'Cergy',audience:'PUBLIC_ONLY',
    audience_confirmed:true,observed_at:now,status:'COMPLETE',pagination_complete:true,total_count:1,
    reason:'TEST_SYNTHETIC_PREVIEW',offers:[{url:'https://trouverunlogement.lescrous.fr/tools/47/accommodations/TEST-PREVIEW',
      title:'TEST — Résidence fictive pour l’aperçu',city:null,housing_type:null,rent_eur:null,
      rent_min_eur:350,rent_max_eur:420,residence:'TEST — Résidence fictive'}]},{origin:'IMPORT'}).state;
  current.message='Aperçu visuel TEST ; ces données fictives ne sont pas des logements disponibles.';
  $('settings-form').querySelector('button').disabled=true;
  render();
} else {
  chrome.storage.onChanged.addListener((changes,area)=>{
    if(area==='local' && changes.cergyLocalV1?.newValue){current=changes.cergyLocalV1.newValue;render();}
  });
  command('GET_STATE');
}
