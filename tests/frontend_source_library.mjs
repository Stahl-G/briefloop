import {sourceViewerUI} from '../frontend/source-viewer.js';
import {sourcesPageUI,sortLibrarySources} from '../frontend/sources-page.js';
// A failed source keeps no usable original: the detail drawer must not show a
// dead "打开原件" link. The same control must follow original_url when present and
// open the page URL for web sources.
import assert from 'node:assert/strict';

function makeNode(){return {hidden:true,textContent:'',href:undefined,innerHTML:'',value:'',disabled:false,dataset:{},replaceChildren(...children){this.children=children},append(){},scrollIntoView(){this.scrolled=true},removeAttribute(name){delete this[name]},setAttribute(){},querySelectorAll:()=>[]}}
function context(s){return {source:s,text:'',provenance:{},attachment:{status:'failed',image_path:null},original_url:null}}
function makeContext(record,response){
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,makeNode());return nodes.get(id)};
 const overview={innerHTML:'',querySelectorAll:()=>[]},usage={innerHTML:'',querySelectorAll:()=>[]};
 const document={querySelector:sel=>sel.includes('"overview"')?overview:sel.includes('"usage"')?usage:null,createElement:()=>makeNode(),createTextNode:text=>({textContent:text})};
 // The drawer builds nodes through the global document; each context is used right after it is made.
 globalThis.document=document;
 const c={$,state:{sources:[record],runs:[],briefs:[]},api:async()=>response};
 const api=(...args)=>c.api(...args),action=async fn=>fn();
 const viewer=sourceViewerUI({api,action,$});
 c.openSourceDrawer=sourcesPageUI({api,action,notice:()=>{},page:()=>{},openBrief:()=>{},markTab:()=>{},reportBrowsing:{sourceUsage(){}},getState:()=>c.state,
  showSourceMedia:viewer.showSourceMedia,drawerSourceMediaView:viewer.drawerSourceMediaView,applySourceLinks:viewer.applySourceLinks,sourceProvenanceRows:viewer.sourceProvenanceRows,$}).openSourceDrawer;
 return c;
}

// Failed source, no retained original: no dead link anywhere.
const failed={id:'src_bad',status:'failed',name:'bad-page',url:null,error:'读取失败',needs_visual:false};
const c=makeContext(failed,context(failed));
await c.openSourceDrawer('src_bad',new Map());
assert.equal(c.$('source-drawer-original').hidden,true,'a failed source must not link to a missing original');
assert.equal(c.$('source-drawer-original').href,undefined,'the original control has no href without an original');
assert.equal(c.$('source-drawer-source').hidden,true,'a source without a URL hides the page link');
console.log('PASS: a source without a retained original renders no dead original link');

// A web source opens its page; a retained original keeps its own control.
const web={id:'src_web',status:'failed',name:'Access Denied',url:'https://example.test/a',error:'x',needs_visual:false};
const cw=makeContext(web,context(web));
await cw.openSourceDrawer('src_web',new Map());
assert.equal(cw.$('source-drawer-source').hidden,false);assert.equal(cw.$('source-drawer-source').href,'https://example.test/a');
assert.equal(cw.$('source-drawer-original').hidden,true);
const kept={id:'src_ok',status:'ready',name:'report.pdf',url:'https://example.test/report.pdf',needs_visual:false};
const ck=makeContext(kept,{source:kept,text:'body',provenance:{original_kind:'provider_response'},attachment:{media_type:'application/pdf'},original_url:'/api/source-original?id=src_ok'});
await ck.openSourceDrawer('src_ok',new Map());
assert.equal(ck.$('source-drawer-original').hidden,false);assert.equal(ck.$('source-drawer-original').href,'/api/source-original?id=src_ok');
assert.equal(ck.$('source-drawer-original').textContent,'下载 Tavily 返回内容 ↗');
console.log('PASS: drawer original control follows original_url and provider_response kind');

const textSource={id:'src_text',name:'text.txt',status:'ready',hash:'saved-hash'};
const ct=makeContext(textSource,{source:textSource,text:'首行\x1c<script>needle</script>\r\n尾行',provenance:{},attachment:{},original_url:null});
const match={source_hash:'saved-hash',hits:[{start_line:2}]};
await ct.openSourceDrawer('src_text',new Map(),match);
const marked=ct.$('source-drawer-body').children[1];
assert.equal(marked.textContent,'<script>needle</script>');assert.equal(marked.scrolled,true);
match.source_hash='old-hash';await ct.openSourceDrawer('src_text',new Map(),match);
assert.match(ct.$('source-drawer-body').textContent,/来源已变化/);
const requests=[];ct.api=()=>new Promise(resolve=>requests.push(resolve));
const a=ct.openSourceDrawer('src_text',new Map());
const b=ct.openSourceDrawer('src_text',new Map());
requests[1]({source:textSource,text:'最新正文',provenance:{},attachment:{}});await b;
requests[0]({source:textSource,text:'旧正文',provenance:{},attachment:{}});await a;
assert.equal(ct.$('source-drawer-body').textContent,'最新正文');
console.log('PASS: source hit opens the verified original line as text and stale same-source reads cannot replace it');

// Newest ingestion first, including same-second rows; do not mutate report input.
const records=[{id:'old',created:'2026-10-02T01:00:00Z'},{id:'new-a',created:'2026-10-09T00:00:00Z'},{id:'new-b',created:'2026-10-09T07:00:00+07:00'},{id:'missing'}];
assert.deepEqual(sortLibrarySources(records).map(s=>s.id),['new-b','new-a','old','missing']);
assert.deepEqual(sortLibrarySources(records,'oldest').map(s=>s.id),['old','new-a','new-b','missing']);
assert.deepEqual(records.map(s=>s.id),['old','new-a','new-b','missing']);
console.log('PASS: source library sorts by ingestion time without changing report source order');
