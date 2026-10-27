import {test} from 'node:test';
import assert from 'node:assert/strict';
import {controlMessage} from '../src/controls.js';

test('badge mutation controls require an actual trusted click and emit only the narrow command', () => {
  for (const action of ['START','STOP','TEST']) {
    for (const event of [undefined, null, {}, {isTrusted:false}, {isTrusted:1}])
      assert.equal(controlMessage(action,event),null, `${action} must reject synthetic or absent clicks`);
    assert.deepEqual(controlMessage(action,{isTrusted:true,privateField:'TEST-private'}),
      {type:'CONTENT_CONTROL',action});
  }
});

test('badge status is read-only and unknown commands can never become runtime messages', () => {
  assert.deepEqual(controlMessage('GET_STATUS'),{type:'CONTENT_CONTROL',action:'GET_STATUS'});
  for (const action of ['GET_STATE','IMPORT_SNAPSHOT','SAVE_CONFIG','OPEN_OFFER','',null,{},'start'])
    assert.equal(controlMessage(action,{isTrusted:true}),null);
});
