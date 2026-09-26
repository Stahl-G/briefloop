import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createReviewControls,reviewModeMetadataHTML} from '../frontend/review-controls.js';

function view({backend='codex',mode='standard',runtime=null,capability=true,allowWeb=true,saveError=false}={}){
 const standard=[{id:'codex',label:'Codex CLI'},{id:'opencode',label:'OpenCode CLI'},{id:'briefloop-native',label:'BriefLoop Agent'}];
 const settings={review_mode:mode,review_runtime:runtime};
 const state={settings,...(capability?{review_capability:{standard_review:standard,strict_review:standard.slice(2),review_choices:standard}}:{})};
 const calls=[],notices=[],nodes={};
 const node=id=>nodes[id]||(nodes[id]={value:'',textContent:'',hidden:false,disabled:false,innerHTML:'',listeners:{},addEventListener(kind,fn){this.listeners[kind]=fn}});
 const modeControl=node('mode-control'),modeHelp=node('mode-help');
 node('requirements').elements={fact_check:{checked:true,disabled:false},allow_web:{checked:allowWeb},writing_mode:{value:'internal_report'}};
 const controls=createReviewControls({$:node,getState:()=>state,backendValue:()=>backend,friendlyModel:s=>s,savedVersion:async()=>'version-1',notice:text=>notices.push(text),
  find:selector=>({'[data-testid="review-mode"]':modeControl,'[data-testid="review-mode-note"]':modeHelp,'[data-testid="review-effort-label"]':node('effort-label')})[selector],
  action:fn=>fn(),api:async(path,payload)=>{calls.push({path,payload});if(saveError)throw Error('offline');return path==='settings'?{...settings,...payload}:{};}});
 controls.init();controls.renderReviewRuntime();
 return {controls,node,state,calls,notices,modeControl,modeHelp,box:node('requirements').elements.fact_check};
}

test('ordinary review follows server capability for Codex and OpenCode; strict does not change engine or downgrade',async()=>{
 for(const backend of ['codex','opencode']){
  const v=view({backend});
  assert.equal(v.box.disabled,false);assert.equal(v.box.checked,true);
  assert.equal(v.node('review-start').textContent,'普通审阅');
  assert.match(v.modeHelp.textContent,/不承诺完全隔离宿主上下文/);
  v.modeControl.value='strict';await v.modeControl.listeners.change();
  assert.equal(v.state.settings.review_mode,'strict');assert.equal(v.state.settings.review_runtime,null);
  assert.equal(v.node('review-backend').value,'');
  assert.equal(v.box.disabled,true);assert.equal(v.node('review-start').disabled,true);
  assert.match(v.modeHelp.textContent,/不会自动改为普通审阅/);
  assert.match(v.node('review-backend').innerHTML,/不支持严格审阅/);
  await assert.rejects(v.controls.startReview(),/不支持严格审阅/);
  assert.deepEqual(v.calls,[{path:'settings',payload:{review_mode:'strict'}}]);
 }
});

test('explicit Native Reviewer permits strict review and freezes selected mode in the actual request',async()=>{
 const v=view({mode:'strict',runtime:{backend:'briefloop-native',model:'provider/model'}});
 assert.equal(v.box.disabled,false);assert.equal(v.node('review-start').disabled,false);
 await v.controls.startReview();
 assert.deepEqual(v.calls,[{path:'review',payload:{version_id:'version-1',review_mode:'strict'}}]);
 assert.deepEqual(v.notices,['已提交严格审阅']);
 const normal=view({runtime:{backend:'opencode',model:'provider/model'}});await normal.controls.startReview();
 assert.equal(normal.calls[0].payload.review_mode,'standard');
 assert.equal(view({backend:'opencode',allowWeb:false}).box.disabled,true);
});

