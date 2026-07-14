# WorkBuddy

BriefLoop 的 WorkBuddy surface 是一个实验性的本地 Skill adapter。它帮助
WorkBuddy 操作者安装 BriefLoop Skill 包、创建或打开 workspace、运行确定性的
BriefLoop CLI transaction、调用 CodeBuddy-compatible role agents 起草 handoff
分配的 artifacts、查看 status 和 Quality Panel，并避免手改控制文件。

这不是新的 BriefLoop authority layer。它不证明语义真实性，不批准交付，不授权
release，不发布报告；除非宿主真实 delegated 对应 role agent，否则不能声称
WorkBuddy / CodeBuddy role agent 已经运行。

## 支持状态

| Surface | 状态 | 边界 |
|---|---|---|
| WorkBuddy Skill source bundle | Experimental | 位于 `.agents/skills/briefloop-workbuddy/` 的 source-clone-only 文件 |
| CodeBuddy project Skill adapter | Experimental | 位于 `.codebuddy/skills/briefloop/` 的 source-clone-only project Skill；只负责主会话编排 |
| CodeBuddy project role agents | Experimental | 位于 `.codebuddy/agents/briefloop-*.md` 的 source-clone-only role agents；只起草 handoff 分配的 artifacts |
| CodeBuddy runtime handoff | Experimental | `--runtime codebuddy` 生成 CodeBuddy-specific handoff；确定性 CLI transactions 仍由主会话负责 |
| 本地 WorkBuddy Skill zip | Experimental | 由 `& $BriefLoop workbuddy pack-skill` 生成；不是 WorkBuddy Marketplace 发布 |
| WorkBuddy Assistant trigger | Experimental template | 远程提示模板，应转入已安装 Skill 的本地 WorkBuddy session |
| WorkBuddy role-agent orchestration | Experimental | 使用 `--runtime codebuddy`；除非 WorkBuddy / CodeBuddy 真实调用 checked-in role agent，否则不能声称 role delegation 发生过 |

当前支持边界是可追溯性和过程问责。语义证明、输出质量提升证明、交付批准和
release 批准都不是当前支持声明；这不授权 release。

## Windows WorkBuddy Pilot 路径

只选择一次 PowerShell，并在整次会话中绑定同一个绝对 CLI 路径：

```powershell
$ErrorActionPreference = "Stop"
$BriefLoopCommand = Get-Command `
  -Name briefloop `
  -CommandType Application `
  -ErrorAction Stop |
  Select-Object -First 1
