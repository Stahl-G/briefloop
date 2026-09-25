// OfficeCLI surfaces stay honest and optional: detection is not a passed check,
// and with the switch off every entry point matches the pre-tool behaviour.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {beginPanel,updatePanel} from '../frontend/report-panels.js';
import {createAssessmentPanel} from '../frontend/assessment-panel.js';
import {createOfficeTools,officeIssueLine} from '../frontend/office-tools.js';

const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
const deliverySource=fs.readFileSync(new URL('../frontend/delivery.js',import.meta.url),'utf8');

function element(){
 return {children:[],textContent:'',innerHTML:'',className:'',src:'',alt:'',hidden:false,disabled:false,checked:false,onclick:null,value:'1',dataset:{},
  append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},remove(){this.removed=true}};
}
function makeDom(){
 const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{...element(),id,opened:false,showModal(){this.opened=true},close(){this.opened=false},get open(){return this.opened}});return nodes.get(id)};
 return {$,nodes,document:{createElement:element}};
}
function makeTools(officeState,api){
 const dom=makeDom();
 const tools=createOfficeTools({api:api||(async()=>({})),action:async fn=>fn(),$:dom.$,esc:String,parse:s=>JSON.parse(s||'{}'),getState:()=>({office:officeState}),notice(){}});
 return {tools,dom};
}

test('settings card separates binary detection from check availability',()=>{
 const {tools,dom}=makeTools({installed:true,enabled:false});
 tools.renderSettingsCapability({id:'officecli',name:'OfficeCLI',installed:false,path:null,version:null,enabled:false,available:false,diagnostic:'未检测到 OfficeCLI；已查找 PATH、~/.opencode/bin、/opt/homebrew/bin 等常见安装目录'});
 assert.match(dom.$('settings-officecli-status').textContent,/未检测到/);
 assert.equal(dom.$('settings-officecli').disabled,true,'switch cannot be enabled without the binary');
 assert.equal(dom.$('settings-officecli').checked,false);
 assert.doesNotMatch(dom.$('settings-officecli-status').textContent,/已接入|调用通过/);

 tools.renderSettingsCapability({id:'officecli',name:'OfficeCLI',installed:true,path:'/usr/local/bin/officecli',version:'1.0.152',enabled:true,available:true,diagnostic:''});
 assert.match(dom.$('settings-officecli-status').textContent,/版本 1\.0\.152/);
 assert.equal(dom.$('settings-officecli').disabled,false);
 assert.equal(dom.$('settings-officecli').checked,true);
 assert.match(dom.$('settings-officecli-status').textContent,/不代表质检已经运行或可用/);
 assert.doesNotMatch(dom.$('settings-officecli-status').textContent,/已接入|调用通过/);

 tools.renderSettingsCapability({installed:true,version:'1.0.152',enabled:false});
 assert.match(dom.$('settings-officecli-status').textContent,/开关当前关闭/);
 assert.equal(dom.$('settings-officecli').checked,false);

 // Constant-shape state hint seeds the same control before discovery runs.
 const off=makeTools(undefined);
 off.tools.syncSettingsToggle();
 assert.equal(off.dom.$('settings-officecli').disabled,true);
 const on=makeTools({installed:true,enabled:true});
 on.tools.syncSettingsToggle();
 assert.equal(on.dom.$('settings-officecli').disabled,false);
 assert.equal(on.dom.$('settings-officecli').checked,true);
});

test('preview dialog stays closed while the switch is off',()=>{
 for(const officeState of [undefined,{installed:false,enabled:false},{installed:true,enabled:false}]){
  const {tools,dom}=makeTools(officeState);
  assert.equal(tools.officeEnabled(),false);
  tools.openPreview({job_id:'job_1'});
  assert.equal(dom.$('office-preview-dialog').open,false,'no modal without the switch');
 }
});

