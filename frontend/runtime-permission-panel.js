import {esc} from './dom.js';
import {permissionChoices,permissionSelection} from './runtime-permission-modes.js';

function modeRow(mode,selected,index,locked){
 const disabled=locked||mode.disabled;
 return `<label class="runtime-permission-choice${disabled?' is-disabled':''}"><input type="radio" name="runtime-permission-mode" value="${esc(mode.id)}" data-permission-mode="${index}" ${mode.id===selected?'checked':''} ${disabled?'disabled':''}><span><strong>${esc(mode.native_name||mode.name||mode.id)}</strong>${mode.description?`<small>${esc(mode.description)}</small>`:''}${mode.disabled&&mode.disabled_reason?`<small class="runtime-permission-disabled-reason">${esc(mode.disabled_reason)}</small>`:''}</span></label>`;
}

export function permissionModeMarkup(catalog,selected,{locked=false}={}){
 const choice=permissionSelection(catalog,selected),modes=choice.modes;
 const inherit=catalog?.inherit_mode;
 const native=modes.map((mode,index)=>modeRow(mode,choice.unavailable?'':choice.selected,index,locked)).join('');
 const inherited=inherit?.id&&!modes.some(mode=>mode.id===inherit.id)?`<section class="runtime-permission-inherit" aria-label="连接选项"><p>连接选项</p>${modeRow(inherit,choice.unavailable?'':choice.selected,modes.length,locked)}</section>`:'';
 const detailRows=modes.filter(mode=>mode.id!==(mode.native_name||mode.name||mode.id));
 const details=(catalog?.note||catalog?.diagnostic||detailRows.length)?`<details class="runtime-permission-details"><summary>读取详情</summary>${catalog.note?`<p>${esc(catalog.note)}</p>`:''}${catalog.diagnostic?`<p>${esc(catalog.diagnostic)}</p>`:''}${detailRows.length?`<dl>${detailRows.map(mode=>`<dt>${esc(mode.native_name||mode.name)}</dt><dd><code>${esc(mode.id)}</code></dd>`).join('')}</dl>`:''}</details>`:'';
 return `<fieldset class="runtime-permission-modes"><legend>下一回合权限</legend>${native||'<p class="help runtime-permission-empty">未取得可选择的权限模式。</p>'}${inherited}</fieldset>${locked?'<p class="help">当前回合的设置已锁定。</p>':''}<p class="runtime-permission-selection-status" role="status"></p>${details}`;
}

function globalRulesMarkup(catalog){
 if(catalog.backend!=='antigravity'||!Array.isArray(catalog.rules)||!catalog.revision)return '';
 return `<details class="runtime-permission-rules"><summary>Antigravity 本机规则</summary><p class="help">更改会保存到本机 Antigravity，也会影响其他会话。</p><div class="permission-rule-list">${catalog.rules.map((row,index)=>`<div><code>${esc(row.decision)} · ${esc(row.rule)}</code><button type="button" data-remove-permission-rule="${index}" aria-label="移除 ${esc(row.decision)} ${esc(row.rule)}">移除</button></div>`).join('')||'<p class="help">没有已保存的规则。</p>'}</div><form id="permission-rule-form"><label for="permission-rule-decision">处理方式</label><select id="permission-rule-decision"><option value="allow">allow</option><option value="ask">ask</option><option value="deny">deny</option></select><label for="permission-rule-value">规则</label><input id="permission-rule-value" required autocomplete="off" spellcheck="false" placeholder="read_file(/path/to/file)"><button type="submit">保存到本机</button></form></details>`;
}

