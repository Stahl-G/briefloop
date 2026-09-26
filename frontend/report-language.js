// Report body language on the setup form. The interface and internal records stay
// Chinese; the language only changes the report text, its length presets and the
// unit shown for its length. Presets mirror models.py (English counts words).
import {$} from './dom.js';

export const LENGTH_PRESETS={
 zh:{quick:[350,500],compact:[800,1000],balanced:[1500,2000],detailed:[2000,2500]},
 en:{quick:[250,350],compact:[500,650],balanced:[1000,1300],detailed:[1300,1600]},
};
export const DEEP_LENGTH={zh:[10000,12000],en:[6500,8000]};
export const INDUSTRY_LENGTH={zh:[5000,5500],en:[3200,3600]};
const EXTENT_LABELS={quick:'速览',compact:'简短',balanced:'标准',detailed:'详细'};

// Saved runs before the language enum hold free text ("中文", "English").
export function reportLanguage(value){
 const text=String(value??'').trim().toLowerCase().replace(/_/g,'-');
 if(['','zh','中文','汉语','简体中文','简体','chinese','simplified chinese'].includes(text)||text.startsWith('zh-'))return 'zh';
 if(['en','english','英文','英语'].includes(text)||text.startsWith('en-')||text.startsWith('english'))return 'en';
 return null;
}
export const lengthUnit=language=>reportLanguage(language)==='en'?'词':'字';
export function runLanguage(state,runId){
 const run=(state?.runs||[]).find(r=>r.id===runId);
 try{return reportLanguage(JSON.parse(run?.requirements||'{}').language)||'zh'}catch{return 'zh'}
}
export function presetLabel(language,extent){
 const [target,maximum]=LENGTH_PRESETS[language][extent],unit=lengthUnit(language),n=v=>new Intl.NumberFormat('zh-CN').format(v);
 return extent==='quick'?`${EXTENT_LABELS[extent]} · ${n(target)}–${n(maximum)} ${unit}`:`${EXTENT_LABELS[extent]} · ${n(target)} ${unit} / 最多 ${n(maximum)}`;
}

export function reportLanguageUI({notice,onChange}){
 const select=()=>$('report-language');
 const current=()=>reportLanguage(select()?.value)||'zh';
 function render(){
  const language=current(),unit=lengthUnit(language);
  for(const option of $('length-preset')?.options||[])if(LENGTH_PRESETS[language][option.value])option.textContent=presetLabel(language,option.value);
  if($('target-words-label'))$('target-words-label').textContent=`目标${unit}数`;
  if($('max-words-label'))$('max-words-label').textContent=`${unit}数上限`;
  // Word covers print the period and organization as typed.
  const period=$('requirements')?.elements?.period;
  if(period)period.placeholder=language==='en'?'例如：August 2026（封面原样显示，英文报告请用英文填写）':'例如：本周，或指定日期';
 }
 function changed(){render();onChange?.(current())}
 // Restoring saved requirements normalizes legacy values without resetting lengths.
 function restore(value){const el=select();if(!el)return;el.value=reportLanguage(value)||'zh';render()}
 // A built-in layout is written for one language; picking it sets the report language.
 function syncTemplate(template){
  // An upload or cleared selection has no language preference. Saved runs still
  // use reportLanguage's Chinese fallback, but an absent hint must not reset a choice.
  if(!String(template?.language_hint??'').trim())return;
  const hint=reportLanguage(template?.language_hint);
  if(!hint||!select()||hint===current())return;
  select().value=hint;changed();
  notice?.(hint==='en'?'所选模板为英文版式，报告语言已改为英文，可在表单中再调整':'所选模板为中文版式，报告语言已改为中文，可在表单中再调整');
 }
 function init(){select()?.addEventListener('change',changed);render()}
 return {init,current,restore,syncTemplate,render};
}
