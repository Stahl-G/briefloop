// Revision questions (#858): edits the Evaluator was unsure about, asked once,
// and the ledger of fact corrections that never become writing skills.
import {$ as lookup,esc} from './dom.js';

export const CATEGORY_CHOICES=[
 ['taste','写法偏好','原稿没错，只是我想这样写'],
 ['fact_correction','事实纠错','原稿的数字、事实或口径不对'],
 ['reader_specific','只针对这位读者','换一位读者未必要这样改'],
];

function excerpt(text,limit=160){const value=String(text||'').replace(/\s+/g,' ').trim();return value.length>limit?value.slice(0,limit)+'…':value}

export function questionHTML(edit){
 const change=edit.op==='delete'?`删掉了：<q>${esc(excerpt(edit.before))}</q>`:edit.op==='insert'?`加入了：<q>${esc(excerpt(edit.after))}</q>`:`把 <q>${esc(excerpt(edit.before))}</q> 改成 <q>${esc(excerpt(edit.after))}</q>`;
 return `<div class="revision-question" data-edit="${esc(edit.id)}"><p class="revision-question-where">${esc(edit.heading||'正文')}</p><p>${change}</p><p class="help">为什么这样改？</p><div class="revision-question-choices">${CATEGORY_CHOICES.map(([value,label,tip])=>`<button type="button" class="outline" data-answer="${value}" title="${esc(tip)}">${esc(label)}</button>`).join('')}<button type="button" class="ghost" data-answer="skip" title="这处改动不进入学习">跳过</button></div></div>`;
}

export function revisionQuestionsUI({api,action,$=lookup}){
 function bind(box){
  box.querySelectorAll('[data-answer]').forEach(button=>button.onclick=()=>{
   const edit=button.closest('[data-edit]').dataset.edit,category=button.dataset.answer;
   action(()=>api('revision-answer',{edit_id:edit,category}),category==='skip'?'已跳过，这处改动不进入学习':category==='fact_correction'?'已记为事实纠错，不作为写作经验':'已记下，会进入下一次学习');
  });
 }
 function render(value){
  const questions=value?.questions||[],corrections=value?.fact_corrections||[];
  const box=$('revision-questions');
  if(box){
   box.hidden=!questions.length;
   box.innerHTML=questions.length?`<h3>${questions.length} 处改动需要你确认原因</h3><p class="help">只问拿不准、改动较大的地方。事实纠错不会写进写作技巧；跳过的改动不进入学习。</p>${questions.map(questionHTML).join('')}`:'';
   bind(box);
  }
  const ledger=$('fact-corrections');
  if(ledger){
   ledger.innerHTML=corrections.length?corrections.map(edit=>`<div class="fact-correction"><small>${esc(edit.heading||'正文')} · ${edit.decided_by==='user'?'你确认':'Evaluator 判断'}</small><p><del>${esc(excerpt(edit.before,120))}</del> → ${esc(excerpt(edit.after,120))}</p></div>`).join(''):'<p class="help">还没有事实纠错记录。</p>';
  }
  const hint=$('revision-questions-hint');
  if(hint){hint.hidden=!questions.length;hint.textContent=questions.length?`有 ${questions.length} 处改动等你确认原因，在“经验”页处理。`:''}
 }
 return {render};
}
