import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {parseDocument} from '../src/parser.js';
import {CERGY_URL, ORIGIN} from '../src/model.js';

const logoutPath='/mse/oidc/local-logout';
function read(header, body='') {
  const {document}=parseHTML(`<!doctype html><html><body><header>${header}</header><main>
    <h1>Aucun logement trouvé pour Cergy</h1>${body}</main></body></html>`);
  return parseDocument(document,{url:CERGY_URL,now:'2026-10-27T18:00:00Z'});
}

test('the observed header account/logout route confirms the session with a synthetic prefixed name',()=>{
  for (const href of [logoutPath,ORIGIN+logoutPath]) {
    const snapshot=read(`<a href="${href}">TEST PERSONNE ⋅ Déconnexion</a><a href="/tools/47/cart">Mon dossier</a>`);
    assert.equal(snapshot.audience,'AUTHENTICATED');assert.equal(snapshot.audience_confirmed,true);
    assert.equal(snapshot.status,'COMPLETE');assert.equal(snapshot.total_count,0);
    assert.equal(JSON.stringify(snapshot).includes('TEST PERSONNE'),false,'account identity must not enter exported snapshots');
  }
});

test('foreign, credential-bearing and altered logout destinations cannot establish authentication',()=>{
  for(const href of ['https://example.invalid'+logoutPath,'//example.invalid'+logoutPath,
    'https://user:secret@trouverunlogement.lescrous.fr'+logoutPath,logoutPath+'?unrecognised=1',logoutPath+'#fragment',
    '/mse/oidc/local-logout-extra','javascript:void(0)']) {
    const snapshot=read(`<a href="${href}">TEST PERSONNE ⋅ Déconnexion</a>`);
    assert.equal(snapshot.audience,'PUBLIC_ONLY');assert.equal(snapshot.audience_confirmed,false);
    assert.notEqual(snapshot.status,'COMPLETE');
  }
  const foreignExactLabel=read('<a href="https://example.invalid/logout">Déconnexion</a>');
  assert.equal(foreignExactLabel.audience_confirmed,false);
});

test('a logout link within a result card or nested main header never establishes the account scope',()=>{
  const snapshot=read('<a href="/mse/discovery/connect">Identification</a>',
    `<article class="fr-card"><header><a href="${logoutPath}">TEST PERSONNE ⋅ Déconnexion</a></header></article>`);
  assert.equal(snapshot.audience,'PUBLIC_ONLY');assert.equal(snapshot.audience_confirmed,true);
  assert.equal(snapshot.status,'COMPLETE');
});

test('a real logout destination and contradictory header Identification remain unknown',()=>{
  const snapshot=read(`<a href="${logoutPath}">TEST PERSONNE ⋅ Déconnexion</a>
    <a href="/mse/discovery/connect">Identification</a>`);
  assert.equal(snapshot.status,'UNKNOWN');assert.equal(snapshot.reason,'AUDIENCE_CONFLICT');
  assert.equal(snapshot.audience_confirmed,false);assert.deepEqual(snapshot.offers,[]);
});
