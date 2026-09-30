// Start from a previous report (#858): import it, then let the BriefLoop agent
// derive the brief contract in /discuss for the user to confirm.
import {$ as lookup} from './dom.js';

export function previousReportPrompt(title){
 return `/discuss 这是我们已经交付过的一期报告《${title}》（见附件）。请阅读它，反推这份周期报告的简报约定：读者是谁、读完要做什么决定；沿用的章节结构；数字单位、时间范围与来源等口径；刻意不写的内容。看不出来的列为未决项，不要猜。整理好后给出 briefloop-requirements，读者清楚时再给一个 briefloop-reader。`;
}

export function readerBlock(text){
 const match=/```briefloop-reader\s*([\s\S]*?)```/.exec(text||'');if(!match)return null;
 try{const value=JSON.parse(match[1].trim());if(!value||typeof value.name!=='string'||!value.name.trim())return null;
  return {name:value.name.trim(),decisions:typeof value.decisions==='string'?value.decisions:'',preferences:typeof value.preferences==='string'?value.preferences:''}}catch{return null}
}

export function previousReportUI({api,uploadPayload,getUploadLimits,openChat,notice,$=lookup}){
 async function importFile(file){
  const result=await api('import-previous',await uploadPayload(file,getUploadLimits(),{}));
  await openChat({text:previousReportPrompt(result.title),source_id:result.source_id});
  notice(result.tracked_changes?'已导入往期报告；其中的 Word 修订已记为改稿，下次学习时分类。确认后把请求发给 BriefLoop。':'已导入往期报告。确认后把请求发给 BriefLoop，它会反推简报约定。');
  return result;
 }
 function bind(){
  const input=$('previous-report-file');
  if(input)input.onchange=async e=>{const file=e.target.files[0];e.target.value='';if(!file)return;try{await importFile(file)}catch(error){notice(error.message,true)}};
 }
 return {bind,importFile};
}
