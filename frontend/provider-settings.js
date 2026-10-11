import {esc} from './dom.js';
import {createProviderCatalog} from './model-catalog.js';

// The "API 提供商" dialog: saved configurations, the save form and the live
// model catalog. Leaving the dialog and adopting a saved model touch app
// state, so app.js passes those in as onClose and onUse.
export function providerSettingsUI({$,api,modelDirectory,providerCapabilities,settingsView,settingsModelTab,onClose,onUse,
  storage=globalThis.localStorage}){
 let savedProviderConfigurations=[];
 const providerEndpoint=()=>$('provider-engine').value==='briefloop-native'?'native':'opencode';
 const providerDirectory=createProviderCatalog({api,$,getEndpoint:providerEndpoint,modelDirectory});
 const loadProviderCatalog=()=>providerDirectory.load();
 function init(){
  $('provider-engine').onchange=()=>{providerDirectory.invalidate();$('custom-api-key').value='';$('custom-provider').value='';$('provider-open').click()};
  $('provider-open').onclick=async()=>{$('provider-result').textContent='';$('custom-supports-images').value='';providerCapabilities.reset(providerEndpoint());
    $('provider-use').hidden=true;settingsView('models');settingsModelTab('api');
   try{const r=await api(providerEndpoint()+'/providers');savedProviderConfigurations=r.configurations||[];
   $('custom-saved').innerHTML='<option value="">新建配置</option>'+savedProviderConfigurations.map((p,i)=>`<option value="${i}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
   const selected=savedProviderConfigurations.findIndex(p=>p.provider===$('custom-provider').value.trim()&&p.model===$('custom-model').value.trim());
   if(selected>=0){
    $('custom-saved').value=String(selected);$('custom-saved').dispatchEvent(new Event('change'));
   }else if(!$('custom-provider').value.trim()&&savedProviderConfigurations.length){
    const previous=storage.getItem('briefloop-provider-id');const index=Math.max(0,savedProviderConfigurations.findIndex(p=>p.provider===previous));
    $('custom-saved').value=String(index);$('custom-saved').dispatchEvent(new Event('change'));
   }else{$('custom-saved').value='';if(savedProviderConfigurations.some(p=>p.provider===$('custom-provider').value.trim()))loadProviderCatalog()}
   }catch(e){$('provider-result').textContent='已有配置读取失败：'+e.message}
  };
  $('custom-saved').onchange=()=>{
   const p=savedProviderConfigurations[Number($('custom-saved').value)];if($('custom-saved').value===''||!p){providerCapabilities.reset(providerEndpoint());
     return}
   for(const [id,key] of [['custom-provider','provider'],['custom-name','name'],['custom-model','model'],['custom-protocol','protocol'],['custom-base-url','base_url'],['custom-context-limit','context_limit'],['custom-output-limit','output_limit'],['custom-supports-images','supports_images']])$(id).value=p[key]==null?'':String(p[key]);
   providerCapabilities.load(p,providerEndpoint());
   $('custom-api-key').value='';$('provider-use').hidden=true;storage.setItem('briefloop-provider-id',p.provider);loadProviderCatalog();
  };
  $('provider-close').onclick=()=>{$('custom-api-key').value='';onClose()};
  $('provider-dialog').addEventListener('close',()=>{$('custom-api-key').value=''});
  $('provider-form').onsubmit=async event=>{
   event.preventDefault();const button=$('provider-save');button.disabled=true;$('provider-result').textContent='正在保存到本机…';
   const engine=providerEndpoint(),backend=engine==='native'?'briefloop-native':'opencode';
   const body={protocol:$('custom-protocol').value,name:$('custom-name').value.trim(),context_limit:$('custom-context-limit').value?Number($('custom-context-limit').value):null,output_limit:$('custom-output-limit').value?Number($('custom-output-limit').value):null,provider:$('custom-provider').value.trim(),base_url:$('custom-base-url').value.trim(),model:$('custom-model').value.trim(),api_key:$('custom-api-key').value,supports_images:$('custom-supports-images').value===''?null:$('custom-supports-images').value==='true',...providerCapabilities.read(engine)};
   $('custom-api-key').value='';
   try{
    const result=await api(engine+'/provider',body);body.api_key='';
    modelDirectory.invalidate(backend);providerDirectory.invalidate(engine,body.provider);
    const refreshed=modelDirectory.fetchModelCatalog(true,backend);
    if(engine!==providerEndpoint()||body.provider!==$('custom-provider').value.trim()){await refreshed;return}
    $('provider-use').hidden=false;$('provider-use').dataset.model=result.model;$('provider-use').dataset.backend=backend;
    $('provider-result').textContent='已保存 '+result.model+'。当前模型选择保持原值。';
    await Promise.all([refreshed,loadProviderCatalog()]);
   }catch(e){$('provider-result').textContent=e.message}finally{body.api_key='';button.disabled=false}
  };

  $('provider-catalog').onclick=loadProviderCatalog;
  $('custom-model').onfocus=loadProviderCatalog;
  $('custom-provider').oninput=()=>providerDirectory.invalidate();
  $('provider-use').onclick=()=>onUse($('provider-use').dataset.model,$('provider-use').dataset.backend);
  $('custom-protocol').onchange=()=>{
   const base=$('custom-base-url').value.replace(/\/+$/,'');
   if(['https://api.deepseek.com','https://api.deepseek.com/v1','https://api.deepseek.com/anthropic','https://api.deepseek.com/anthropic/v1'].includes(base))
   $('custom-base-url').value=$('custom-protocol').value==='anthropic-messages'?'https://api.deepseek.com/anthropic/v1':'https://api.deepseek.com';
  };
  $('custom-preset').onchange=()=>{
   const presets={deepseek:['DeepSeek','deepseek','chat-completions','https://api.deepseek.com'],openai:['OpenAI','openai-custom','responses','https://api.openai.com/v1'],anthropic:['Anthropic','anthropic-custom','anthropic-messages','https://api.anthropic.com/v1']};
   const p=presets[$('custom-preset').value];if(!p)return;providerDirectory.invalidate();
   ['custom-name','custom-provider','custom-protocol','custom-base-url'].forEach((id,i)=>$(id).value=p[i]);
  };
 }
 return {init,providerEndpoint,providerDirectory,loadProviderCatalog};
}
