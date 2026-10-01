import {esc} from './dom.js';

const sourceNames={provider_api:'提供方 API',host:'宿主目录',host_catalog:'宿主目录',host_session:'宿主会话目录',native_config:'本机配置',local_settings:'本机配置',local_routes:'本机路由',host_default_only:'宿主未提供目录',unsupported:'不支持目录',builtin_hints:'静态建议（已停用）'};
const statusNames={ok:'目录已读取',reachable:'目录已读取',partial:'部分目录读取失败',error:'目录读取失败',failed:'目录读取失败',unsupported:'不支持列举模型',unavailable:'宿主未提供目录',configured:'已读取本机设置（非实时目录）',unconfigured:'尚未配置提供商',empty:'目录为空',auth_failed:'认证失败',insufficient_balance:'余额不足',forbidden:'无权访问',catalog_unavailable:'目录接口不可用',rate_limited:'请求限流',upstream_unavailable:'提供方暂不可用',connection_failed:'连接失败',invalid_catalog:'目录响应无法识别',http_error:'接口返回错误'};
const unavailable=new Set(['error','failed','unsupported','unconfigured','auth_failed','insufficient_balance','forbidden','catalog_unavailable','rate_limited','upstream_unavailable','connection_failed','invalid_catalog','http_error']);
export function catalogSource(source){return sourceNames[source]||source||'来源未说明'}
export function catalogStatus(catalog){
 if(catalog?.loading)return '正在查询目录；上次结果尚未刷新';
 if(!catalog)return '尚未读取目录';
 return statusNames[catalog.status]||catalog.status||(catalog.models?.length?'目录已读取':'未读取到模型');
}
export function catalogModelName(model,backend){return model.id==='default'?(backend==='claude'?'跟随 Claude Code 默认':'跟随宿主默认'):model.name||model.id}
export function catalogSummary(catalog,name=''){
 return [name,catalogStatus(catalog),catalog?.models?.length?catalog.models.length+' 项':''].filter(Boolean).join(' · ');
}
export function catalogDescription(catalog,name=''){
 const parts=[name,catalogStatus(catalog)];
 if(catalog){
  parts.push(catalogSource(catalog.source));
  if(catalog.models?.length)parts.push(`${catalog.models.length} 个模型`);
  const checked=catalog.checked_at||catalog.refreshed_at;
  if(checked)parts.push(`${catalog.loading?'上次查询':'查询时间'}：${checked}`);
  if(catalog.diagnostic)parts.push(catalog.diagnostic);
  if(catalog.note)parts.push(catalog.note);
  const providers=(catalog.providers||[]).map(p=>`${p.name||p.provider||p.id}：${statusNames[p.status]||p.status||'状态未说明'}${p.diagnostic?'（'+p.diagnostic+'）':''}`);
  if(providers.length)parts.push(providers.join('；'));
 }
 return parts.filter(Boolean).join(' · ')+'。可手动输入模型 ID；目录结果不代表模型调用成功。';
}

