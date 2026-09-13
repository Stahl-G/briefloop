import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync('frontend/app.js','utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function runtimeFeedback('),source.indexOf('function renderRuntimeFeedback(')),context);
test('errors remain in their turn; retries update without hiding provider reasons',()=>{
 const messages=[{id:'u1',role:'user',turn_id:'u1',status:'completed',created:'2026-09-13T10:00:00'}, {id:'u2',role:'user',turn_id:'u2',status:'delivered',created:'2026-09-13T11:00:00'}];
 const events=[{seq:1,kind:'error',created:'2026-09-13T10:01:00',data:{message:'HTTP 429 quota exceeded'}}, {seq:2,kind:'runtime/status',data:{turnId:'u2',status:'retry',message:'HTTP 503 unavailable',attempt:2,next:40000}}];
 let rows=context.runtimeFeedback(messages,events);
 assert.equal(rows.length,2);assert.equal(rows[0].anchor,'u1');assert.equal(rows[1].retry,true);
 events.push({seq:3,kind:'runtime/status',data:{turnId:'u2',status:'resumed'}});
 rows=context.runtimeFeedback(messages,events);assert.equal(rows[1].retry,false);assert.equal(rows[1].resumed,true);assert.match(rows[1].message,/503/);
});
