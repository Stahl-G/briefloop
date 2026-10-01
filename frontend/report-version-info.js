const engines={codex:'Codex','briefloop-native':'BriefLoop 内置',claude:'Claude Code',opencode:'OpenCode',mimo:'MiMo'};
const efforts={low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高',ultra:'最高',minimal:'最小',none:'宿主默认',off:'关闭'};
function configurationRows(config,esc){
 config=config||{};const reported=config.reported||{};
 return [['backend','执行引擎'],['model','模型'],['effort','推理强度']].map(([key,label])=>{
  const actual=!!reported[key],value=actual?reported[key]:config[key];
  const text=key==='backend'?(engines[value]||value):key==='effort'?(efforts[value]||value):value==='default'?'宿主默认':value;
  return `<div class="kv"><dt>${label}${value?(actual?' · 宿主确认':' · 请求'):''}</dt><dd>${esc(text||'未记录')}</dd></div>`;
 }).join('');
}
export function versionInformationHTML(brief,{esc}){
 const info=brief.execution_provenance||{},mode=info.mode||({user:'manual',agent:'ai',import:'imported',example:'example'}[brief.author]||'unknown');
 const label=mode==='manual'?'人工编辑':mode==='imported'?'导入稿件':mode==='example'?'示例稿件':mode==='unknown'?'作者方式未记录':info.action==='revision'?'AI 修订':'AI 生成';
 const origin=info.original_configuration;
 const original=origin&&brief.id!==info.original_version_id?`<details><summary>原始生成配置</summary><dl>${configurationRows(origin,esc)}</dl></details>`:'';
 return `<section class="assistant-card report-version-info" aria-label="本稿信息"><h3>本稿信息</h3><p>${label}</p><dl>${mode==='ai'?configurationRows(info.configuration,esc):''}<div class="kv"><dt>保存时间</dt><dd>${esc(brief.created||'未记录')}</dd></div></dl>${original}<p class="muted">${mode==='manual'?'本版由人工保存；原始生成配置不代表本次人工编辑。':mode==='imported'?'导入稿未记录生成配置。':'请求为本版冻结的设置；仅宿主回执确认的字段标为宿主确认。未记录项不补当前默认。'}</p></section>`;
}