test('preview dialog renders returned pages and reports partial results',async()=>{
 const calls=[];
 const pages=[{page:1,url:'/api/office-image?digest='+'a'.repeat(64)+'&page=1',width:1600,height:1200}];
 const responses=[{target:{kind:'export',id:'job_1',name:'report.docx'},tool:'officecli',tool_version:'1.0.152',cached:false,pages,incomplete:false,reason:null},
  {target:{kind:'export',id:'job_1',name:'report.docx'},tool:'officecli',tool_version:'1.0.152',cached:true,pages,incomplete:true,reason:'请求时间预算用尽，仅返回已完成页面'}];
 const api=async(route,body)=>{calls.push({route,body});return responses[calls.length-1]};
 globalThis.document={createElement:element};
 const {tools,dom}=makeTools({installed:true,enabled:true},api);
 tools.openPreview({job_id:'job_1'});
 const dialog=dom.$('office-preview-dialog');
 assert.equal(dialog.open,true);
 assert.equal(dom.$('office-preview-images').children.length,0,'previous images cleared on open');

 dom.$('office-preview-pages').value='1,3';
 await dom.$('office-preview-render').onclick();
 assert.deepEqual(calls[0],{route:'office-preview',body:{job_id:'job_1',pages:[1,3]}});
 assert.equal(dom.$('office-preview-render').disabled,false,'button re-enabled after the run');
 const [figure]=dom.$('office-preview-images').children;
 const [caption,image]=figure.children;
 assert.equal(caption.textContent,'第 1 页');
 assert.equal(image.src,pages[0].url);
 assert.equal(image.className,'source-preview-image');
 assert.doesNotMatch(dom.$('office-preview-note').textContent,/部分页/);

 await dom.$('office-preview-render').onclick();
 assert.match(dom.$('office-preview-note').textContent,/部分页未渲染/);
 assert.equal(dom.$('office-preview-images').children.length,1,'only completed pages listed');
 delete globalThis.document;
});

test('source view renders office pages through the shared page input',async()=>{
 const calls=[];
 const api=async(route,body)=>{calls.push({route,body});return {target:{kind:'source',id:'src_1',name:'a.xlsx'},tool:'officecli',tool_version:'1.0.152',cached:false,pages:[{page:2,url:'/api/office-image?digest='+'b'.repeat(64)+'&page=2',width:800,height:600}],incomplete:false,reason:null}};
 globalThis.document={createElement:element};
 const {tools}=makeTools({installed:true,enabled:true},api);
 const view={pages:{value:'2'},images:element(),note:{textContent:''},render:element()};
 const trigger=element();
 await tools.previewSourceInto('src_1',view,trigger);
 assert.deepEqual(calls[0],{route:'office-preview',body:{source_id:'src_1',pages:[2]}});
 assert.equal(view.images.children.length,1);
 assert.equal(view.images.children[0].children[1].src,'/api/office-image?digest='+'b'.repeat(64)+'&page=2');
 assert.equal(trigger.disabled,false);
 await assert.rejects(tools.previewSourceInto('src_1',{...view,pages:{value:'9,1,2,3,4'}},trigger),/1–4 页/);
 delete globalThis.document;
});

