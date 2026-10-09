// Compact report controls: same component in the composer and creation form.
import {$ as lookup} from './dom.js';
import {factCheckAvailability} from './fact-check-availability.js';
export function compactReportControlsUI({api,action,refresh,notice,page,showSettings,settingsView,setAutoLearn,chatBackendChoice,backendValue,runtimeName,sessionBudget,getState,getComposerOptions,pendingReview,$=lookup}){
 function compactReportInstruction(){return getComposerOptions().instruction()}

 function compactReportControls(where){
  const row=document.createElement('div');row.className='compact-report-options';row.dataset.reportOptions=where;
  row.innerHTML='<label>研究 <select data-option="tier" aria-label="研究深度"><option value="quick">快速</option><option value="standard" selected>标准</option><option value="deep">深度研究</option></select></label><label title="生成后联网补查关键主张，增加时间与用量"><input type="checkbox" data-option="fact">事实核查</label><label title="改稿或评论后在后台启动学习验证，会调用模型；已启用的技能不受这个开关影响"><input type="checkbox" data-option="learn">自动学习</label><label><input type="number" min="1" max="20" value="1" data-option="rounds" aria-label="自动学习最多轮数">轮</label><details><summary aria-label="更多报告选项">更多 ⋯</summary><div class="compact-options-menu"><label>目标用时 <select data-option="timeout"><option value="10">约 10 分钟</option><option value="30">30 分钟</option><option value="60" selected>60 分钟</option><option value="120">120 分钟</option><option value="0">不设目标</option></select></label><label>Scout 并发 <input data-option="scouts" type="number" min="1" max="16" value="4"></label></div></details><p class="compact-fact-note" data-fact-note role="status" hidden><span data-fact-reason></span> <button type="button" data-fact-action></button></p>';
  row.querySelector('[data-fact-action]').onclick=()=>{
   const target=compactFactAvailability(where).target;
   if(target==='network'){if(where==='chat'){showSettings();settingsView('execution')}else{const control=$('requirements').elements.allow_web;control.scrollIntoView({block:'center'});control.focus()}}
   if(target==='review'){page('setup');const panel=document.querySelector('.review-runtime-settings');panel.open=true;panel.scrollIntoView({block:'center'})}
  };
  row.addEventListener('change',event=>action(async()=>{
   const key=event.target.dataset.option;if(!key)return;const value=event.target.type==='checkbox'?event.target.checked:event.target.value;
   if(key==='learn'){try{await setAutoLearn(value);await refresh()}finally{syncCompactReportControls()}$('auto-learn').checked=getState().learning_authorization?.state==='authorized';return}
   if(key==='tier'){$('research-tier').value=value;$('research-tier').dispatchEvent(new Event('change'))}
   if(key==='fact')$('requirements').elements.fact_check.checked=value;
   const field={tier:'research_tier',fact:'fact_checker',learn:'auto_learn',rounds:'k',timeout:'timeout_minutes',scouts:'max_parallel'}[key];
   const setting=['rounds','timeout','scouts'].includes(key)?Number(value):value;
   await api('settings',{[field]:setting});getState().settings[field]=setting;
   if(key==='learn')$('auto-learn').checked=value;if(key==='rounds')$('rounds').value=value;if(key==='timeout')$('timeout-minutes').value=value;
   syncCompactReportControls();
  }));return row;
 }
 function compactFactAvailability(where){
  const state=getState();
  return factCheckAvailability({allowWeb:where==='chat'?$('chat-allow-web').checked:$('requirements').elements.allow_web.checked,
   backend:where==='chat'?chatBackendChoice():backendValue(),reviewRuntime:state.settings.review_runtime,
   reviewMode:state.settings.review_mode||'standard',capability:state.review_capability,pendingReview:pendingReview(),backendLabel:runtimeName});
 }
 function syncCompactReportControls(){
  const state=getState();if(!state?.settings)return;
  getComposerOptions().sync();
  document.querySelectorAll('[data-report-options]').forEach(row=>{
   const availability=compactFactAvailability(row.dataset.reportOptions);
   for(const [key,value] of Object.entries({tier:row.dataset.reportOptions==='setup'?$('research-tier').value:state.settings.research_tier||'standard',fact:row.dataset.reportOptions==='setup'?$('requirements').elements.fact_check.checked:state.settings.fact_checker,learn:state.learning_authorization?.state==='authorized',rounds:state.settings.k,timeout:state.settings.timeout_minutes,scouts:state.settings.max_parallel})){
    const input=row.querySelector(`[data-option="${key}"]`);if(document.activeElement===input)continue;if(input.type==='checkbox')input.checked=!!value;else input.value=value;
   }
   const fact=row.querySelector('[data-option="fact"]');fact.disabled=!availability.enabled;if(!availability.enabled)fact.checked=false;
   fact.title=availability.reason;
   const note=row.querySelector('[data-fact-note]');note.hidden=availability.enabled;
   note.querySelector('[data-fact-reason]').textContent=availability.reason;
   const action=note.querySelector('[data-fact-action]');action.textContent=availability.action;
  });
  const reports=$('max-reports');if(reports&&document.activeElement!==reports&&!reports.dataset.editing)reports.value=state.settings.max_reports||4;
  sessionBudget.sync();
 }
 function mountCompactReportControls(){
  const paramsPanel=$('composer-params-panel')||$('chat-input').closest('form');
  getComposerOptions().mount(paramsPanel);
  const setup=compactReportControls('setup');const tier=$('research-tier').closest('label');tier.before(setup);tier.classList.add('compact-legacy-option');tier.hidden=true;
  if(tier.nextElementSibling?.classList.contains('help'))tier.nextElementSibling.hidden=true;
  const fact=$('requirements').elements.fact_check.closest('label');fact.classList.add('compact-legacy-option');fact.hidden=true;if(fact.nextElementSibling?.classList.contains('help'))fact.nextElementSibling.hidden=true;
  const label=document.createElement('label');label.textContent='同时生成报告数 ';const input=document.createElement('input');input.id='max-reports';input.type='number';input.min='1';input.max='16';input.value='4';label.append(input);$('settings-view-execution').append(label);
  input.oninput=()=>{input.dataset.editing='1'};input.onchange=()=>{const value=Number(input.value);return action(async()=>{const result=await api('settings',{max_reports:value});getState().settings.max_reports=result.max_reports;delete input.dataset.editing;notice('并发数已保存；已运行报告继续，新任务按空位开始')})};
  const save=document.createElement('button');save.type='button';save.className='outline';save.textContent='保存并发数';save.onclick=()=>input.onchange();label.append(save);sessionBudget.mount(label);
  for(const control of [$('chat-allow-web'),$('requirements').elements.allow_web])control.addEventListener('change',syncCompactReportControls);
  syncCompactReportControls();
 }
 return {compactReportInstruction,compactFactAvailability,syncCompactReportControls,mountCompactReportControls};
}
