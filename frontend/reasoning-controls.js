// One visible selector for every host; the backend supplies host/model choices.
export function settingsEffort(settings,backend){
 if(!['codex','opencode'].includes(backend))return settings.runtime_efforts?.[backend]||'none';
 return Object.hasOwn(settings,'reasoning_effort')?(settings.reasoning_effort||'none'):'high';
}
export function reasoningModel(backend,model,effort){
 return backend==='antigravity'&&effort&&effort!=='none'&&/^gemini-/.test(model)
  ?model.replace(/-(low|medium|high)$/,''):model;
}
export function reasoningControls({api}){
 const requests=new Map();let revision=0;
 const names={off:'关闭思考',on:'开启思考',minimal:'最少',low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高'};
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
  const current=()=>control.value||blank;
  const render=(info,loading=false)=>{
   const value=current(),choices=info.options||[],signature=JSON.stringify([key,choices,value,info.note,loading]);
   if(select.dataset.reasoningSignature===signature)return;
   const nodes=[new Option(loading?'模型默认（正在读取其他档位…）':'模型默认',blank),...choices.map(o=>new Option(names[o.id]?names[o.id]+' · '+o.id:o.name||o.id,o.id))];
   if(variant)nodes.push(new Option('自定义档位…','__custom__'));
   else if(value!==blank&&!choices.some(o=>o.id===value))nodes.push(new Option(value+(loading?'（读取中）':'（当前宿主未确认）'),value));
   select.replaceChildren(...nodes);
   select.value=variant&&value&&!choices.some(o=>o.id===value)?'__custom__':value;
   if(variant)control.hidden=select.value!=='__custom__';
   select.dataset.reasoningSignature=signature;
   const note=info.note||'下一次发送生效；可用档位取决于所选模型。';select.title=note;
   let help=control.parentElement.querySelector('[data-reasoning-note]');
   if(!help){help=document.createElement('small');help.dataset.reasoningNote='';help.className='help';control.parentElement.append(help)}
   help.textContent=info.kind==='host'||info.error?note:'';help.hidden=!help.textContent;
  };
  if(control.dataset.reasoningKey!==key)render({options:[]},true);
  control.dataset.reasoningKey=key;
  if(!requests.has(key))requests.set(key,api('runtime/reasoning?backend='+encodeURIComponent(backend)+'&model='+encodeURIComponent(model||'default')));
  return requests.get(key).then(info=>{
   if(control.dataset.reasoningKey!==key)return;
   render(info);delete control.dataset.reasoningError;
  }).catch(error=>{
   if(control.dataset.reasoningKey!==key)return;
   render({options:[],error:true,note:'暂时无法读取宿主档位：'+error.message+'。可选择模型默认；重新选择模型可重试。'});
   control.dataset.reasoningError='1';
  });
 }
 function refresh(){requests.clear();revision++}
 return {configure,refresh};
}
