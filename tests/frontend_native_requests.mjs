import test from 'node:test';
import assert from 'node:assert/strict';
import {createNativeRequests,questionAnswers,permissionAnswers,requestKind,requestMarkup} from '../frontend/native-requests.js';
import {permissionSelection} from '../frontend/runtime-permission-modes.js';
import {createComposerOptions} from '../frontend/composer-options.js';

test('multi-select, single-select and free text produce question answers without permission values',()=>{
 const questions=[{id:'scope',question:'范围',multiSelect:true,options:[{label:'政策'},{label:'市场'}]},{id:'reader',question:'读者',options:[{label:'管理层'}],allowCustom:false},{id:'note',question:'补充',options:[]}];
 assert.deepEqual(JSON.parse(JSON.stringify(questionAnswers(questions,{scope:{selected:['政策','市场'],custom:'其他领域'},reader:{selected:['管理层']},note:{custom:'保留原文限制'}}))),{scope:{answers:['政策','市场','其他领域']},reader:{answers:['管理层']},note:{answers:['保留原文限制']}});
 const reserved=questionAnswers([{id:'__proto__',question:'保留ID',options:[]}],JSON.parse('{"__proto__":{"custom":"回答"}}'));
 assert.equal(Object.getPrototypeOf(reserved),null);assert.deepEqual(reserved.__proto__,{answers:['回答']});
 const editor=[{id:'edit',question:'编辑',inputType:'editor',prefill:'\n  original\n'}];
 assert.deepEqual(questionAnswers(editor,{edit:{custom:'\n  changed\n'}}).edit,{answers:['\n  changed\n']});
 assert.deepEqual(questionAnswers(editor,{edit:{custom:''}}).edit,{answers:['']});
 assert.throws(()=>questionAnswers(questions,{scope:{selected:['政策']},reader:{custom:'allow_once'},note:{custom:'x'}}),/读者/);
 const request={id:'q',data:{kind:'question',questions,native_options:[{optionId:'allow_once',name:'允许本次'}]}};
 assert.equal(requestKind(request),'question');const html=requestMarkup(request);assert.match(html,/type="checkbox"/);assert.match(html,/type="radio"/);assert.match(html,/name="custom-2"/);assert.doesNotMatch(html,/允许本次|allow_once|data-permission-option/);
});

test('permissions use opaque option IDs and keep long command details collapsed',()=>{
 const request={id:'p',data:{kind:'permission',tool:'Bash',input:{description:'保存报告',command:'python /long/path/tool.py'},questions:[{id:'approval',question:'原始授权说明'}],native_options:[{optionId:'opaque-once',name:'允许一次',kind:'allow_once'},{optionId:'opaque-deny',name:'拒绝',kind:'reject_once'}]}};
 assert.deepEqual(permissionAnswers(request,'opaque-once'),{approval:{answers:['opaque-once']}});assert.throws(()=>permissionAnswers(request,'允许一次'),/不在当前请求/);
 const html=requestMarkup(request);assert.match(html,/<details class="native-permission-details"><summary>操作详情/);assert.match(html,/原始授权说明/);assert.ok(html.indexOf('python /long/path')>html.indexOf('<details'));assert.doesNotMatch(html,/提交回答|<input/);
});

test('a request submits only once across panels, including refresh failure after acceptance',async()=>{
 let resolve,calls=0;const pending=new Promise(done=>resolve=done);
 const ui=createNativeRequests({api:async(route,body)=>{calls++;assert.equal(route,'harness/answer');assert.equal(body.session_id,'s');await pending},getSessionId:()=> 's',refresh:async()=>{throw Error('offline')}});
 const request={id:'q'},answers={a:{answers:['yes']}};const first=ui.submit(request,answers);assert.equal(await ui.submit(request,answers),false);resolve();assert.equal(await first,true);assert.equal(await ui.submit(request,answers),false);assert.equal(calls,1);
});

test('failed answer submission can be retried without crossing request/session identity',async()=>{
 let attempts=0;const bodies=[];const ui=createNativeRequests({api:async(route,body)=>{bodies.push(body);if(++attempts===1)throw Error('offline')},getSessionId:()=> 's',refresh:async()=>{}});
 assert.equal(await ui.submit({id:'q'},{a:{answers:['x']}}),false);assert.equal(await ui.submit({id:'q'},{a:{answers:['x']}}),true);
 assert.deepEqual(bodies[0],bodies[1]);
});

test('Auto names/defaults come from the host and explicit restrictions survive',()=>{
 const native={default_mode:'workspace-write',modes:[{id:'workspace-write',name:'Auto · 工作区读写'},{id:'read-only',name:'只读'}]};
 assert.equal(permissionSelection(native).selected,'workspace-write');assert.equal(permissionSelection(native,'read-only').selected,'read-only');
 const acp={default_mode:'native',modes:[{id:'native',name:'跟随宿主'}]};assert.deepEqual(permissionSelection(acp),{modes:acp.modes,selected:'native'});assert.doesNotMatch(JSON.stringify(permissionSelection(acp)),/Auto|bypass/);
 assert.deepEqual(permissionSelection(native,'removed-mode'),{modes:native.modes,selected:'removed-mode',unavailable:true},'an unknown saved choice is not represented as Auto');
});

test('per-chat report choices preserve global settings and fast mode does not add a fact-check stage',()=>{
 const settings={research_tier:'deep',fact_checker:true},chat={reportOptions:{completion_mode:'fast',research_tier:'quick',fact_check:true}},web={checked:false};
 const ui=createComposerOptions({$:()=>web,getChat:()=>chat,getState:()=>({settings}),availability:()=>({enabled:true}),rememberDraft(){},openSettings(){},openReview(){}});
 assert.match(ui.instruction(),/completion_mode=fast，research_tier=quick，research_strategy=guided，fact_check=false/);web.checked=true;assert.match(ui.instruction(),/completion_mode=fast_web/);assert.deepEqual(settings,{research_tier:'deep',fact_checker:true});
});


test('goal-driven research is per-report and never grants network or changes workspace defaults',()=>{
 const chat={reportOptions:{research_strategy:'goal_driven'}},state={settings:{research_tier:'standard'}},web={checked:false};
 const ui=createComposerOptions({$:()=>web,getChat:()=>chat,getState:()=>state,availability:()=>({enabled:true})});
 assert.equal(ui.read().research_strategy,'goal_driven');assert.match(ui.instruction(),/research_strategy=goal_driven/);
 assert.equal(web.checked,false);assert.equal(state.settings.research_strategy,undefined);
 chat.reportOptions={};assert.equal(ui.read().research_strategy,'guided');
});
