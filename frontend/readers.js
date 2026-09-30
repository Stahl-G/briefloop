// Reader profiles (#858): pick a saved reader for a report, and manage profiles
// and each reader's own skill on the learning page.
import {$ as lookup,esc} from './dom.js';

export function readersUI({api,action,$=lookup}){
 let editing=null,known=[];
 function fillSelect(readers){
  const select=$('reader-select');if(!select)return;
  const value=select.value;
  select.innerHTML='<option value="">不指定读者档案</option>'+readers.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
  select.value=readers.some(r=>r.id===value)?value:'';
 }
 function onSelect(){
  const select=$('reader-select'),audience=select?.form?.elements?.audience;if(!select||!audience)return;
  const chosen=known.find(r=>r.id===select.value);
  // Fill the audience only when it still holds the default or another reader's name.
  if(chosen&&(!audience.value.trim()||audience.value==='自己'||known.some(r=>r.name===audience.value)))audience.value=chosen.name;
 }
 function profileHTML(reader){
  return `<div class="reader-profile" data-reader="${esc(reader.id)}"><div class="reader-profile-head"><strong>${esc(reader.name)}</strong><span>${reader.skill_id?'专属技能 '+esc(reader.skill_id):'沿用工作区技能'}</span></div>${reader.decisions?`<p><small>用报告做什么决定</small>${esc(reader.decisions)}</p>`:''}${reader.preferences?`<p><small>偏好</small>${esc(reader.preferences)}</p>`:''}<div class="reader-profile-actions"><button type="button" class="outline" data-reader-edit>编辑</button>${reader.skill_id?'<button type="button" class="outline" data-reader-unbind>回到工作区技能</button>':''}<button type="button" class="ghost" data-reader-archive>归档</button></div>${reader.wiki?`<details><summary>这位读者的经验</summary><pre class="reader-wiki">${esc(reader.wiki)}</pre></details>`:''}</div>`;
 }
 function renderList(readers){
  const box=$('reader-profiles');if(!box)return;
  box.innerHTML=readers.length?readers.map(profileHTML).join(''):'<p class="help">还没有读者档案。写给固定读者的报告，建一个档案后，只针对这位读者的改法会单独学习。</p>';
  box.querySelectorAll('[data-reader]').forEach(node=>{
   const reader=readers.find(r=>r.id===node.dataset.reader);
   node.querySelector('[data-reader-edit]').onclick=()=>openForm(reader);
   node.querySelector('[data-reader-archive]').onclick=()=>action(()=>api('reader-archive',{id:reader.id}),'读者档案已归档，旧报告保留原样');
   const unbind=node.querySelector('[data-reader-unbind]');
   if(unbind)unbind.onclick=()=>action(()=>api('reader-skill',{reader_id:reader.id,skill_id:null}),'这位读者的下一份报告改用工作区技能');
  });
 }
 function openForm(reader=null){
  const form=$('reader-form');if(!form)return;
  editing=reader?.id||null;form.hidden=false;
  form.elements.name.value=reader?.name||'';form.elements.decisions.value=reader?.decisions||'';form.elements.preferences.value=reader?.preferences||'';
  form.querySelector('[data-reader-form-title]').textContent=reader?'编辑读者档案':'新建读者档案';
  form.elements.name.focus?.();
 }
 function bind(){
  const add=$('reader-add');if(add)add.onclick=()=>openForm();
  const form=$('reader-form');
  if(form){
   form.onsubmit=e=>{e.preventDefault();const body={name:form.elements.name.value,decisions:form.elements.decisions.value,preferences:form.elements.preferences.value,...(editing?{id:editing}:{})};
    action(async()=>{await api('reader-save',body);form.hidden=true;editing=null},'读者档案已保存');};
   const cancel=form.querySelector('[data-reader-cancel]');if(cancel)cancel.onclick=()=>{form.hidden=true;editing=null};
  }
  const select=$('reader-select');if(select)select.addEventListener('change',onSelect);
 }
 function render(readers=[]){known=readers;fillSelect(readers);renderList(readers)}
 return {render,bind,openForm};
}
