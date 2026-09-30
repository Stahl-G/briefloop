/** Model declarations are independent of credentials and of other models. */
export function createProviderCapabilities({$}) {
  const field=()=>$('custom-supports-reasoning');
  function reset(engine) {
    field().value='';
    $('custom-reasoning-capability').hidden=engine!=='native';
    $('provider-save-help').textContent=engine==='native'
      ?'保存同名 Provider 会更新 BriefLoop Agent 的本机接口地址；填写 Key 会更新该 Provider 的本机凭据。仅保存配置，不测试推理、不启动任务。'
      :'保存同名 Provider 会更新本机 OpenCode 的接口地址；填写 Key 会更新该 Provider 的本机凭据。仅保存配置，不测试推理、不启动任务。';
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