test('word export rows add office summary and preview only when enabled',()=>{
 const job={id:'job_1',kind:'export_docx',status:'complete',payload:JSON.stringify({version_id:'v1',run_id:'r1'}),result:JSON.stringify({download_url:'/saved.docx',office:{tool:'officecli',tool_version:'1.0.152',validate:{status:'ok',summary:'Validation passed: no errors found.'},issues:{status:'ok',count:0,items:[]}}})};
 const code=source.slice(source.indexOf('function renderWordExports(){'),source.indexOf('function renderWordExports(){')+source.slice(source.indexOf('function renderWordExports(){')).indexOf('\n}')+2);
 const setup=extra=>{
  let html='';
  const buttons=extra.buttons||[];
  const box={...element(),dataset:{},set innerHTML(value){this._html=value},get innerHTML(){return this._html||''},querySelectorAll:()=>buttons};
  const $=id=>id==='word-exports'?box:element();
  const c=vm.createContext({$,esc:String,parse:s=>JSON.parse(s||'{}'),statuses:{complete:'已完成'},document:{createElement:element},...extra,state:extra.state,current:extra.current});
  vm.runInContext(code,c);return {c,box,get html(){return box._html||''}};
 };

 // No office module and no state key: identical to today's markup.
 const plain=setup({state:{jobs:[job]},current:{id:'v1',run_id:'r1'}});
 vm.runInContext('renderWordExports()',plain.c);
 assert.match(plain.html,/下载工作稿 Word/);
 assert.doesNotMatch(plain.html,/OfficeCLI|data-office-preview/);
 assert.equal(plain.box.children.length,0,'no enablement hint without the state key');

 // Enabled: summary line, preview button and its binding appear.
 const opened=[];
 const enabled=setup({state:{jobs:[job],office:{installed:true,enabled:true}},current:{id:'v1',run_id:'r1'},
  buttons:[{dataset:{officePreview:'job_1'},onclick:null}],
  office:{officeEnabled:()=>true,officeCheckSummary:()=>'OfficeCLI 质检通过',openPreview:target=>opened.push(target)}});
 vm.runInContext('renderWordExports()',enabled.c);
 assert.match(enabled.html,/OfficeCLI 质检通过/);
 assert.match(enabled.html,/data-office-preview="job_1"/);
 const bound=enabled.box.querySelectorAll()[0];
 assert.equal(typeof bound.onclick,'function');
 bound.onclick();
 assert.equal(JSON.stringify(opened),JSON.stringify([{job_id:'job_1'}]));

 // Installed but disabled: no preview button, one dismissible hint instead.
 const hintContext=setup({state:{jobs:[job],office:{installed:true,enabled:false}},current:{id:'v1',run_id:'r1'},buttons:[],
  office:{officeEnabled:()=>false,officeCheckSummary:()=>'OfficeCLI 质检通过',openPreview(){throw Error('must not open')}}});
 vm.runInContext('renderWordExports()',hintContext.c);
 assert.doesNotMatch(hintContext.html,/data-office-preview/);
 assert.equal(hintContext.box.children.length,1,'enablement hint appended');
 const hint=hintContext.box.children[0];
 assert.match(hint.textContent,/检测到 OfficeCLI，可在设置中开启导出质检/);
 const [close]=hint.children;
 close.onclick();
 assert.equal(hint.removed,true);
 vm.runInContext('renderWordExports()',hintContext.c);
 assert.equal(hintContext.box.children.length,1,'dismissed hint never comes back in this session');
});

