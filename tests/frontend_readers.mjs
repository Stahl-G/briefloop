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


test('first render restores the saved reader after options exist and polling keeps the user choice',()=>{
 let html='',chosen='',options=[];
 const select={
  get value(){return chosen},
  set value(value){chosen=options.includes(value)?value:''},
  get innerHTML(){return html},
  set innerHTML(value){html=value;options=[...value.matchAll(/<option value="([^"]*)"/g)].map(m=>m[1]);chosen=options[0]||''},
 };
 const list={innerHTML:'',querySelectorAll:()=>[]};
 const ui=readersUI({api:async()=>{},action:fn=>fn(),$:id=>({'reader-select':select,'reader-profiles':list})[id]});
 const readers=[{id:'reader_saved',name:'管理层'},{id:'reader_new',name:'董事会',skill_id:'legacy_skill',wiki:'# 旧经验'}];
 select.value='reader_saved';assert.equal(select.value,'','native select drops a value with no matching option');
 ui.render(readers,{readerId:'reader_saved'});
 assert.equal(select.value,'reader_saved');
 assert.match(list.innerHTML,/历史专属技能 legacy_skill（暂不启用）/);
 assert.match(list.innerHTML,/历史读者经验（暂不启用）/);
 select.value='reader_new';ui.render(readers,{readerId:'reader_saved'});
 assert.equal(select.value,'reader_new','polling must not restore a stale requirement over the user selection');
 select.value='';ui.render(readers,{readerId:'reader_saved'});
 assert.equal(select.value,'','clearing the choice survives polling');
});
