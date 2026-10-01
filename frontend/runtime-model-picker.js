import {esc} from './dom.js';
import {catalogSummary,catalogDescription,catalogModelName} from './model-catalog.js';

// A staged execution choice: browsing never changes a draft or workspace settings.
// Only Apply commits the complete host/model/effort tuple through the owning form.
export function createRuntimeModelPicker({api,$,directory,getRuntimes,read,commit,canEdit,canSwitchBackend=()=>true,document:doc=globalThis.document,Option:OptionClass=globalThis.Option}){
 let choice=null,original=null,target=null,revision=0,reasoningRevision=0,busy=false,effortLoading=false,opener=null;
 const dialog=$('runtime-model-picker');
 const field=id=>$('runtime-picker-'+id);
 const variant=backend=>['opencode','briefloop-native','mimo'].includes(backend);
 const blank=backend=>variant(backend)?'':'none';
 const runtime=()=>getRuntimes().find(r=>r.id===choice?.backend);
 const active=ticket=>ticket===revision&&!!choice&&dialog.open;
 function invalidate(){revision++;reasoningRevision++;choice=null;target=null;busy=false}
 function close(){if(busy)return;dialog.close();invalidate();opener?.focus()}
 dialog.oncancel=event=>{event.preventDefault();close()};
 dialog.onclose=()=>{if(choice&&!dialog.open)invalidate()};
 field('close').onclick=close;field('cancel').onclick=close;
 function controls(){
  if(!choice)return;
  const model=directory.catalogs.get(choice.backend)?.models?.find(m=>m.id===choice.model);
  field('apply').disabled=busy||effortLoading||!canEdit(target)||!runtime()?.available||!choice.model.trim()||model?.available===false||!!model?.disabled;
  field('backend').disabled=busy||!canSwitchBackend(target);field('search').disabled=busy;field('custom').disabled=busy;
  field('close').disabled=busy;field('cancel').disabled=busy;
 }
 function render(){
  if(!choice)return;
  const catalog=directory.catalogs.get(choice.backend),query=field('search').value.trim().toLowerCase();
  field('status').textContent=catalogSummary(catalog,runtime()?.name||choice.backend);
  field('details').textContent=catalogDescription(catalog);
  field('refresh').disabled=busy||!!catalog?.loading;
  field('selected').textContent=choice.model?'已选模型：'+choice.model:'请选择此运行时的模型';
  const models=(catalog?.models||[]).filter(m=>[m.id,m.name,m.provider].some(v=>String(v||'').toLowerCase().includes(query)));
  field('list').innerHTML=models.map(m=>`<button type="button" class="workspace-choice ${choice.model===m.id?'selected':''}" data-runtime-model="${esc(m.id)}" aria-pressed="${choice.model===m.id}" ${busy||m.available===false||m.disabled?'disabled':''}><span><strong>${esc(catalogModelName(m,choice.backend))}</strong><small>${esc(m.id)}${m.available===false||m.disabled?' · 不可用':''}</small></span><em>${choice.model===m.id?'✓':''}</em></button>`).join('')||'<p class="help">'+(catalog?.loading?'正在读取模型目录…':'没有可选模型。可刷新目录，或填写完整模型 ID。')+'</p>';
  field('list').querySelectorAll('[data-runtime-model]').forEach(button=>button.onclick=()=>selectModel(button.dataset.runtimeModel));
  controls();
 }
 async function loadModels(){
  const ticket=revision,backend=choice.backend;
  const request=directory.fetchModelCatalog(true,backend);render();await request;
  if(active(ticket)&&choice.backend===backend)render();
 }
 async function loadEffort(){
  const ticket=revision,request=++reasoningRevision,{backend,model}=choice;effortLoading=true;controls();
  const select=field('effort'),fallback=blank(backend);
  select.replaceChildren(new OptionClass('模型默认（读取中…）',fallback));select.value=fallback;select.disabled=true;
  field('effort-note').textContent='';field('custom-effort-wrap').hidden=true;
  if(!model){effortLoading=false;field('effort-note').textContent='选择模型后可调整推理强度';controls();return}
  let info;
  try{info=await api('runtime/reasoning?backend='+encodeURIComponent(backend)+'&model='+encodeURIComponent(model))}
  catch(error){info={options:[],error:true,note:'推理强度读取失败：'+error.message+'；可使用模型默认。'}}
  if(!active(ticket)||request!==reasoningRevision||choice.backend!==backend||choice.model!==model)return;
  const options=info.options||[];
  select.replaceChildren(new OptionClass(backend==='claude'?'跟随 Claude Code':'模型默认',fallback),...options.map(o=>new OptionClass(o.name||o.id,o.id)));
  if(variant(backend))select.add(new OptionClass('自定义档位…','__custom__'));
  const known=choice.effort===fallback||options.some(o=>o.id===choice.effort);
  if(!known&&!variant(backend)){
   field('effort-note').textContent='原推理强度未获此模型确认，将使用模型默认。';choice.effort=fallback;
  }else field('effort-note').textContent=info.note||'';
  select.value=!known&&variant(backend)?'__custom__':choice.effort;
  field('custom-effort-wrap').hidden=select.value!=='__custom__';field('custom-effort').value=choice.effort;
  effortLoading=false;select.disabled=busy;controls();
 }
 function selectModel(model){
  if(!choice||busy)return;
  choice.model=model.trim();choice.effort=blank(choice.backend);field('custom').value='';field('error').textContent='';render();loadEffort();
 }
 field('backend').onchange=()=>{
  if(!choice||busy||!canSwitchBackend(target)){if(choice)field('backend').value=choice.backend;return}
  const backend=field('backend').value;
  if(!getRuntimes().some(r=>r.id===backend&&r.available)){field('backend').value=choice.backend;return}
  revision++;choice={backend,model:'',effort:blank(backend)};
  field('search').value='';field('custom').value='';field('error').textContent='';render();loadEffort();loadModels();
 };
 field('search').oninput=render;
 field('search').onkeydown=event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();field('list').querySelector('[data-runtime-model]:not(:disabled)')?.focus()}};
 field('refresh').onclick=()=>{if(choice&&!busy)loadModels()};
 field('custom-form').onsubmit=event=>{event.preventDefault();const model=field('custom').value.trim();if(!model||/\s/.test(model)){field('error').textContent='请输入完整模型 ID，不含空白字符。';return}selectModel(model)};
 field('effort').onchange=()=>{if(!choice||busy)return;const value=field('effort').value;field('custom-effort-wrap').hidden=value!=='__custom__';choice.effort=value==='__custom__'?'':value;if(value==='__custom__'){field('custom-effort').value='';field('custom-effort').focus()}};
 field('custom-effort').oninput=()=>{if(choice&&!busy)choice.effort=field('custom-effort').value.trim()};
 field('apply').onclick=async()=>{
  if(!choice||busy||field('apply').disabled)return;
  controls();
  if(field('apply').disabled){field('error').textContent='当前任务状态或模型可用性已改变，请重新选择。';return}
  const ticket=revision;busy=true;render();field('effort').disabled=true;field('custom-effort').disabled=true;
  try{await commit(target,{...choice},{...original});if(active(ticket)){busy=false;close()}}
  catch(error){if(active(ticket))field('error').textContent=error.message||'保存失败，请重试'}
  finally{if(active(ticket)){busy=false;field('effort').disabled=false;field('custom-effort').disabled=false;render()}}
 };
 async function open(id){
  if(busy||!canEdit(id))return;
  revision++;reasoningRevision++;target=id;original={...read(id)};choice={...original};opener=doc.activeElement;
  field('backend').replaceChildren(...getRuntimes().map(r=>{const option=new OptionClass(r.name+(r.available?'':' · 不可用'),r.id);option.disabled=!r.available;return option}));
  if(!getRuntimes().some(r=>r.id===choice.backend)){const option=new OptionClass(choice.backend+' · 未检测到',choice.backend);option.disabled=true;field('backend').add(option)}
  field('backend').value=choice.backend;field('search').value='';field('custom').value='';field('error').textContent='';field('custom-effort').disabled=false;
  field('scope').textContent=id==='chat-model'?(canSwitchBackend(id)?'用于下一回合；取消不会改动当前选择或执行权限。':'报告任务沿用冻结运行时；可调整同一运行时的模型与强度。'):'保存为新报告与新会话的默认选择；切换运行时会清空原运行时的角色模型。';
  if(!dialog.open)dialog.showModal();render();field('backend').focus();await Promise.all([loadModels(),loadEffort()]);
 }
 return {open,close};
}
