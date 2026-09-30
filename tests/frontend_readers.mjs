import test from 'node:test';
import assert from 'node:assert/strict';
import {readersUI} from '../frontend/readers.js';

test('the reader picker keeps a valid choice, fills a default audience and escapes profiles',()=>{
 const audience={value:'自己'},listeners={};
 const select={value:'',innerHTML:'',form:{elements:{audience}},addEventListener:(k,f)=>listeners[k]=f};
 const list={innerHTML:'',querySelectorAll:()=>[]};
 const ui=readersUI({api:async()=>{},action:fn=>fn(),$:id=>({'reader-select':select,'reader-profiles':list})[id]});
 ui.bind();
 ui.render([{id:'reader_1',name:'<管理层>',decisions:'出货节奏',preferences:'',skill_id:null,wiki:''}]);
 assert.match(select.innerHTML,/&lt;管理层&gt;/);assert.doesNotMatch(list.innerHTML,/<管理层>/);
 select.value='reader_1';listeners.change();
 assert.equal(audience.value,'<管理层>');
 audience.value='董事会';select.value='reader_1';listeners.change();
 assert.equal(audience.value,'董事会','a typed audience is kept');
 select.value='reader_gone';ui.render([]);
 assert.equal(select.value,'');
});
