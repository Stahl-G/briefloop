// One visible selector for every host; the backend supplies host/model choices.
export function settingsEffort(settings,backend){
 if(!['codex','opencode'].includes(backend))return settings.runtime_efforts?.[backend]||'none';
 return Object.hasOwn(settings,'reasoning_effort')?(settings.reasoning_effort||'none'):'none';
}
export function reasoningModel(backend,model,effort){
 return backend==='antigravity'&&effort&&effort!=='none'&&/^gemini-/.test(model)
  ?model.replace(/-(low|medium|high)$/,''):model;
}
export function reasoningControls({api,onUpdate=()=>{}}){
 const requests=new Map();let revision=0;
 const names={off:'关闭思考',on:'开启思考',minimal:'最少',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高',ultra:'超强'};
 function configure(control,backend,model,{variant=false}={}){
  if(!control)return;
  const key=JSON.stringify([backend,model||'default',revision]);
  let select=control;
  if(variant){
   select=control.parentElement.querySelector('[data-variant-choices]');
   if(!select){
    select=document.createElement('select');select.dataset.variantChoices='';select.id=control.id+'-choices';
    select.setAttribute('aria-label',control.getAttribute('aria-label')||'推理强度');
    control.before(select);control.removeAttribute('list');
    select.onchange=()=>{
     control.hidden=select.value!=='__custom__';
     if(!control.hidden){control.focus();return}
     control.value=select.value;control.dispatchEvent(new Event('change',{bubbles:true}));
    };
   }
   select.disabled=control.disabled;
  }
  const blank=variant?'':'none';
  if(control.dataset.reasoningKey!==key)delete control.dataset.reasoningUnconfirmed;
  const render=(info,loading=false)=>{
   // An empty response while loading is not evidence that the selection is unsupported.
   // Read the live value so a delayed reply cannot undo a newer user choice.
   const requestedValue=control.value||blank,choices=info.options||[];
   const unconfirmed=backend==='claude'&&!loading&&!info.error&&requestedValue!==blank&&!choices.some(option=>option.id===requestedValue);
   const value=unconfirmed?blank:requestedValue,signature=JSON.stringify([key,choices,value,info.note,loading]);
   if(!loading&&backend==='claude'){if(unconfirmed)control.dataset.reasoningUnconfirmed=requestedValue;else delete control.dataset.reasoningUnconfirmed}
   if(select.dataset.reasoningSignature===signature)return;
   const defaultName=backend==='claude'?'跟随 Claude Code':'模型默认';
   const nodes=[new Option(loading?defaultName+'（读取中…）':defaultName,blank),...choices.map(o=>new Option(names[o.id]?names[o.id]+' · '+o.id:o.name||o.id,o.id))];
   if(variant)nodes.push(new Option('自定义档位…','__custom__'));
   else if((backend!=='claude'||loading||info.error)&&value!==blank&&!choices.some(o=>o.id===value))nodes.push(new Option(value+(loading?'（读取中）':'（当前宿主未确认）'),value));
   select.replaceChildren(...nodes);
   select.value=variant&&value&&!choices.some(o=>o.id===value)?'__custom__':value;
   if(variant)control.hidden=select.value!=='__custom__';
   select.dataset.reasoningSignature=signature;
   const note=info.note||'下一次发送生效；可用档位取决于所选模型。';select.title=note;
   let help=control.parentElement.querySelector('[data-reasoning-note]');
   if(!help){help=document.createElement('small');help.dataset.reasoningNote='';help.className='help';control.parentElement.append(help)}
   help.textContent=backend==='claude'?(unconfirmed&&!loading?'已保存的 '+requestedValue+' 未获宿主确认；请重新选择，或跟随 Claude Code。':info.error?'档位暂不可读，可跟随 Claude Code。':''):(info.kind==='host'||info.error?note:'');help.hidden=!help.textContent;
   return true;
  };
  if(control.dataset.reasoningKey!==key)render({options:[]},true);
  control.dataset.reasoningKey=key;
  // briefloop-native validates the model against its registry; 'default' is not registered, so wait for a real pick.
  if(!requests.has(key))requests.set(key,backend==='briefloop-native'&&!model?Promise.resolve({options:[],note:'选择模型后读取该模型的档位。'}):api('runtime/reasoning?backend='+encodeURIComponent(backend)+'&model='+encodeURIComponent(model||'default')));
  return requests.get(key).then(info=>{
   if(control.dataset.reasoningKey!==key)return;
   const changed=render(info);delete control.dataset.reasoningError;
   if(changed)onUpdate(control);
  }).catch(error=>{
   if(control.dataset.reasoningKey!==key)return;
   const changed=render({options:[],error:true,note:'暂时无法读取宿主档位：'+error.message+'。可选择模型默认；重新选择模型可重试。'});
   control.dataset.reasoningError='1';
   if(changed)onUpdate(control);
  });
 }
 function refresh(){requests.clear();revision++}
 return {configure,refresh};
}
