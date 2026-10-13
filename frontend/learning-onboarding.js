/** First-use learning choice shares the existing settings authorization gate. */
export function createLearningOnboarding({api,$,getState,refresh,describePlan}){
 let choice=null,shownPlan=null,mounted=false;
 function mount(anchor){
  anchor.insertAdjacentHTML('beforebegin',
   '<div id="welcome-learning"><label class="check"><input id="welcome-learning-choice" type="checkbox">改稿后自动学习，供之后的报告使用</label>'+
   '<p id="welcome-learning-status" class="help" role="status"></p>' +
   '<details><summary id="welcome-learning-budget-summary">学习调用上限</summary>'+
   '<p id="welcome-learning-budget" class="help"></p></details></div>');
  $('welcome-learning-choice').onchange=event=>{choice=event.target.checked;sync()};
  mounted=true;
  sync();
 }
 function sync(){
  if(!mounted)return;
  const state=getState(),settings=state?.settings||{},authorization=state?.learning_authorization;
  const checked=choice===null?settings.auto_learn===true:choice;
  $('welcome-learning-choice').checked=checked;
  shownPlan=authorization?.plan||null;
  const selected=!!settings.model&&!settings.model_selection_required;
  $('welcome-learning-budget').textContent=selected&&shownPlan?describePlan(shownPlan):'选择模型后显示实际调用上限。';
  $('welcome-learning-budget-summary').textContent=selected&&shownPlan?
   `每轮最多 ${shownPlan.trial_generations_per_round} 次试写，普通 ${shownPlan.rounds} 轮；`+
   `明确长期要求最多 ${shownPlan.rounds_with_explicit_requirement} 轮，共 ${shownPlan.max_trial_generations} 次 · 调用明细`:
   '学习调用上限';
  const authorized=authorization?.state==='authorized';
  $('welcome-learning-status').textContent=!checked?'自动学习关闭；改稿和反馈仍会保存。':
   authorized?'自动学习已开启，可随时在报告选项中关闭。':
   '尚未开启。点击“开始使用并开启自动学习”会确认下方调用上限，之后改稿或评论将自动调用模型验证经验。';
  $('welcome-start').textContent=checked&&!authorized?'开始使用并开启自动学习 →':'开始使用 →';
 }
 async function confirmStart(){
  const checked=$('welcome-learning-choice').checked;
  if(!checked){
   await api('settings',{auto_learn:false});
   await refresh();
   choice=null;
   sync();
   return;
  }
  if(!shownPlan||!getState()?.settings?.model)throw Error('请先选择模型并查看学习调用上限');
  const confirmed=shownPlan;
  const result=await api('settings',{auto_learn:true,confirm_learning_rounds:confirmed.rounds,confirm_plan:confirmed.fingerprint});
  if(!result.auto_learn||result.auto_learn_authorized_rounds<result.k||
   result.auto_learn_authorized_plan!==confirmed.scope_fingerprint)throw Error('自动学习尚未获得当前计划的授权，请重新查看调用上限');
  await refresh();
  if(getState()?.learning_authorization?.state!=='authorized')throw Error('学习配置已变化，请重新查看调用上限');
  choice=null;
  sync();
 }
 return {mount,sync,confirmStart};
}
