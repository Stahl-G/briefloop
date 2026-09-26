/** Model declarations are independent of credentials and of other models. */
export function createProviderCapabilities({$}) {
  const field=()=>$('custom-supports-reasoning');
  function reset(engine) {
    field().value='';
    $('custom-reasoning-capability').hidden=engine!=='native';
  }
  function load(config,engine) {
    reset(engine);
    if(engine==='native')field().value=typeof config?.supports_reasoning==='boolean'?String(config.supports_reasoning):'';
  }
  function read(engine) {
    if(engine!=='native')return {};
    const value=field().value;
    if(!['','true','false'].includes(value))throw Error('请选择模型推理能力');
    return {supports_reasoning:value===''?null:value==='true'};
  }
  return {reset,load,read};
}
