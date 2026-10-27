import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const read=path=>readFileSync(resolve(root,path),'utf8');
const manifest=JSON.parse(read('manifest.json'));
assert.equal(manifest.manifest_version,3);
assert.deepEqual([...manifest.permissions].sort(),['alarms','notifications','storage']);
assert.deepEqual(manifest.host_permissions,['https://trouverunlogement.lescrous.fr/*']);
assert.equal(manifest.externally_connectable,undefined);
assert.equal(manifest.background.type,'module');
const required=[manifest.background.service_worker,manifest.action.default_popup,...Object.values(manifest.icons),
 ...manifest.content_scripts.flatMap(item=>item.js),...manifest.web_accessible_resources.flatMap(item=>item.resources)];
for(const path of required)assert(existsSync(resolve(root,path)),path+' missing');
for(const entry of manifest.content_scripts){assert.equal(entry.all_frames,false);assert.deepEqual(entry.matches,manifest.host_permissions);}
for(const size of [48,128]){
 const png=readFileSync(resolve(root,'icons/icon'+size+'.png'));
 assert.equal(png.subarray(1,4).toString(),'PNG');assert.equal(png.readUInt32BE(16),size);assert.equal(png.readUInt32BE(20),size);
}
for(const path of ['src/model.js','src/parser.js','src/engine.js','src/controller.js','src/worker.js','src/content.js','src/controls.js','popup/popup.js']){
 const source=read(path);
 assert(!/chrome\.(?:cookies|identity|debugger|webRequest|history)\b/.test(source),path+' forbidden API');
 assert(!/\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(/.test(source),path+' unexpected network transport');
}
assert(!/\son\w+=/.test(read('popup/index.html')),'Inline event handler');
assert(!/https?:\/\//.test(read('popup/index.html')),'Remote popup asset');
console.log('Manifest, local assets, PNG dimensions and bounded permissions validated.');