// Loading and rendering are read-only. Only a radio change or an explicit rule
// action crosses the injected write boundary.
export function createRuntimePermissionPanel({$,api,directory,getBackend,getModel=()=>'',runtimeName,getSelection,onSelect,isLocked=()=>false}){
 let current=null,sequence=0,currentModel='',saving=false;
 const el=name=>$('chat-permissions-'+name);
 const matching=(catalog,model)=>catalog&&catalog.backend===getBackend()&&model===getModel();
 function render(catalog=current){
  if(!catalog||!matching(catalog,currentModel))return;
  current=catalog;el('native').hidden=true;
  const selected=getSelection(catalog),choice=permissionSelection(catalog,selected),locked=isLocked();
  el('host').textContent=runtimeName(catalog.backend)+' · 下一回合';
  el('note').textContent=catalog.source?.label||'权限目录来源未提供';
  el('error').textContent=choice.unavailable?`原权限模式「${selected}」当前不可用，请重新选择后发送。`:'';
  const box=el('options');box.innerHTML=permissionModeMarkup(catalog,selected,{locked})+globalRulesMarkup(catalog);
  const choices=permissionChoices(catalog);
  box.querySelectorAll('[data-permission-mode]').forEach(input=>input.onchange=()=>{
   if(!input.checked)return;
   const mode=choices[Number(input.dataset.permissionMode)];
   if(!matching(catalog,currentModel)||isLocked()||!mode||mode.disabled){render();return}
   onSelect(mode.id,catalog);
   el('error').textContent='';
   box.querySelector('.runtime-permission-selection-status').textContent='已选择，下一回合生效。';
  });
  box.querySelectorAll('[data-remove-permission-rule]').forEach(button=>{
   button.disabled=locked;
   button.onclick=()=>{const row=catalog.rules[Number(button.dataset.removePermissionRule)];if(row)saveRule({operation:'remove',decision:row.decision,rule:row.rule})};
  });
  const form=box.querySelector('#permission-rule-form');
  if(form){
   form.querySelectorAll('button,input,select').forEach(control=>control.disabled=locked);
   form.onsubmit=event=>{event.preventDefault();saveRule({operation:'add',decision:$('permission-rule-decision').value,rule:$('permission-rule-value').value.trim()})};
  }
 }
 async function load({refresh=true}={}){
  const backend=getBackend(),model=getModel(),ticket=++sequence;
  current=null;currentModel=model;el('native').hidden=true;
  el('host').textContent=runtimeName(backend)+' · 下一回合';el('note').textContent='';el('error').textContent='';
  el('options').textContent='正在读取权限目录…';el('options').setAttribute('aria-busy','true');
  el('refresh').disabled=true;el('refresh').textContent='读取中…';
  try{
   const catalog=await directory.load(backend,{refresh,model});
   if(ticket!==sequence||backend!==getBackend()||model!==getModel())return;
   current={...catalog,backend};render();
  }catch(error){
   if(ticket!==sequence||backend!==getBackend()||model!==getModel())return;
   current={backend,kind:'unavailable',modes:[],source:{kind:'unavailable',label:'未取得运行端权限目录'},diagnostic:error.message};render();
   if(!el('error').textContent)el('error').textContent='权限目录读取失败，请刷新重试。';
  }finally{
   if(ticket===sequence){el('options').setAttribute('aria-busy','false');el('refresh').disabled=false;el('refresh').textContent='刷新'}
  }
 }
 async function saveRule(change){
  const catalog=current,model=currentModel;
  if(saving||isLocked()||!matching(catalog,model)||catalog.backend!=='antigravity'||!catalog.revision)return;
  saving=true;
  const controls=[...el('options').querySelectorAll('button,input,select')],disabled=controls.map(control=>control.disabled);
  controls.forEach(control=>control.disabled=true);el('error').textContent='';
  try{
   await api('runtime/permissions',{backend:catalog.backend,revision:catalog.revision,...change});
   if(!matching(catalog,model))return;
   await load();
   if(matching(current,model))el('options').querySelector('.runtime-permission-selection-status').textContent='本机规则已保存。';
  }catch(error){
   if(matching(catalog,model)){el('error').textContent=error.message;controls.forEach((control,index)=>control.disabled=disabled[index])}
  }finally{saving=false}
 }
 return {load,render,saveRule};
}