test('failed strict-mode save stays visible and blocks review instead of submitting the old ordinary mode',async()=>{
 const v=view({saveError:true});v.modeControl.value='strict';await v.modeControl.listeners.change();
 assert.equal(v.modeControl.value,'strict');assert.equal(v.state.settings.review_mode,'standard');
 assert.match(v.node('review-runtime-status').textContent,/未保存：offline/);
 assert.equal(v.node('review-start').disabled,true);
 await assert.rejects(v.controls.startReview(),/尚未保存/);
 assert.equal(v.calls.length,1);
});

test('Codex saves its real effort and preserves same-backend provider settings; switching engines clears the draft model',async()=>{
 const v=view({runtime:{backend:'codex',model:'gpt-6-luna',reasoning_effort:'medium',model_provider:'configured-provider',service_tier:'fast'}});
 assert.equal(v.node('review-variant').value,'medium');
 assert.equal(v.node('effort-label').textContent,'推理强度');
 assert.match(v.node('review-model').placeholder,/模型 ID/);
 v.node('review-variant').value='high';await v.node('review-variant').listeners.change();
 assert.deepEqual(v.calls[0].payload.review_runtime,{backend:'codex',model:'gpt-6-luna',reasoning_effort:'high',model_provider:'configured-provider',service_tier:'fast'});
 v.controls.renderReviewRuntime();assert.equal(v.node('review-variant').value,'high');
 assert.match(v.node('review-runtime-summary').textContent,/high/);
 v.node('review-backend').value='opencode';await v.node('review-backend').listeners.change();
 assert.equal(v.node('review-model').value,'');assert.equal(v.node('review-variant').value,'');
 assert.equal(v.calls.length,1);assert.equal(v.node('review-start').disabled,true);
 await assert.rejects(v.controls.startReview(),/尚未保存/);
 v.node('review-model').value='provider/model';v.node('review-variant').value='high';await v.node('review-model').listeners.change();
 assert.deepEqual(v.calls[1].payload.review_runtime,{backend:'opencode',model:'provider/model',model_variant:'high'});
 assert.equal(v.node('review-start').disabled,false);
});

test('failed runtime save cannot submit the formerly saved backend',async()=>{
 const v=view({runtime:{backend:'codex',model:'gpt-6-luna'},saveError:true});
 v.node('review-backend').value='briefloop-native';await v.node('review-backend').listeners.change();
 v.node('review-model').value='provider/model';await v.node('review-model').listeners.change();
 assert.equal(v.state.settings.review_runtime.backend,'codex');
 assert.equal(v.node('review-backend').value,'briefloop-native');
 assert.match(v.node('review-runtime-status').textContent,/未保存：offline/);
 await assert.rejects(v.controls.startReview(),/尚未保存/);
 assert.ok(v.calls.every(call=>call.path==='settings'));
});

test('historical review mode is never inferred from current settings and unsafe backend text is escaped',()=>{
 assert.match(reviewModeMetadataHTML({review_mode:null}),/历史审阅（未记录模式）/);
 assert.doesNotMatch(reviewModeMetadataHTML({review_mode:null}),/严格审阅|普通审阅/);
 assert.match(reviewModeMetadataHTML({review_mode:'standard',review_backend:'<script>'}),/不承诺完全隔离.*&lt;script&gt;/);
 assert.match(reviewModeMetadataHTML({review_mode:'strict',review_backend:'briefloop-native'}),/强制只读访问冻结核查包/);
 const label=view().controls.backendLabel;
 const html=reviewModeMetadataHTML({review_mode:'strict',review_backend:'briefloop-native'},label);
 assert.match(html,/执行后端：BriefLoop Agent/);assert.doesNotMatch(html,/briefloop-native/);
});

test('capability not loaded does not invent engine support, and ordinary assessment is separate from review',()=>{
 const v=view({capability:false});assert.equal(v.box.disabled,false);assert.equal(v.node('review-capability-note').hidden,true);
 const source=fs.readFileSync(new URL('../frontend/assessment-panel.js',import.meta.url),'utf8');
 assert.match(source,/普通评分，不是独立审阅：当前配置未运行所选模式的 Reviewer/);
});