$BriefLoop = $BriefLoopCommand.Path
if ($BriefLoop -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\)') {
  throw "BriefLoop application path is not fully qualified."
}
& $BriefLoop version
py -3 --version
git --version
```

`py -3 --version` 只是诊断，不证明 `$BriefLoop` 的 Python 身份。doctor、run、
secrets import 和 diagnose 必须始终调用同一个 `$BriefLoop`。不要自动混用或
回退到 `bash`、`which`、`command -v`、`export`、`/c/Users/...`、
`source .venv/bin/activate` 或 `bash scripts/setup.sh`。如果实际宿主是 Git Bash，
报告真实 shell 并停止这条 PowerShell 路径；不要猜测转换路径或混用两种 shell。
这条路径不声明 Git Bash 支持。

Tavily 先记录用户的搜索选择。只有新工作区创建成功后，或验证已有工作区存在
后，才能导入 workspace secret。不得在 `& $BriefLoop new` 之前运行
`secrets import`。

## 从 Source Clone 安装

在 BriefLoop source checkout 中运行：

```powershell
py -3 scripts/check_workbuddy_skill_pack.py
& $BriefLoop workbuddy pack-skill --output dist/workbuddy
```

这会写出本地 Skill zip 和 manifest，例如：

```text
dist/workbuddy/briefloop-workbuddy-skill-v0.12.1.zip
dist/workbuddy/briefloop-workbuddy-skill-v0.12.1.manifest.json
```

这个 zip 是确定性、public-safe 的本地包。它不是 Python package data，也不是
WorkBuddy Marketplace release。

通过 WorkBuddy 的本地 Skill 导入流程安装生成的 zip。如果你的 WorkBuddy 版本要求
导入文件夹而不是 zip，使用仓库里的 source folder：

```text
.agents/skills/briefloop-workbuddy/
```

如果你的 CodeBuddy 版本按官方 project Skill 和 project sub-agent 目录发现能力，
使用仓库里的 project adapter：

```text
.codebuddy/skills/briefloop/
.codebuddy/agents/briefloop-*.md
```

CodeBuddy Skill 是主会话编排 adapter。不要添加 `context: fork`；BriefLoop
Skill 必须留在 main CodeBuddy session，这样它才能显式调用 role sub-agents，
然后由主会话运行确定性的 BriefLoop CLI transactions。

WorkBuddy 用户应安装或打开 BriefLoop WorkBuddy Skill。不要把第一次使用
WorkBuddy 的用户指向 `.agents/skills/briefloop/`；那是给 coding agent 和
BriefLoop 维护者看的 repo operator protocol，不是 WorkBuddy first-user
adapter。

## 第一次使用

当用户说“跑周报”或“生成行业简报”时，Skill 应先判断请求类型：

- existing workspace：询问本地文件夹路径；
- first-time run：解释 BriefLoop workspace 是这个报告项目的本地文件夹，建议
  `~/BriefLoop/<topic-slug>` 这类安全路径，并在创建前取得明确确认。

确认后，按用户语言选择产品入口：

| 用户请求 | Product entry |
|---|---|
| 周报、行业、市场、竞品、weekly、industry、market、competitor | `industry-weekly` |
| 管理月报、月报、management monthly | `management-monthly` |
| 文件审阅、PDF 审阅、document review、file review | `document-review` |

如果用户打开在线搜索，强烈推荐 Tavily，并使用：

```powershell
& $BriefLoop new industry-weekly "<workspace>" --search-backend tavily
```

如果用户拒绝在线搜索，使用：

```powershell
& $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
```

只有用户已启用 Tavily 时，才在工作区存在后持久导入 key，且不显示密钥。
如果用户已禁用在线搜索，跳过此导入：

```powershell
$SecretSource = Join-Path $HOME ".briefloop-secrets.env"
if (-not (Test-Path -LiteralPath $SecretSource -PathType Leaf)) {
  throw "Create the user-confirmed private secret file before importing Tavily."
}
& $BriefLoop secrets import `
  --workspace "<workspace>" `
  --from $SecretSource `
  --keys TAVILY_API_KEY `
  --json
```

`$SecretSource` 必须是用户确认的私有文件。如果只有环境变量、没有该文件，停止并
指导用户先创建私有文件；不得打印、自动复制或在命令行展开密钥，也不得提交密钥、
临时修改 PATH，或把 key 注入单条命令。

只有在 source checkout 中存在 CodeBuddy project assets 时，才运行
`--runtime codebuddy`：

```text
.codebuddy/skills/briefloop/SKILL.md
.codebuddy/agents/briefloop-*.md
```

本地 WorkBuddy Skill zip 本身不会安装这些 `.codebuddy/` project assets。

`solar-periodic` 仍是实验性入口，使用前必须说明它是 experimental。

### 默认搜索

首次运行前，WorkBuddy 应先询问：

```text
是否要打开在线搜索？如果要打开搜索，强烈建议添加 Tavily API。
```

BriefLoop 推荐在线搜索使用 Tavily，但生成的 workspace 默认保持
`configure_later`，除非用户明确打开在线搜索。用户选择打开在线搜索时，默认先使用
Tavily，并且只验证 `TAVILY_API_KEY` 是否存在。不要显示 key 的值。只有用户明确要求
替代 provider 时，才介绍 Exa、Brave、Firecrawl 或 Serper。
如果用户选择不开启在线搜索，应在来源发现前显式关闭 web search。

## 操作规则

只有当 source checkout 中存在 CodeBuddy project Skill 和 role-agent assets
时，WorkBuddy 完整工作流才使用 `--runtime codebuddy`：

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
```

