import test from 'node:test';
import assert from 'node:assert/strict';
import {diffRows,changedRange} from '../frontend/version-diff.js';
const p=text=>({type:'paragraph',content:[{type:'text',text}]});
test('inserting a paragraph does not mark unchanged following paragraphs',()=>{
 const a=[p('一'),p('二')],b=[p('一'),p('新增'),p('二')];
 assert.deepEqual(diffRows(a,b).map(r=>r.changed),[false,true,false]);
 assert.equal(diffRows(a,b)[1].before,undefined);
});
test('revision matches replaced text and keeps source documents untouched',()=>{
 const a=[p('同比增长10%'),p('删除')],b=[p('同比增长20%')],snapshot=JSON.stringify([a,b]);
 const rows=diffRows(a,b);assert.equal(rows.length,2);assert.ok(rows.every(r=>r.changed));
 assert.deepEqual(changedRange('同比增长10%','同比增长20%'),{start:4,endA:5,endB:5});
 assert.equal(JSON.stringify([a,b]),snapshot);
});
test('table, figure and style changes are not lost to text-only comparisons',()=>{
 for(const [a,b] of [[{type:'image',attrs:{src:'a'}},{type:'image',attrs:{src:'b'}}],[{...p('同字'),attrs:{textAlign:'left'}},{...p('同字'),attrs:{textAlign:'right'}}]])assert.equal(diffRows([a],[b])[0].changed,true);
 assert.equal(diffRows([{...p('同字'),attrs:{blockId:'a'}}],[{...p('同字'),attrs:{blockId:'b'}}])[0].changed,false);
});