// One live source for every model selector. Settled results are only a display
// snapshot: every open/refresh queries the backend, while concurrent opens share
// the same request. A response never writes a selected model or selected backend.
export function createModelCatalog({api,$,getBackend,getChatBackend,runtimeName=id=>id,runtimeIcon=()=>'',onCatalogChange=()=>{},openExecutionPicker=null,document:doc=globalThis.document,Option:OptionClass=globalThis.Option,MutationObserver:Observer=globalThis.MutationObserver}){
 const catalogs=new Map(),pending=new Map(),generations=new Map();
 let pickerTarget=null,pickerRevision=0;
 const targetBackend=target=>{
  const backendInput=target&&$(target)?.dataset?.modelBackendInput;
  return backendInput?$(backendInput)?.value||getBackend():target==='chat-model'?getChatBackend():getBackend();
 };
 function normalize(data,backend){
  const disabled=data.source==='builtin_hints'||unavailable.has(data.status);
  const models=disabled?[]:(data.models||[]).map(m=>typeof m==='string'?{id:m,name:m}:{...m,name:m.name||m.label||m.id}).filter(m=>m.id);
  return {...data,backend,models,loading:false,diagnostic:data.diagnostic||data.error||'',providers:Array.isArray(data.providers)?data.providers:[]};
 }
 function changed(){
  refreshInlineModelPickers();
  const status=$('runtime-model-status');if(status)status.textContent=catalogSummary(catalogs.get(getBackend()),runtimeName(getBackend()));
  const suggestions=$('model-suggestions');if(suggestions)suggestions.innerHTML=(catalogs.get(getBackend())?.models||[]).map(m=>`<option value="${esc(m.id)}" label="${esc(m.name)}"></option>`).join('');
  if(pickerTarget&&$('model-picker')?.open)renderModelPicker();
  onCatalogChange();
 }
 function fetchModelCatalog(_force=false,backend=getBackend()){
  if(pending.has(backend))return pending.get(backend);
  const generation=generations.get(backend)||0;
  catalogs.set(backend,{...catalogs.get(backend),backend,models:catalogs.get(backend)?.models||[],loading:true});
  const current=()=>generation===(generations.get(backend)||0)&&pending.get(backend)===request;
  const request=Promise.resolve().then(()=>api('models?backend='+encodeURIComponent(backend)+'&refresh=1')).then(data=>{
   if(!current())return [];
   catalogs.set(backend,normalize(data,backend));return catalogs.get(backend).models;
  }).catch(error=>{
   if(!current())return [];
   catalogs.set(backend,{backend,models:[],status:'error',diagnostic:error.message||'模型目录读取失败',loading:false});return [];
  }).finally(()=>{if(current()){pending.delete(backend);changed()}});
  pending.set(backend,request);changed();return request;
 }
 function invalidate(backend=getBackend()){
  generations.set(backend,(generations.get(backend)||0)+1);
  pending.delete(backend);catalogs.delete(backend);changed();
 }
 async function refreshModelSuggestions(){
  await Promise.all([...new Set([getBackend(),getChatBackend()])].filter(Boolean).map(backend=>fetchModelCatalog(true,backend)));
 }
 async function openModelPicker(target){
  if(openExecutionPicker&&['chat-model','model-select'].includes(target))return openExecutionPicker(target);
  pickerTarget=target;pickerRevision++;
  $('model-picker-search').value='';
  if($('model-picker-custom-form')){
   const form=$('model-picker-custom-form'),input=$('model-picker-custom-id');form.hidden=true;input.value='';
   $('model-picker-custom-open').onclick=()=>{form.hidden=false;input.value='';input.focus();$('model-picker-custom-confirm').disabled=true};
   $('model-picker-custom-cancel').onclick=()=>{form.hidden=true;$('model-picker-custom-open').focus()};
   input.oninput=()=>{input.setCustomValidity?.('');$('model-picker-custom-confirm').disabled=!input.value.trim()};
   form.onsubmit=event=>{event.preventDefault();const value=input.value.trim();if(value&&!/\s/.test(value))pickModel(value);else input.setCustomValidity?.('请输入完整模型 ID，不含空白字符。')};
  }
  $('model-picker').showModal();
  return refreshCurrentModelPicker();
 }
 async function refreshCurrentModelPicker(){
  const revision=++pickerRevision,backend=targetBackend(pickerTarget);
  await fetchModelCatalog(true,backend);
  if(revision===pickerRevision&&backend===targetBackend(pickerTarget))renderModelPicker();
 }
 function renderModelPicker(){
  if(!pickerTarget)return;
  const backend=targetBackend(pickerTarget),catalog=catalogs.get(backend),models=catalog?.models||[];
  const q=$('model-picker-search').value.trim().toLowerCase();
  const shown=models.filter(m=>[m.id,m.name,m.provider].some(value=>String(value||'').toLowerCase().includes(q)));
  $('model-picker-status').textContent=catalogSummary(catalog,runtimeName(backend))+(q?` · 筛出 ${shown.length} 个`:'');
  if($('model-picker-details'))$('model-picker-details').textContent=catalogDescription(catalog);
  $('model-picker-refresh').disabled=!!catalog?.loading;
  let lastProvider=null,html=$(pickerTarget)?.dataset.roleModel?'<button type="button" class="workspace-choice" data-model-pick=""><span><strong>继承主链模型</strong></span></button>':'';
  for(const model of shown){
   const provider=model.provider||runtimeName(backend);
   if(provider!==lastProvider){lastProvider=provider;if(new Set(shown.map(item=>item.provider||backend)).size>1)html+=`<h3 class="workspace-list-heading">${esc(provider)}</h3>`}
   const name=catalogModelName(model,backend),selected=$(pickerTarget)?.value===model.id;
   html+=`<button type="button" class="workspace-choice ${selected?'selected':''}" data-model-pick="${esc(model.id)}" aria-pressed="${selected}"><span><strong>${esc(name)}</strong>${name!==model.id&&model.id!=='default'?`<small>${esc(model.id)}</small>`:''}</span><em>${selected?'✓':''}</em></button>`;
  }
  $('model-picker-list').innerHTML=html||`<p class="help">${esc(catalog?.loading?'正在读取模型列表…':models.length?'没有匹配的模型。可在下方单独填写模型 ID。':'暂时没有模型可供选择。请刷新目录，或填写完整模型 ID；当前选择仍保留。')}</p>`;
  $('model-picker-list').querySelectorAll('[data-model-pick]').forEach(button=>button.onclick=()=>pickModel(button.dataset.modelPick));
 }
 function pickModel(id){
  const input=pickerTarget&&$(pickerTarget);$('model-picker').close();pickerRevision++;
  if(!input)return;input.value=id;input.dispatchEvent(new Event('change',{bubbles:true}));refreshModelLabels();
 }
 function refreshModelLabels(){
  doc.querySelectorAll('[data-model-trigger]').forEach(button=>{
   if(!button.dataset.modelTrigger)return;
   const input=$(button.dataset.modelTrigger);if(!input)return;
   const backend=targetBackend(input.id),model=input.value,record=catalogs.get(backend)?.models.find(item=>item.id===model);
   const label=model==='default'?'默认':record?.name||model||(input.dataset.roleModel?'继承主链模型':'选择模型');
   button.innerHTML=`<span class="model-trigger-icon" aria-hidden="true">${runtimeIcon(backend)}</span><span class="model-trigger-label">${esc(input.id==='chat-model'?runtimeName(backend)+' · '+label:model==='default'?catalogModelName({id:model},backend):label)}</span><span class="model-trigger-chevron" aria-hidden="true">⌄</span>`;
   button.disabled=input.disabled;button.title=model==='default'?catalogModelName({id:model},backend):label;
  });
 }
 function refreshInlineModelPickers(){
  refreshModelLabels();
  doc.querySelectorAll('.model-picker-select').forEach(select=>{
   const input=select.parentElement.querySelector('input'),backend=targetBackend(input?.id),catalog=catalogs.get(backend);
   select.replaceChildren(new OptionClass('▾',''));
   const status=new OptionClass(catalogStatus(catalog),'__status__');status.disabled=true;select.add(status);
   for(const model of catalog?.models||[])select.add(new OptionClass(catalogModelName(model,backend),model.id));
   select.add(new OptionClass('输入其他模型 ID…','__custom__'));
   select.add(new OptionClass('搜索 / 刷新模型目录…','__browse__'));
   if(input?.dataset.roleModel)select.add(new OptionClass('继承主链模型','__inherit__'));
   select.title=catalogDescription(catalog,runtimeName(backend));
  });
 }
 function setupModelPickers(root=doc){
  for(const input of root.querySelectorAll('input[list="model-suggestions"]')){
   input.removeAttribute('list');
   const wrap=doc.createElement('span');wrap.className='model-picker';input.before(wrap);wrap.append(input);
   input.hidden=true;input.type='hidden';
   const button=doc.createElement('button');button.type='button';button.className='model-picker-trigger';button.dataset.modelTrigger=input.id;button.setAttribute('aria-haspopup','dialog');
   button.onclick=()=>{if(!input.disabled)openModelPicker(input.id)};wrap.append(button);
   const select=doc.createElement('select');select.className='model-picker-select';select.setAttribute('aria-label',input.id==='chat-model'?'选择模型':'选择'+(input.getAttribute('aria-label')||'模型'));select.dataset.testid='model-catalog-select';
   select.hidden=true;
   // Pointer and keyboard opens refresh equally; a focus event alone does not
   // double-query the following pointer event.
   select.onpointerdown=()=>{if(!input.disabled)fetchModelCatalog(true,targetBackend(input.id))};
   select.onkeydown=event=>{if(['ArrowDown','ArrowUp',' ','Enter','F4'].includes(event.key)&&!input.disabled)fetchModelCatalog(true,targetBackend(input.id))};
   select.onchange=()=>{
    const model=select.value;select.value='';if(input.disabled)return;
    if(model==='__custom__'){openModelPicker(input.id).then(()=>{$('model-picker-custom-open')?.onclick?.()});return}
    if(model==='__browse__'){openModelPicker(input.id);return}
    if(!model||model==='__status__')return;input.value=model==='__inherit__'?'':model;
    input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.focus();
   };
   wrap.append(select);
   if(Observer)new Observer(()=>{select.disabled=input.disabled;button.disabled=input.disabled}).observe(input,{attributes:true,attributeFilter:['disabled']});select.disabled=input.disabled;
  }
  refreshInlineModelPickers();
 }
 return {catalogs,invalidate,fetchModelCatalog,refreshModelSuggestions,openModelPicker,refreshCurrentModelPicker,renderModelPicker,pickModel,refreshInlineModelPickers,refreshModelLabels,setupModelPickers,targetBackend};
}