这会写出 CodeBuddy-specific handoff，包含明确的 role-agent 名称和
`runtime_capabilities` metadata。它仍是 experimental，不新增 gate、delivery、
release 或 semantic-proof authority。

main WorkBuddy / CodeBuddy session 显式调用：

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

这些 role agents 只能起草当前 handoff 分配的 artifacts。它们不运行 BriefLoop
CLI，不完成 stage，不运行 gates，不冻结 Claim Ledger，不 finalize / complete
finalize，不批准或汇报 delivery，也不授权 release。role 返回不等于 stage
通过；每个 role 返回后，确定性 transactions 与 diagnose 仍由 main session 负责。

角色工作前读取两个 handoff 文件，并核对 handoff runtime 与 capability runtime
都是 `codebuddy`、`delegation_supported=true`、`subagent_names` 精确等于上述
七个角色，而且 canonical source checkout 含精确角色资产。capability flag 只是
声明，不是运行证据；只有宿主可见的精确角色调用与返回才算。generic Team、
Expert、helper、Send Message 或叙述标签都不算。

Formatter 只是只读 finalize-readiness reporter。它不得运行 Bash/PowerShell/CLI、
转换 Markdown-to-DOCX、写 reader delivery artifacts，或声称 reader-clean、
gate/finalize/delivery 成功。

声称“正式 finalize 管线已完成”必须同时观察到当前 run 的全部确定性证据：
handoff/diagnose 已授权且当前 workspace-config 绑定的 finalize 事务成功、结构有效的
`finalize_report.json`、reader-clean
pass、`delivery_promotion == promoted`、当前 `render_transaction_id`、finalize
quality gate pass、handoff/diagnose 已授权且当前 workspace 绑定、记录了 reason 的
finalize-complete 事务成功、diagnose 中当前
finalize event、valid delivery truth，以及准确报告的 delivery outcome。
`delivery_truth.valid=true` 只是资格，不是交付发生证据。

WorkBuddy 或 generic helper 在该生命周期之外手写的 Markdown/DOCX 只能标为
`draft/manual/unverified`。不得移动、改名或描述为正式 BriefLoop delivery，
不得声称 residue 已清理、模板已渲染，或后续口头回复已经修复。若含 `CL-*`、
`SRC-*`、`Claim Ledger`、本地路径或其他 forbidden reader residue，必须报告，
停止交付声明并跟随确定性 repair/finalize；不得手改 frozen artifact 或绕过 gate。

如果这些 role agents 不可用，必须在完整工作流执行前停止。仍可运行确定性的
setup、status、quality、delivery draft 或 demo 命令，但不能手写 BriefLoop workflow
JSON artifacts，也不能静默回退到 `--runtime operator`。

如果已有 operator handoff、但用户要求 subagents，停止使用它，以
`--runtime codebuddy --repo-workdir` 重新生成并重读两个 handoff 文件。不得
继续 operator 同时承诺以后委派；operator 不得声称任何 `briefloop-*` 角色已运行。

每次运行 BriefLoop CLI 命令后，WorkBuddy operator 应重新阅读：

```text
output/intermediate/agent_handoff.md
output/intermediate/agent_handoff.json
```

每个 stage 或 role-owned artifact action 前，也要重新阅读对应 handoff step。
这样可以避免 WorkBuddy 把 BriefLoop 当成手写 JSON 的流程。

