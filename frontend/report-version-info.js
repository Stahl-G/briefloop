const engines={'codex':'Codex','briefloop-native':'BriefLoop 内置','claude':'Claude Code','opencode':'OpenCode','mimo':'MiMo'};
const efforts={low:'低',medium:'中',high:'高',xhigh:'极高',max:'最高',ultra:'最高',minimal:'最小',none:'关闭'};
export function versionInformationHTML(brief,{esc}){
 const info=brief.execution_provenance||{},mode=info.mode||({user:'manual',agent:'ai'}[brief.author]||'imported');
 const label=mode==='manual'?'人工编辑':mode==='imported'?'导入稿件':info.action==='revision'?'AI 修订':'AI 生成';
 const config=info.configuration||{};
 const rows=mode==='ai'?[['执行引擎',engines[config.backend]||config.backend||'未记录'],['模型',config.model||'未记录（宿主默认或旧稿）'],['推理强度',efforts[config.effort]||config.effort||'未记录']]:[];
 const origin=info.original_configuration;
 const original=origin&&brief.id!==info.original_version_id?`<details><summary>原始生成配置</summary><dl>${[['执行引擎',engines[origin.backend]||origin.backend||'未记录'],['模型',origin.model||'未记录'],['推理强度',efforts[origin.effort]||origin.effort||'未记录']].map(([k,v])=>`<div class="kv"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl></details>`:'';
 return `<section class="assistant-card report-version-info" aria-label="本稿信息"><h3>本稿信息</h3><p>${label}</p><dl>${rows.map(([k,v])=>`<div class="kv"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}<div class="kv"><dt>保存时间</dt><dd>${esc(brief.created||'未记录')}</dd></div></dl>${original}<p class="muted">${mode==='manual'?'本版由人工保存；原始生成配置不代表本次人工编辑。':mode==='imported'?'导入稿未记录生成配置。':'显示本版保存时的执行配置；未记录项不会使用当前默认补填。'}</p></section>`;
}