test('audit dialog resets the render checkbox and sends the explicit choice',async()=>{
 const openCode=deliverySource.slice(deliverySource.indexOf('function openAuditBundle(releaseId){'),deliverySource.indexOf('async function submitAuditBundle()'));
 const submitCode=deliverySource.slice(deliverySource.indexOf('async function submitAuditBundle()'),deliverySource.indexOf('function init(){'));
 const release={id:'rel_1',status:'released',created:'2026-01-01T00:00:00Z',change_type:'initial'};
 const makeContext=(officeEnabled,notices)=>{
  const nodes=new Map();
  const $=id=>{if(!nodes.has(id))nodes.set(id,{...element(),id,opened:false,showModal(){this.opened=true},close(){this.opened=false},get open(){return this.opened}});return nodes.get(id)};
  $('audit-source-list').querySelectorAll=()=>[{dataset:{auditSource:'src_1'},value:'metadata'}];
  const bodies=[];
  const c=vm.createContext({$,esc:String,notice:m=>notices.push(m),displayDate:()=>'时间',changeTypeLabel:{initial:'首次交付'},
   releaseView:{data:{releases:[release]}},auditTarget:null,office:{officeEnabled},
   api:async(route,body)=>{bodies.push({route,body});return {}}});
  return {c,nodes,bodies,$};
 };
 // Disabled: the row stays hidden and any stale checkbox is cleared.
 let ctx=makeContext(()=>false,[]);
 ctx.$('audit-office-render').checked=true;
 vm.runInContext(openCode,ctx.c);vm.runInContext('openAuditBundle("rel_1")',ctx.c);
 assert.equal(ctx.nodes.get('audit-office-render').checked,false,'residual check never leaks into a new open');
 assert.equal(ctx.nodes.get('audit-office-row').hidden,true,'row hidden while the switch is off');

 // Enabled: row visible, still reset to false on every open.
 ctx=makeContext(()=>true,[]);
 ctx.$('audit-office-render').checked=true;ctx.$('audit-office-row').hidden=false;
 vm.runInContext(openCode,ctx.c);vm.runInContext('openAuditBundle("rel_1")',ctx.c);
 assert.equal(ctx.$('audit-office-render').checked,false);
 assert.equal(ctx.$('audit-office-row').hidden,false);

 // The payload always carries the explicit choice; default matches today's request.
 vm.runInContext(submitCode,ctx.c);
 ctx.c.auditTarget={id:'rel_9'};
 await vm.runInContext('submitAuditBundle()',ctx.c);
 assert.equal(JSON.stringify(ctx.bodies[0].body),JSON.stringify({release_id:'rel_9',source_permissions:{src_1:'metadata'},include_office_render:false}));
 ctx.$('audit-office-render').checked=true;
 await vm.runInContext('submitAuditBundle()',ctx.c);
 assert.equal(ctx.bodies[1].body.include_office_render,true);
});

test('delivery checks show office tool output as observation, or nothing',async()=>{
 const base={broken_refs:[],numbers:{total:1,checked:1,matched:1,status:'checked_bindings',unmatched:[],skipped:[],occurrence_review:null},
  export:{escaped_bold:false,figure_error:null,figure_markers:[]},layout:null};
 const render=async office=>{
  let html='';
  const box={dataset:{},isConnected:true,get innerHTML(){return html},set innerHTML(value){html=value}};
  const assessment={querySelector:()=>box,prepend(){}};
  const panel=createAssessmentPanel({api:async()=>({...base,...(office?{office}:{})}),action:async fn=>fn(),notice(){},
   $:()=>assessment,esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
   parse:s=>JSON.parse(s||'{}'),getState:()=>({assessments:[],jobs:[],sources:[]}),getCurrent:()=>({id:'v1'}),
   getEditor:()=>null,isDirty:()=>false,bindSources(){},applyHighlightState(){},readerHighlights:()=>[],toEditor:x=>x,
   beginPanel,updatePanel,reviewPending:()=>false});
  await panel.renderDeliveryChecks();return html;
 };
 // Absent: response without the key renders exactly like today.
 assert.doesNotMatch(await render(undefined),/OfficeCLI/);
 // Tool failure is an observation, never a gate.
 assert.match(await render({validate:{status:'error',reason:'officecli 执行超时（120 秒）'},issues:{status:'error',reason:'请求时间预算用尽'}}),/tag error">OfficeCLI：质检未完成：officecli 执行超时（120 秒）；不影响导出/);
 // Found issues list a bounded summary.
 const issuesHTML=await render({validate:{status:'ok',summary:'Validation passed: no errors found.'},issues:{status:'issues',count:2,items:[{message:'标题样式不一致 <b>'},{message:'表格宽度溢出'},{message:'第三条不应显示为被截断计数'}]}});
 assert.match(issuesHTML,/OfficeCLI：质检发现 2 项/);
 assert.match(issuesHTML,/标题样式不一致 &lt;b&gt;/);
 assert.match(issuesHTML,/不阻断交付/);
 // All clear states it is tool output only.
 assert.match(await render({validate:{status:'ok',summary:'Validation passed: no errors found.'},issues:{status:'ok',count:0,items:[]}}),/OfficeCLI：结构校验与质检通过（工具输出，不阻断交付）</);
 // Unknown issue shapes degrade to a placeholder, never crash.
 assert.equal(officeIssueLine({}), '未提供摘要');
});
