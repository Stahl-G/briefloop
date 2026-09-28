// Length preferences and their recorded source share one form/report projection.
// No color or sizing rules here: reuse the existing help and over-limit styles.
export function createLengthControls({$, language=()=> 'zh', notice=()=>{}}){
 let source=null,sourceMaximum=null,sourceLanguage=null;
 const mode=()=>$('length-mode')?.value==='strict'?'strict':'soft';
 const unit=()=>language()==='en'?'词':'字';
 const maximum=()=>Number($('max-words')?.value);
 function render(){
  const strict=mode()==='strict';
  if($('max-words-label'))$('max-words-label').textContent=strict?`严格${unit()}数上限`:`建议${unit()}数上沿`;
  const help=$('length-policy-help');
  if(help)help.textContent=strict&&sourceLanguage!==language()?'报告语言和计数单位已变化，请重新确认严格上限，不能沿用另一计数单位的原要求。':strict?'按你明确选择的上限检查；超出会说明差距，工作稿仍可保存、编辑和下载。必要内容与上限冲突时保留事实、引用和采用条件。':'篇幅是建议，超出不会阻断草稿提交；不为凑字数扩写，也不为压字数删掉必要内容。';
  const label=$('length-requirement-source');
  if(label){label.hidden=!strict;label.textContent=strict&&source?`要求来源：${source.text}`:''}
 }
 function select(){
  source=mode()==='strict'?{kind:'user_selection',text:`在报告需求中明确选择严格上限：${maximum()} ${unit()}`} : null;
  sourceMaximum=maximum();sourceLanguage=language();render();
 }
 function restore(requirements={}, {fromDiscussion=false}={}){
  const candidate=requirements.length_requirement;
  // A model-filled requirements block is not evidence of a user's UI click.
  const allowed=candidate&&['user_quote','user_selection'].includes(candidate.kind)&&String(candidate.text||'').trim()&&(!fromDiscussion||candidate.kind==='user_quote');
  const strict=requirements.length_mode==='strict'&&allowed;
  if($('length-mode'))$('length-mode').value=strict?'strict':'soft';
  source=strict?{kind:candidate.kind,text:candidate.text}:null;sourceMaximum=Number(requirements.max_words);sourceLanguage=language();
  // A discussion may give only a strict maximum. Do not submit the unrelated
  // old form target above it; explicit targets remain untouched for validation.
  const target=$('target-words');
  if(fromDiscussion&&strict&&requirements.target_words==null&&target&&Number.isFinite(maximum())&&maximum()>0&&Number(target.value)>maximum())target.value=String(maximum());
  if(requirements.length_mode==='strict'&&!allowed)notice('严格篇幅缺少用户原话来源，请在篇幅选项中明确选择。');
  render();
 }
 function read(){
  if(mode()!=='strict')return {length_mode:'soft',length_requirement:null};
  if(!source)throw Error('请明确选择严格篇幅或提供原始要求，不能由旧上限自动启用。');
  if(sourceLanguage!==language())throw Error('报告语言和计数单位已变化，请重新确认严格上限。');
  // A preset/language update is not proof the quoted user limit changed.
  // Direct numeric input already records its own explicit form choice above.
  if(sourceMaximum!==maximum())throw Error('篇幅数字已变化，请重新确认严格上限；不能沿用不同数字的原要求。');
  return {length_mode:'strict',length_requirement:{...source}};
 }
 function init(){
  $('length-mode')?.addEventListener('change',select);
  $('max-words')?.addEventListener('input',()=>{if(mode()==='strict')select()});
  $('report-language')?.addEventListener('change',render);
  $('requirements')?.addEventListener('reset',()=>{restore({})});
  render();
 }
 function renderBrief(element,stats,{dirty=false,language:reportLanguage='zh'}={}){
  element.classList.remove('over-limit');element.title='';
  if(!stats||!Number.isFinite(stats.count)){element.textContent='字数信息暂不可用';return}
  const reportUnit=reportLanguage==='en'?'词':'字',format=n=>new Intl.NumberFormat('zh-CN').format(n),strict=stats.length_mode==='strict';
  const parts=[`${dirty?'上次保存':'正文'} ${format(stats.count)} ${reportUnit}`];
  if(stats.target_words!=null)parts.push(`建议目标 ${format(stats.target_words)}`);
  if(stats.max_words!=null)parts.push(`${strict?'严格上限':'建议上沿'} ${format(stats.max_words)}`);
  if(stats.over_limit===true&&stats.max_words!=null){
   parts.push(`超出${strict?'严格上限':'建议'} ${format(stats.count-stats.max_words)} ${reportUnit}`);
   if(strict)element.classList.add('over-limit');
  }
  if(!strict)parts.push('篇幅建议');
  if(strict&&stats.length_requirement?.text)parts.push(`要求来源：${stats.length_requirement.text}`);
  if(dirty)parts.push('保存后更新');
  element.textContent=parts.join(' · ');element.title=(stats.rule||'')+' 普通工作稿可保存、编辑和下载。';
 }
 return {init,restore,read,render,renderBrief};
}