每次启动、CLI、role 返回或中断后：重读 handoff -> diagnose -> 跟随当前动作 ->
仅当当前动作明确指派 role-owned draft work 时调用该精确角色；如果当前动作是
deterministic-only，不调用任何角色，由主会话直接运行获授权事务 -> 再 diagnose。
raw workflow state、
event log、Registry、时间戳和文件存在性只作审计证据，不能重构 next action、
gate、finalize 或 delivery 真值。例如：

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
Quality Panel 已生成。
```

把 role execution 与 workflow success 分开。只有当前 handoff step 中出现
host-visible 的精确角色调用和返回，才能说“`briefloop-analyst` role 已返回”；
只有当前确定性 stage/transaction truth 才能说“Analyst stage 已完成”。
`briefloop-auditor` 同理：可见返回只证明 role 执行，audit/gate 成功必须读取
当前确定性 verdict/status。匹配 artifact、stale event、manual file 或旧
transaction 单独都不能证明这些事实。

### Run Card 和硬停止规则

每次关键 CLI 命令、role 返回、repair、gate check、finalize 尝试、Quality
Panel 或打包/导出请求后，WorkBuddy 都应该展示只来自机器事实的 Run Card：

```text
runtime:
current_stage:
run_integrity:
recovery_status:
recovery_action:
blocked:
latest_gate_status:
finalize_report:
delivery_truth:
delivery_event:
next_allowed_action:
```

硬停止条件：

- `& $BriefLoop doctor` 有任何 error：停止，展示完整 doctor 输出、workspace
  路径、当前用户、output 路径存在/可写结果、权限或 ACL 证据；
  修复环境/配置并由同一 `$BriefLoop` 重新通过前始终是 error；
  `request_human_review`、用户确认或另一环境中的 standalone pass 不能覆盖。
  diagnose 可以展示为证据，但 `doctor.status=not_run_read_only` 不能清除、替代
  或绕过已观测失败，也不得执行其 completion action。中断后或会话连续性不确定
  时，继续前用同一 `$BriefLoop`、workspace 和 config 重跑 doctor；
- 不得从 `run_integrity` 推断 recovery、finalize 或 delivery 路由。只跟随
  diagnose 投影的 `recovery_status`、`recovery_action`、
  `next_allowed_action` 和当前 gate/finalize/delivery truth。恢复完成的
  non-reference run 可以继续保持 contaminated，同时由 diagnose 允许有界交付；
- completion projection 的 `delivery_truth.valid` 不是 `true`：
  不能说交付完成，也不能导出 delivery package。只有
  `output/intermediate/audited_brief.md` 存在时，才能说
  有草稿；否则应说目前还没有草稿或交付产物。这是 finalize 之前的正常状态，本身
  不阻止更早的 handoff-assigned stages；
- 打包、导出、附件候选包含 `.env`、token、private planning 文件或机器密钥：
  停止，丢弃该包，并建议轮换暴露的 key。

不要分享整个 workspace zip。只使用 BriefLoop 生成的 delivery bundle 或 audit
bundle。需要支持时，只分享人工检查过、确认不含密钥的
`& $BriefLoop status --json` 或 doctor 输出摘录。

## 只读诊断

当 WorkBuddy 需要紧凑、机器可读的 Run Card，而不是自己解释多个控制 JSON
文件时，使用：

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
```

这个命令是 canonical completion projection 上的只读 adapter，并且只对
`next_allowed_action` 加 WorkBuddy-only doctor/secret safety overlay。它报告
doctor 状态、runtime、current stage、run integrity、blocked 状态、latest gate
status、invalid/stale artifacts、finalize truth、delivery truth、`.env` 非空 key
等 secret-risk flags，以及 next safe action。它不根据文件存在推断 delivery；
除非 projection 显示 `delivery_truth.valid=true`，WorkBuddy 不能把
`output/delivery/` 或 `finalize_report.json` 存在当成交付真实状态。它不运行
gates、不修复 artifacts、不批准 delivery、不授权 release，也不证明语义真实。

## Assistant Trigger 模板

Assistant 模板在这里：

```text
integrations/workbuddy/assistant/briefloop-assistant-prompt.md
```

它只能作为远程 trigger，把用户请求转入已经安装 BriefLoop Skill 的本地
WorkBuddy session。它不是云端 BriefLoop runtime；没有人工命令和当前 gate status
时，不能 finalize、deliver、approve 或 publish。

## Manual Smoke Checklist

WorkBuddy dogfood 时使用这个手动 smoke checklist：

```text
docs/workbuddy-smoke-checklist.md
```

这个 checklist 是实验性 integration smoke 路径。它不是 runtime proof、delegated-agent
proof、输出质量证明、语义证明、交付批准或 release 批准。
