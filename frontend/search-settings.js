// Search policy and provider keys: one settings block, shown in Settings or inline in the report setup.
import {$ as lookup} from './dom.js';
export const SEARCH_LABELS={native:'宿主自带搜索',tavily:'Tavily',duckduckgo:'DuckDuckGo',bocha:'博查',zhipu:'智谱搜索'};
export function searchSettingsUI({api,getState,runtimeName,renderBudgetProviderScope,$=lookup}){
 function readSearchPolicy(){return {primary_provider:$('search-provider').value,supplemental_providers:[...$('search-supplements').querySelectorAll('input[value]:checked')].map(e=>e.value).filter(v=>v!==$('search-provider').value),zhipu_engine:$('zhipu-engine').value,native_search_enabled:$('search-native').checked,coverage_mode:$('search-coverage').value,market_scope:$('search-market').value,platform_scope:[]}}
 function loadSearchPolicy(){const state=getState();const p=state.settings.search_policy||{primary_provider:state.settings.search_provider,native_search_enabled:state.settings.search_provider==='tavily'};$('search-provider').value=p.primary_provider||'native';$('search-coverage').value=p.coverage_mode||'coverage';$('search-market').value=p.market_scope||'';$('zhipu-engine').value=p.zhipu_engine||'search_std';$('search-native').checked=!!p.native_search_enabled;$('fetch-contact').value=state.settings.fetch_contact_email||'';$('search-supplements').querySelectorAll('input[value]').forEach(e=>e.checked=(p.supplemental_providers||[]).includes(e.value));refreshBochaSettings();refreshZhipuSettings()}
 function nativeSearchName(){return SEARCH_LABELS[$('search-provider').value]||'宿主自带搜索'}
 function renderSearchProvider(){const state=getState(),p=readSearchPolicy(),all=[p.primary_provider,...p.supplemental_providers];$('tavily-settings').hidden=!all.includes('tavily');$('bocha-settings').hidden=!all.includes('bocha');$('zhipu-settings').hidden=!all.includes('zhipu');$('search-supplements').disabled=p.coverage_mode==='primary_only';$('search-supplements').querySelectorAll('input[value]').forEach(e=>{e.disabled=e.value===p.primary_provider;if(e.disabled)e.checked=false});$('search-native').disabled=p.primary_provider==='native';$('search-native-note').textContent=(state.settings.agent_backend==='briefloop-native'?'内置引擎不提供宿主自带搜索；请使用已配置的受控搜索渠道。当前开关在切换其他宿主后适用。':runtimeName(state.settings.agent_backend||'codex')+'：能否搜索由宿主实际工具和授权决定。原生调用次数未知，不纳入受控 API 次数；发现的正文仍须保存。');$('setup-search-summary').textContent='优先 '+nativeSearchName()+(p.coverage_mode==='primary_only'?' · 仅首选':(p.supplemental_providers.length?' · 补充 '+p.supplemental_providers.map(x=>SEARCH_LABELS[x]).join('、'):'')+(p.native_search_enabled&&p.primary_provider!=='native'?' · 允许宿主搜索':''));renderBudgetProviderScope()}
 async function saveSearchPolicy(){const button=$('search-policy-save');button.disabled=true;const policy=readSearchPolicy();$('search-settings-status').textContent='保存中…';try{const contact=$('fetch-contact').value.trim();await api('settings',{search_provider:policy.primary_provider,search_policy:policy,fetch_contact_email:contact});const {settings}=getState();settings.search_provider=policy.primary_provider;settings.search_policy=policy;settings.fetch_contact_email=contact;$('search-settings-status').textContent='已保存。新任务采用此策略，运行中的报告保持原配置。';await Promise.all([refreshTavilySettings(),refreshBochaSettings(),refreshZhipuSettings()])}catch(e){$('search-settings-status').textContent='未保存：'+e.message}finally{button.disabled=false;renderSearchProvider()}}
 async function refreshBochaSettings(){try{const r=await api('bocha');$('bocha-key-status').textContent=r.configured?'已配置 · '+(r.source==='environment'?'环境变量':'本机配置'):'尚未配置，博查搜索需填写 API Key';$('bocha-key-remove').hidden=r.source!=='file'}catch{$('bocha-key-status').textContent='无法读取博查配置，请重试'}}
 async function refreshZhipuSettings(){try{const r=await api('zhipu-search');$('zhipu-key-status').textContent=r.configured?'已配置 · '+(r.source==='environment'?'环境变量':'本机配置'):'尚未配置，智谱搜索需填写 API Key';$('zhipu-key-remove').hidden=r.source!=='file'}catch{$('zhipu-key-status').textContent='无法读取智谱搜索配置，请重试'}}
 async function refreshTavilySettings(){
  try{const result=await api('tavily');const where={environment:'环境变量',file:'本机配置'}[result.source]||'本机配置';$('tavily-key-status').textContent=result.configured?`已配置 · ${where} · 所有工作区可用`:'尚未配置 Tavily 密钥';$('tavily-key-remove').hidden=result.source!=='file';return result}
  catch{$('tavily-key-status').textContent='暂时无法读取本机配置，请稍后重试。';$('tavily-key-remove').hidden=true}
 }
 async function saveTavilyKey(event){
  event.preventDefault();const input=$('tavily-key'),key=input.value.trim();if(!key){$('search-settings-status').textContent='请先粘贴 Tavily API Key。';return}
  $('tavily-key-save').disabled=true;input.disabled=true;$('search-settings-status').textContent='正在保存本机配置…';
  try{await api('tavily',{api_key:key});$('search-settings-status').textContent='密钥已保存，未发起验证或搜索。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='密钥未能保存，请检查本地服务后重试。'}finally{input.value='';input.disabled=false;$('tavily-key-save').disabled=false}
 };
 function moveSearchSettings(place){
  const slot=$(place==='setup'?'setup-search-inline':'settings-search-slot');slot.append($('settings-search-block'));
  if(place==='settings')renderSearchProvider();else if(!$('setup-search-inline').hidden)$('tavily-settings').hidden=false;
 }
 function init(){
  $('search-policy-save').onclick=saveSearchPolicy;
  for(const id of ['search-supplements','search-coverage','search-market','zhipu-engine'])$(id).onchange=()=>{renderSearchProvider();$('search-settings-status').textContent='设置已修改，点击“保存搜索策略”生效。'};
  for(const action of ['save','remove'])$('bocha-key-'+action).onclick=async()=>{const button=$('bocha-key-'+action),key=$('bocha-key');button.disabled=true;try{await api('bocha',action==='remove'?{remove:true}:{api_key:key.value.trim()});await refreshBochaSettings();$('search-settings-status').textContent=action==='remove'?'本机密钥已移除。':'博查密钥已保存，未调用搜索。'}catch(e){$('search-settings-status').textContent=e.message}finally{key.value='';button.disabled=false}};
  for(const action of ['save','remove'])$('zhipu-key-'+action).onclick=async()=>{const button=$('zhipu-key-'+action),key=$('zhipu-key');button.disabled=true;try{await api('zhipu-search',action==='remove'?{remove:true}:{api_key:key.value.trim()});await refreshZhipuSettings();$('search-settings-status').textContent=action==='remove'?'本机密钥已移除。':'智谱搜索密钥已保存，未调用搜索。'}catch(e){$('search-settings-status').textContent=e.message}finally{key.value='';button.disabled=false}};
  $('search-provider').onchange=()=>{renderSearchProvider();$('search-settings-status').textContent='设置已修改，点击“保存搜索策略”生效。'};
  $('tavily-key-remove').onclick=async()=>{
   const button=$('tavily-key-remove');button.disabled=true;$('tavily-key').value='';
   try{await api('tavily',{remove:true});$('search-settings-status').textContent='本机保存的密钥已移除。';await refreshTavilySettings()}catch{$('search-settings-status').textContent='未能移除本机配置，请稍后重试。'}finally{button.disabled=false}
  };
  $('settings-dialog').addEventListener('close',()=>{$('tavily-key').value='';$('bocha-key').value='';$('zhipu-key').value='';$('zhipu-key').value='';if(!$('setup').hidden)moveSearchSettings('setup')});
  $('tavily-key-save').onclick=saveTavilyKey;
  $('tavily-key').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();if(!$('tavily-key-save').disabled)saveTavilyKey(event)}};
  $('setup-search-config').onclick=()=>{
   const container=$('setup-search-inline'),show=container.hidden;container.hidden=!show;$('setup-search-config').setAttribute('aria-expanded',String(show));
   if(show){moveSearchSettings('setup');$('tavily-settings').hidden=false;refreshTavilySettings().catch(()=>{})}else $('tavily-key').value='';
  };
 }
 return {init,readSearchPolicy,loadSearchPolicy,renderSearchProvider,refreshTavilySettings,moveSearchSettings};
}
