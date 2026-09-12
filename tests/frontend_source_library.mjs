// A failed source keeps no usable original: the detail drawer must not show a
// dead "打开原件" link. The same control must follow original_url when present and
// open the page URL for web sources.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to));
const code=slice('function sourceOriginalLink','function showSource(')
 +slice('let sourceMediaId=null',"$('source-dialog').addEventListener('close'")
 +slice('function sourceState','function renderTemplatesPage');

function makeNode(){return {hidden:true,textContent:'',href:undefined,innerHTML:'',value:'',disabled:false,dataset:{},replaceChildren(){},append(){},removeAttribute(name){delete this[name]},setAttribute(){},querySelectorAll:()=>[]}}
function context(s){return {source:s,text:'',provenance:{},attachment:{status:'failed',image_path:null},original_url:null}}
function makeContext(record,response){
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,makeNode());return nodes.get(id)};
 const overview={innerHTML:'',querySelectorAll:()=>[]},usage={innerHTML:'',querySelectorAll:()=>[]};
 const document={querySelector:sel=>sel.includes('"overview"')?overview:sel.includes('"usage"')?usage:null,createElement:()=>({className:'',src:'',alt:'',append(){}})};
 const c=vm.createContext({$:$,esc:String,parse:JSON.parse,document,URL,Map,
  state:{sources:[record],runs:[],briefs:[]},
  api:async()=>response,action:async fn=>fn(),notice:()=>{},openBrief:()=>{},page:()=>{}});
 vm.runInContext(code,c);return c;
}

// Failed source, no retained original: no dead link anywhere.
const failed={id:'src_bad',status:'failed',name:'bad-page',url:null,error:'读取失败',needs_visual:false};
const c=makeContext(failed,context(failed));
await vm.runInContext("openSourceDrawer('src_bad',new Map())",c);
assert.equal(c.$('source-drawer-original').hidden,true,'a failed source must not link to a missing original');
assert.equal(c.$('source-drawer-original').href,undefined,'the original control has no href without an original');
assert.equal(c.$('source-drawer-source').hidden,true,'a source without a URL hides the page link');
console.log('PASS: a source without a retained original renders no dead original link');

// A web source opens its page; a retained original keeps its own control.
const web={id:'src_web',status:'failed',name:'Access Denied',url:'https://example.test/a',error:'x',needs_visual:false};
const cw=makeContext(web,context(web));
await vm.runInContext("openSourceDrawer('src_web',new Map())",cw);
assert.equal(cw.$('source-drawer-source').hidden,false);assert.equal(cw.$('source-drawer-source').href,'https://example.test/a');
assert.equal(cw.$('source-drawer-original').hidden,true);
const kept={id:'src_ok',status:'ready',name:'report.pdf',url:'https://example.test/report.pdf',needs_visual:false};
const ck=makeContext(kept,{source:kept,text:'body',provenance:{original_kind:'provider_response'},attachment:{media_type:'application/pdf'},original_url:'/api/source-original?id=src_ok'});
await vm.runInContext("openSourceDrawer('src_ok',new Map())",ck);
assert.equal(ck.$('source-drawer-original').hidden,false);assert.equal(ck.$('source-drawer-original').href,'/api/source-original?id=src_ok');
assert.equal(ck.$('source-drawer-original').textContent,'下载 Tavily 返回内容 ↗');
console.log('PASS: drawer original control follows original_url and provider_response kind');
