import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {readFileSync} from 'node:fs';
import {parseDocument} from '../src/parser.js';
import {CERGY_URL} from '../src/model.js';

function parse(html) {
  return parseDocument(parseHTML(html).document,{url:CERGY_URL,now:'2026-10-27T06:00:00Z'});
}

test('official bare 500 and internal-error shells are unknown source outages, not empty searches or invalid credentials',()=>{
  for (const html of ['<html><body>500<h1></h1></body></html>',
    '<html><body><h1>500 - ERREUR INTERNE</h1><p>La page ou la fonction ne fonctionne pas.</p></body></html>',
    '<html><body><main><h1>500 - ERREUR INTERNE</h1></main></body></html>']) {
    const snapshot=parse(html);
    assert.equal(snapshot.status,'UNKNOWN');assert.equal(snapshot.reason,'SOURCE_UNAVAILABLE');
    assert.equal(snapshot.audience_confirmed,false);assert.equal(snapshot.total_count,null);
    assert.equal(snapshot.pagination_complete,false);assert.deepEqual(snapshot.offers,[]);
  }
});

test('a missing loading main remains unrecognised and an ordinary rent of 500 euros remains a result',()=>{
  assert.equal(parse('<html><body>Chargement…</body></html>').reason,'RESULTS_NOT_READY');
  const fixture=readFileSync(new URL('./fixtures/two-offers-public.html',import.meta.url),'utf8');
  const snapshot=parse(fixture.replace(/480/g,'500'));
  assert.equal(snapshot.status,'COMPLETE');assert.equal(snapshot.offers.length,2);
});
