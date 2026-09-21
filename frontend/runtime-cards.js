// Local artwork provenance: THIRD_PARTY_NOTICES.md and third_party/open-design/NOTICE.md.
const brands = {
 codex: ['Codex CLI','OpenAI 开发的命令行 Agent'],
 claude: ['Claude Code','Anthropic 开发的命令行 Agent'],
 opencode: ['OpenCode','开源命令行 Agent · 支持多家模型提供商'],
 codebuddy: ['CodeBuddy Code','腾讯 CodeBuddy 命令行 Agent'],
 hermes: ['Hermes','通过 ACP 连接的命令行 Agent'],
 kimi: ['Kimi CLI','Moonshot AI 的 Kimi 命令行 Agent'],
 mimo: ['MiMo Code','小米 MiMo 命令行 Agent'],
 reasonix: ['DeepSeek Reasonix','DeepSeek 命令行 Agent'],
 aider:['Aider','终端中的 AI 编程助手'],
 amp:['Amp','命令行编程 Agent'],
 amr:['AMR','Open Design 的命令行运行时'],
 antigravity:['Antigravity','命令行开发 Agent'],
 atomcode:['AtomCode CLI','命令行编程 Agent'],
 copilot:['GitHub Copilot CLI','GitHub 的命令行编程助手'],
 'cursor-agent':['Cursor Agent','Cursor 的命令行 Agent'],
 'deepseek-harness':['DeepSeek Harness','DeepSeek 命令行运行时'],
 deepseek:['DeepSeek TUI','DeepSeek 终端交互界面'],
 devin:['Devin for Terminal','Cognition 的终端编程 Agent'],
 'grok-build':['Grok Build','Grok 命令行编程工具'],
 kilo:['Kilo','Kilo Code 命令行 Agent'],
 kiro:['Kiro CLI','Kiro 命令行 Agent'],
 pi:['Pi','命令行编程 Agent'],
 qoder:['Qoder CLI','Qoder 命令行 Agent'],
 qwen:['Qwen Code','Qwen 命令行编程 Agent'],
 'trae-cli':['Trae CLI','Trae 的命令行 Agent'],
 vibe:['Mistral Vibe CLI','Mistral 开源命令行 Agent'],
 zcode:['ZCode','智谱开源的命令行 Agent'],
};
const iconIds=new Set(['codex','claude','opencode','codebuddy','hermes','kimi','mimo','reasonix','aider','amr','antigravity','copilot','cursor-agent','deepseek','devin','grok-build','kilo','kiro','pi','qoder','qwen','trae-cli','vibe']);
const pngIds=new Set(['aider','devin','trae-cli']);
export function runtimeModelSummary(id,chosen,model,catalog,esc){
 const choice=id===chosen?(model||'待选择模型'):'选择后配置模型';
 const source={host:'宿主目录',native_config:'本机配置',builtin_hints:'内置建议',local_routes:'本机路由',host_default_only:'宿主默认'}[catalog?.source];
 return `模型 <strong>${esc(choice)}</strong>${catalog?`<span class="runtime-model-source">${esc(source||'模型目录')} · ${catalog.models.length} 项</span>`:''}`;
}
export function runtimeCard(r,{chosen,model,catalog,esc,compact=false}){
 const brand=brands[r.id],name=brand?.[0]||r.name;
 const iconId=r.id==='deepseek-harness'?'deepseek':r.id;
 const icon=iconIds.has(iconId)?`<img src="/runtime-${iconId}.${pngIds.has(iconId)?'png':'svg'}" width="40" height="40" alt="" draggable="false">`:'<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m6 9 3 3-3 3m6 0h6"/></svg>';
 const protocol={acp:'ACP','claude-stream-json':'流式 JSON','opencode-json':'OpenCode JSON','native-manager':'原生宿主接口'}[r.protocol]||r.protocol;
 const capabilityNames={chat:'对话',cancel:'停止任务',resume:'续接',images:'图片',questions:'提问',steer:'运行中补充',restricted_reviewer:'受限审阅'};
 const supported=Object.entries(capabilityNames).filter(([key])=>r.capabilities?.[key]===true).map(([,name])=>name);
 const status=r.available?'已检测到':r.installed?'尚未接入':'未安装';
 return `<article data-runtime-card="${esc(r.id)}" class="runtime-card ${r.id===chosen?'selected':''} ${compact?'runtime-missing':''}"><div class="runtime-card-head"><span class="runtime-icon" aria-hidden="true">${icon}</span><div class="runtime-identity"><h3>${esc(name)}${r.id===chosen?'<span class="runtime-selected">当前选择</span>':''}</h3><p class="runtime-description">${esc(brand?.[1]||'本机命令行工具')}</p><p class="runtime-version">${esc(r.installed?(r.version||'版本未确认'):'本机未检测到')} <span class="runtime-detection">${status}</span></p></div>${compact?'':`<div class="runtime-actions"><button type="button" class="outline" data-runtime-select="${esc(r.id)}" ${r.available?'':'disabled'}>${r.id===chosen?'已选择':r.available?'选择':status}</button>${r.available?`<button type="button" class="outline" data-runtime-test="${esc(r.id)}" ${r.id!==chosen?'disabled':''}>测试</button>`:''}</div>`}</div>${compact?`<p class="runtime-missing-info">${esc((r.bins||[]).join(' / ')||r.id)} · ${r.integrated?'已有适配 · 安装后可测试':'尚无协议适配'}</p>`:`<p class="runtime-current-model" data-runtime-model="${esc(r.id)}">${runtimeModelSummary(r.id,chosen,model,catalog,esc)}</p>`}<details><summary>安装与能力</summary><p class="runtime-path">${esc(r.path||'未安装')}</p>${protocol?`<p>连接方式：${esc(protocol)}</p>`:''}${r.available&&supported.length?`<p>已接入：${esc(supported.join('、'))}</p>`:''}<p>${esc(r.diagnostic||(r.installed?'已检测到本机 CLI；账号与模型的可用性可通过“测试”确认。':'未在本机找到此 CLI。安装后可重新检测；接入状态以 BriefLoop 实际支持为准。'))}</p></details></article>`;
}
