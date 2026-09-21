import profiles from '../src/briefloop/static/runtime-reasoning.json';

export function effortValue(value:any):string|null {
 if(value===null||value===undefined||value===''||value==='none')return null;
 if(typeof value!=='string'||value.length>100||!value.trim())throw Error('无效推理强度');
 return value.trim();
}
export function reasoningProfile(runtime:string):any {
 const p=profiles[runtime]||{kind:'host',note:'当前宿主接口没有提供推理强度设置。'};
 return {...p,options:(p.levels||[]).map((id:string)=>({id,name:id}))};
}
// agy advertises tier-qualified Gemini IDs, but an explicit --effort must use
// the family alias; otherwise the CLI rejects conflicting model/effort flags.
export function reasoningModel(runtime:string,model:string,effort:any):string {
 return runtime==='antigravity'&&effortValue(effort)&&/^gemini-/.test(model||'')
  ?model.replace(/-(low|medium|high)$/,''):model;
}
export function validateEffort(runtime:string,value:any):string|null {
 const effort=effortValue(value);if(!effort)return null;
 const p=reasoningProfile(runtime);
 if(p.kind==='host')throw Error(p.note);
 if(p.kind==='levels'&&!p.levels.includes(effort))throw Error('此宿主不支持推理强度：'+effort);
 return effort;
}
const flatten=(items:any[]):any[]=>items.flatMap(item=>Array.isArray(item?.options)?flatten(item.options):[item]);
export function acpReasoning(options:any):any {
 if(!Array.isArray(options))return null;
 const field=options.find(o=>o.category==='thought_level')||options.find(o=>/^(reasoning[_-]?effort|thinking[_-]?(level|mode)|thought[_-]?level|effort)$/i.test(o.id||''));
 if(!field||!Array.isArray(field.options))return null;
 const choices=flatten(field.options).filter(o=>typeof o?.value==='string').map(o=>({id:o.value==='none'?'off':o.value,name:o.name||o.value,value:o.value}));
 return choices.length?{id:field.id,kind:'levels',options:choices,current:field.currentValue}:null;
}
export async function applyAcpEffort(conn:any,sessionId:string,options:any,value:any){
 const effort=effortValue(value);if(!effort)return;
 const field=acpReasoning(options),choice=field?.options.find((o:any)=>o.id===effort);
 if(!field||!choice)throw Error('宿主未提供所选推理强度，请重新读取档位或选择模型默认');
 const result=await conn.call('session/set_config_option',{sessionId,configId:field.id,value:choice.value});
 const actual=acpReasoning(result?.configOptions);
 if(actual&&actual.current!==undefined&&actual.current!==choice.value)throw Error('宿主没有采用所选推理强度');
}
