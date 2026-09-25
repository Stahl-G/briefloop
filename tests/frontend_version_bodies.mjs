import * as time from '../frontend/time.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {section} from './source_section.mjs';

// Windows checkouts may use CRLF; handler extraction below matches LF boundaries.
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const handler=section(source,"$('version-diff').onclick=",'\n});\n','frontend/app.js')+'\n});\n';
const exportModule=fs.readFileSync(new URL('../frontend/report-export.js',import.meta.url),'utf8');

test('HTML and PDF exports take the open draft rather than a bodiless list entry',()=>{
 assert.match(exportModule,/const version=await savedVersion\(\),brief=getCurrent\(\)/,'exports must read the freshly saved draft after the save flush');
 const exportCode=section(exportModule,"if(!['download-html','download-pdf'].includes(id))return;","notice('导出未完成：'",'frontend/report-export.js');
 assert.ok(!exportCode.includes('state.briefs'),'exports must not read bodies from polled state');
});

function fixture(load){
 const el=id=>el.nodes[id]||(el.nodes[id]={id,textContent:'',value:'',disabled:false,
  // Like a real <select>, new options select the first one.
  set innerHTML(html){this.html=html;this.value=/value="([^"]+)"/.exec(html)?.[1]||''},get innerHTML(){return this.html||''},
  replaceChildren(){this.cleared=true},showModal(){calls.push('show')},querySelectorAll:()=>[]});
 el.nodes={};
 const calls=[],rendered=[],notices=[];
 const doc=text=>JSON.stringify({type:'doc',content:[{type:'paragraph',content:[{type:'text',text}]}]});
 const current={id:'v3',run_id:'r',author:'user',created:'2026-09-15T10:00:00Z',markdown:'三',editor_document:doc('三')};
 // Polled state: summaries only, the open draft included.
 const summaries=['v3','v2','v1'].map((id,i)=>({id,run_id:'r',author:i===2?'agent':'user',hash:'h'+id,created:'2026-09-15T0'+(9-i)+':00:00Z',excerpt:'…'}));
 const ctx=vm.createContext({...time,
  $:el,esc:s=>String(s),notice:(...args)=>notices.push(args),parse:s=>JSON.parse(s||'{}'),
  state:{briefs:summaries},current,savedVersion:async()=>current.id,
  action:async fn=>{try{await fn()}catch(e){notices.push([e.message,true])}},
  loadBrief:b=>load(b,calls),
  editor:{schema:{nodeFromJSON:json=>({toJSON:()=>json})}},
  Editor:class{constructor(){throw Error('summaries must not be parsed as Markdown')}},
  DOMSerializer:{fromSchema:()=>({serializeNode:()=>({})})},
  editorDocument:(d)=>d,
  renderVersionDiff:(container,before,after)=>{rendered.push([before[0].content[0].text,after[0].content[0].text]);return 1},
 });
 vm.runInContext(handler,ctx);
 return {ctx,el,calls,rendered,notices,doc};
}

test('version comparison loads the base body on demand before showing the dialog',async()=>{
 const f=fixture(async(b,calls)=>{calls.push('load:'+b.id);return {...b,markdown:b.id,editor_document:f.doc(b.id==='v2'?'二':'一')}});
 await f.el.nodes['version-diff'].onclick();
 assert.deepEqual(f.calls,['load:v2','show']);
 assert.deepEqual(f.rendered,[['二','三']]);
 assert.match(f.el.nodes['diff-base'].innerHTML,/value="v2">上一稿/);
 assert.match(f.el.nodes['diff-summary'].textContent,/1 处内容或格式变化/);
 assert.deepEqual(f.notices,[]);
});

test('a slower earlier base cannot overwrite the latest comparison',async()=>{
 const pending=new Map();
 const f=fixture((b,calls)=>{calls.push('load:'+b.id);if(!pending.size&&b.id==='v2')return Promise.resolve({...b,editor_document:f.doc('二')});
  return new Promise(resolve=>pending.set(b.id,()=>resolve({...b,editor_document:f.doc(b.id==='v2'?'二':'一')})))});
 await f.el.nodes['version-diff'].onclick();
 const select=f.el.nodes['diff-base'];
 select.value='v1';select.onchange();
 select.value='v2';select.onchange();
 assert.equal(f.el.nodes['diff-summary'].textContent,'正在读取比较版本…');
 pending.get('v2')();await new Promise(r=>setImmediate(r));
 pending.get('v1')();await new Promise(r=>setImmediate(r));
 assert.deepEqual(f.rendered,[['二','三'],['二','三']],'the late v1 body is discarded');
});

test('a failed base load is shown in the dialog instead of a stale comparison',async()=>{
 let fail=false;
 const f=fixture(async b=>{if(fail)throw Error('报告已删除');return {...b,editor_document:f.doc('二')}});
 await f.el.nodes['version-diff'].onclick();
 fail=true;const select=f.el.nodes['diff-base'];select.value='v1';select.onchange();
 await new Promise(r=>setImmediate(r));
 assert.equal(f.el.nodes['diff-summary'].textContent,'比较版本读取失败：报告已删除');
 assert.equal(f.el.nodes['diff-body'].cleared,true);
 assert.equal(f.el.nodes['diff-next'].disabled,true);
});