// The Native configuration form consumes the same provider response as the
// execution selectors. OpenCode's configuration probe remains a direct provider
// API check, distinct from OpenCode's execution-host catalogue.
export function createProviderCatalog({api,$,getEndpoint,modelDirectory}){
 const pending=new Map(),generations=new Map();let revision=0;
 function invalidate(engine,provider){
  if(engine&&provider){const key=JSON.stringify([engine,provider]);generations.set(key,(generations.get(key)||0)+1);pending.delete(key)}
  if(!engine||(engine===getEndpoint()&&provider===$('custom-provider').value.trim())){revision++;$('provider-model-options').innerHTML=''}
 }
 async function load(){
  const provider=$('custom-provider').value.trim(),engine=getEndpoint(),request=++revision;
  $('provider-model-options').innerHTML='';
  if(!provider){$('provider-result').textContent='先保存提供商配置，再读取目录；也可手动输入模型 ID。';return}
  $('provider-result').textContent='正在查询提供方模型目录；此操作不调用模型。';
  const key=JSON.stringify([engine,provider]),generation=generations.get(key)||0;
  if(!pending.has(key)){
   const query=engine==='native'?modelDirectory.fetchModelCatalog(true,'briefloop-native').then(()=>{
    const catalog=modelDirectory.catalogs.get('briefloop-native');
    const result=catalog?.providers?.find(p=>(p.provider||p.id)===provider);
    return result?{...result,models:(result.models||[]).map(id=>typeof id==='string'?{id,name:id}:id)}:
     {...catalog,models:[],diagnostic:catalog?.diagnostic||'此提供商未返回模型目录；请先保存配置。'};
   }):Promise.resolve().then(()=>api('opencode/provider-catalog',{provider})).then(result=>({...result,source:'provider_api',models:(result.models||[]).map(id=>typeof id==='string'?{id,name:id}:id)}));
   const active=query.finally(()=>{if(pending.get(key)===active)pending.delete(key)});
   pending.set(key,active);
  }
  let result;
  try{result=await pending.get(key)}catch(error){result={models:[],status:'error',source:'provider_api',diagnostic:error.message}}
  if(request!==revision||generation!==(generations.get(key)||0)||engine!==getEndpoint()||provider!==$('custom-provider').value.trim())return;
  const models=unavailable.has(result.status)?[]:result.models||[];
  $('provider-model-options').innerHTML=models.map(model=>`<option value="${esc(model.id)}"></option>`).join('');
  $('provider-result').textContent=catalogDescription({...result,models},provider);
 }
 return {load,invalidate};
}
