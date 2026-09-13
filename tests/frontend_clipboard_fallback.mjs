import test from 'node:test';
import assert from 'node:assert/strict';
import {copyText} from '../frontend/clipboard.js';
test('denied async clipboard falls back and restores focus, failures remain failures',async()=>{
 let copied='',removed=0,focused=0,field;
 const doc={activeElement:{focus(){focused++}},body:{append(x){field=x}},createElement(){return {style:{},setAttribute(){},select(){copied=this.value},remove(){removed++}}},execCommand(){return true}};
 const nav={clipboard:{async writeText(){throw Error('denied')}}};
 await copyText('中文回复',doc,nav);assert.equal(copied,'中文回复');assert.equal(removed,1);assert.equal(focused,1);
 doc.execCommand=()=>false;await assert.rejects(copyText('失败',doc,nav),/无法访问剪贴板/);assert.equal(removed,2);
});
