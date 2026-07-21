---
name: briefloop-workbuddy
description: Operate BriefLoop from WorkBuddy through CodeBuddy-compatible role subagents and deterministic CLI transactions. Use for requests like "跑周报", "生成行业简报", "运行简报", "briefloop", "industry weekly", or "market brief".
---

# BriefLoop WorkBuddy Skill

## Scope

当 WorkBuddy 用户想要创建、打开、查看、运行、修复或总结一个 BriefLoop
工作区时，使用本 Skill。当用户用自然语言提出"跑周报"、"生成行业简报"、
"运行简报"、"帮我做市场简报"这类请求、并期望由 BriefLoop 驱动简报工作流时，
也使用本 Skill。

本 Skill 是围绕 BriefLoop CLI、CodeBuddy 项目角色代理和工作区工件的
面向 WorkBuddy 的适配层。它不是新的 BriefLoop 权威层：它不证明语义真实性、
不批准交付、不自行执行 gate，也不在 WorkBuddy/CodeBuddy 没有真正委派时
声称角色子代理已运行。本源码包只能从 BriefLoop 源码检出获得；在打包命令
补齐之前，Python wheel/sdist 包安装不包含这些 WorkBuddy 文件。

## Purpose

帮助 WorkBuddy 用户通过确认过的本地工作区、兼容 CodeBuddy 的角色子代理、
`briefloop` CLI 事务和生成的 handoff 工件来操作 BriefLoop，而不把
WorkBuddy 的对话文字变成运行时权威。

## Use When

当 WorkBuddy 会话中的请求涉及周报、行业简报、市场简报、文档审阅、已有
BriefLoop 工作区的查看、修复、状态、质量摘要或交付准备时使用。如果用户只是
在改 BriefLoop 源码，请改用仓库开发技能。

## Inputs

- 用户的报告主题，或已有工作区路径。
- 当前生效的 `briefloop` 命令路径和版本。
- 已存在的工作区文件、status 输出和生成的 handoff 工件。
- 创建新工作区或执行交付前，用户的明确确认。

## Outputs

- 用户可以自行核查的 BriefLoop CLI 命令。
- 基于当前 handoff、`& $BriefLoop status --workspace "<workspace>" --json`
  （Store-native status 投影）和
  `& $BriefLoop runtime next --workspace "<workspace>"` 的确定性进度摘要。
- 仅在 handoff 指派时产出的角色子代理草稿工件。
- 绝不直接编辑 Python 拥有的控制文件或冻结工件。

## 首检（First Checks）

操作工作区之前：

1. Windows WorkBuddy 路径只使用 PowerShell。先绑定一个绝对 CLI 路径，并在
   本次会话的 bootstrap、doctor、run、secrets import、status 和 runtime next
   中始终复用：

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

   `py -3 --version` 只是诊断信息，不证明 `$BriefLoop` 使用了该 Python。
   Windows 路径不得自动混用或回退到 `bash`、`which`、`command -v`、`export`、
   `/c/Users/...`、`source .venv/bin/activate` 或 `bash scripts/setup.sh`。
   如果宿主实际暴露的是 Git Bash，报告真实 shell，停止这里的 PowerShell
   路径，不猜测转换路径，也不混用 PowerShell 命令与 Git Bash 路径。本 Skill
   目前不声明 Git Bash 支持。

2. 向用户报告解析出的二进制路径和版本。
3. 如果 `briefloop` 不可用，请用户先激活源码检出的虚拟环境或完成安装。
4. 正确使用首跑搜索默认值：
   - 询问用户是否要为这个工作区开启在线搜索；
   - 如果开启在线搜索，强烈推荐 Tavily，并先检查 `TAVILY_API_KEY`；
   - 如果启用了 Tavily 搜索但 `TAVILY_API_KEY` 缺失，说明需要先配置密钥
     才能进行在线信源发现；
   - 如果用户拒绝在线搜索，在继续之前显式关闭 web 搜索；
   - 只有当用户主动问起其他提供商时才提及 Exa、Brave、Firecrawl 或 Serper；
   - 绝不打印 API key 的值；只报告对应环境变量是否存在。
5. 如果用户没有给出工作区路径，不要只问"工作区在哪里？"，先分类：
   - 已有工作区：请用户给出文件夹路径；
   - 首次运行：主动提出创建一个。
6. 向用户解释：BriefLoop 工作区就是这份报告项目的本地文件夹。创建之前，
   请用户对目标路径做出明确确认。
7. 创建工作区时使用产品入口，并把用户的搜索选择写明：

   ```powershell
   # 用户开启在线搜索；强烈推荐 Tavily
   & $BriefLoop new industry-weekly "<workspace>" --search-backend tavily
   & $BriefLoop new management-monthly "<workspace>" --search-backend tavily
   & $BriefLoop new document-review "<workspace>" --search-backend tavily
   & $BriefLoop new solar-periodic "<workspace>" --search-backend tavily

   # 用户拒绝在线搜索
   & $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
   & $BriefLoop new management-monthly "<workspace>" --web-search-mode disabled
   & $BriefLoop new document-review "<workspace>" --web-search-mode disabled
   & $BriefLoop new solar-periodic "<workspace>" --web-search-mode disabled
   ```

`solar-periodic` 是实验性产品入口，使用前要先说明。

## 搜索默认值

BriefLoop 推荐用 Tavily 做在线搜索，但生成的工作区在用户明确开启之前，
在线搜索保持 `configure_later`。首次运行时询问用户：

```text
是否要打开在线搜索？如果要打开搜索，强烈建议添加 Tavily API。
```

如果用户开启在线搜索，把密钥保存在工作区环境中，不要用临时 `export`、
一次性 PATH 修改或单条命令注入密钥：

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
指导用户先创建私有文件；不要打印、自动复制或在命令行展开密钥。只验证
`TAVILY_API_KEY` 是否存在，不要显示或提交密钥内容。除非用户明确要求
替代方案，不要让用户在 Tavily、Exa、Brave、Firecrawl、Serper 之间做选择。

如果用户拒绝在线搜索，用 `--web-search-mode disabled` 或
`web_search.enabled: false` 显式关闭。

## 运行模式

完整的 BriefLoop 工作区通过 CodeBuddy 运行时执行：

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
```

只有当源码检出包含 `.codebuddy/skills/briefloop/SKILL.md` 和
`.codebuddy/agents/briefloop-*.md` 时才使用 `--runtime codebuddy`。
仅有本地 WorkBuddy Skill zip 不会安装这些 CodeBuddy 项目资产。

完整执行要求 CodeBuddy/WorkBuddy 主会话本身具有可调用 `briefloop` 的命令执行
能力；宿主可以把它显示为终端、Shell 或等价工具，不要求 UI 中一定叫
`Bash`。委派角色工作前先读
`references/workbuddy-delegation.md`。CodeBuddy/WorkBuddy host 只要加载了
源码检出中的项目角色，就使用同一组 `.codebuddy/agents/briefloop-*.md`
定义，并按精确角色名显式调用。

Scout、Screener、Claim Ledger、Analyst、Editor 和 Auditor 角色声明
`Read, Write, Grep, Glob`；Formatter 是只读 readiness reporter，只声明
`Read, Grep, Glob`。所有角色都故意不提供 `Bash`：角色负责 handoff 指派的
草稿，返回后由拥有命令执行能力的主会话负责验证、gate、state、finalize、
交付和 quality CLI 事务。角色不持有 Bash 不会禁用主会话的控制面；两者是
分开的权限域。

当 handoff 指派对应阶段时，严格使用这些角色名：

- `briefloop-scout`
- `briefloop-screener`
- `briefloop-claim-ledger`
- `briefloop-analyst`
- `briefloop-editor`
- `briefloop-auditor`
- `briefloop-formatter`

签入的角色定义位于：

```text
.codebuddy/agents/briefloop-*.md
```

在 CodeBuddy/WorkBuddy 中显式调用时，使用类似下面的指令，并核对宿主确实
显示了对应角色的调用和返回，而不是仅在正文中声称已经委派：

```text
使用 briefloop-scout 子代理执行当前 BriefLoop handoff 指派的 Scout 工作；
完成后把写入的工件路径返回主会话。
```

如果主会话不能调用 `briefloop`，完整工作流无法执行，应在任何角色工作或状态
推进前停下，并改用具有命令执行能力的 CodeBuddy/WorkBuddy 环境。如果当前
host 能执行 CLI、但无法派发这些项目角色，则在 codebuddy 完整工作流执行之前
停下。
你仍可以运行确定性 setup、`status`、`state check`、`quality summarize`、
`doctor` 或 demo 命令，但在 codebuddy handoff 下不要退回主会话代写角色工件，
不要静默切换到 `--runtime operator`，也不要修改角色 frontmatter 的 tools
清单来绕过设计。

向用户说明限制。下一步必须由用户明确决定：在具有项目角色派发能力的
CodeBuddy/WorkBuddy 会话中继续，或改用 operator 运行时重新生成 handoff：

```powershell
& $BriefLoop run --workspace "<workspace>" --runtime operator
```

operator handoff 是主机无关的紧凑工作流，明确允许主会话亲自起草角色工件
（operator-authored artifact work），不假设也绝不声称子代理运行过。用户
同意后按新生成的 operator handoff 逐步执行；用户不同意则停在当前状态并
输出 Run Card。如果已有 operator handoff、但用户要求子代理，立即停止使用
该 handoff，重新生成 codebuddy handoff，并重读两个 handoff 文件；不得一边
继续 operator 一边承诺稍后调用子代理。operator handoff 永远不能声称任何
`briefloop-*` 角色已经运行。

在每个 stage 或角色工件动作之前、以及每条 BriefLoop CLI 命令之后，先重新
打开 `output/intermediate/agent_handoff.md` 和
`output/intermediate/agent_handoff.json` 中相应的步骤再继续。不要跳过
handoff 步骤；除非 WorkBuddy/CodeBuddy 确实委派并记录了那个角色，不要声称
某个子代理已经运行。

每次启动、每条 CLI、每个角色返回和任何中断之后都使用同一顺序：重读
`agent_handoff.md/json` -> 运行 `& $BriefLoop status --workspace
"<workspace>" --json` 和 `& $BriefLoop runtime next --workspace "<workspace>"` ->
跟随 handoff/status/runtime next 的当前动作 -> 仅当当前动作明确
指派 role-owned draft work 时调用该精确角色；如果当前动作是 deterministic-only，
不调用任何角色，由主会话直接运行获授权事务 -> 再次 status。raw `workflow_state.json`、
`event_log.jsonl`、Registry、时间戳或文件存在性只能作为审计证据，不能替代
动作路由，也不能用于重构 gate、finalize、delivery 或 next action 真值。

## Run Card 协议

在每个关键 CLI 命令、角色返回、repair 动作、gate 检查、finalize 尝试、
quality 摘要或打包/导出请求之后，打印一张机器事实 Run Card。不要用自由
发挥的"已完成"总结代替 Run Card。

严格使用这些字段，未知值填 `unknown` 而不是猜测：

```text
runtime:
current_stage:
terminal_state:
package_ready:
delivered:
store_revision:
next_action:
```

这些值从 `& $BriefLoop status --workspace "<workspace>" --json`（Store-native
status 投影）和 `& $BriefLoop runtime next --workspace "<workspace>"` 读取。
status 投影的 `terminal_state`、`package_ready`、`delivered` 字段带 receipt
绑定（`projection_source` 含 `store_revision` 与 receipt ids）；流程推进真值
（`next_action`）来自 `runtime next`。不要从
`workflow_state.json`、`event_log.jsonl`、投影文件或文件存在性检查重构交付、gate、
finalize 或下一步动作的真值。legacy completion projection /
`workbuddy diagnose` 面已退役，不要再调用它。
`package_ready=true` 只表示当前 run 的 reader
package 已就绪、可以进入交付决策，不表示交付已经发生。只有 status 投影报告当前
run 的 `delivered=true`（即 `terminal_state=delivered`）才允许声称已交付；
`terminal_state=draft_created` 表示草稿已创建，不是 delivered。
仅当角色专属草稿工件（例如
`output/intermediate/audited_brief.md`）确实存在时才说 run 里有草稿；
否则说目前既没有草稿也没有交付。

`run_integrity` 等完整性判定字段同样只能引用 status 投影的输出；
完整性由 Python 判定，不要根据自己的操作推断或自行宣布 contamination。
恢复与下一步动作必须读取 `runtime next` 给出的当前动作与原因。不要从
`run_integrity` 推断恢复进度或下一步。

## 硬停规则

只在会让所请求动作不安全的条件下立即停止，并只展示机器证据。不要把
finalize 之前的正常状态当作流程停止。

1. `& $BriefLoop doctor` 报告任何错误。展示完整 doctor 输出、实际工作区路径、
   当前用户、输出路径存在性/可写性检查结果、以及平台权限/ACL 输出。不要在
   叙述里降级该错误。必须修正环境或配置并重新运行 doctor 成功后才能继续。
   人类可以补充缺失路径、凭据或环境证据，但任何确认都不能改变 doctor 真值；
   先前在另一 shell、PATH、用户或环境中通过的 standalone doctor 也不能替代
   本次 `$BriefLoop` 执行上下文。中断后或会话连续性不确定时，
   继续前用同一 `$BriefLoop`、workspace 和 config 重新运行 doctor。
2. 对 finalize、交付、导出或分享请求：
   - 不要仅凭 `run_integrity` 决定恢复动作。恢复中的 run 永久保持
     `run_integrity=contaminated` 和 `reference_eligible=false`；只执行
     `runtime next` / handoff 给出的当前动作指定的受控事务。
   - status 投影或 handoff 报告恢复未终结（例如 `awaiting_recovery`、
     `repair_in_progress`、`downstream_rerun_pending` 或
     `invalid_recovery_state`）时，不要交付、导出或
     分享；按机器给出的恢复动作处理。
   - 恢复状态为 `finalize_render_required` 时只能按投影运行 finalize；
     `finalize_completion_pending` 时只能完成当前绑定的 gate /
     finalize-complete。
   - 恢复状态为 `completed_non_reference` 时不要再次运行 finalize；仅当
     status 投影报告 `package_ready=true` 时才可本地交付，并且永久不具备
     reference 资格，否则停止交付。
   对于早期阶段的草稿工作，报告 Run Card，并只继续 handoff 允许的非交付
   工作流步骤。
3. 对交付、导出、分享或完成声明：如果 status 投影没有报告
   `package_ready=true`，停止交付动作。如果 status 投影没有报告当前 run 的
   `delivered=true`，不要说 "delivered"、"交付完成"或
   "delivery complete"；对 `terminal_state=draft_created` 只能说草稿已创建。
   仅当 `output/intermediate/audited_brief.md` 存在时才说有草稿；否则说
   目前既没有草稿也没有交付。
   只有 handoff 和 Run Card 允许时才继续更早的角色工作阶段。
4. 任何导出、分享、打包、zip 或附件候选包含 `.env`、token、私有规划文件
   或机器密钥。停止，告诉用户移除该包，并建议轮换任何暴露的密钥。绝不分享
   整个工作区 zip。

## Formatter 与 Finalize

`briefloop-formatter` 只是只读的 finalize-readiness reporter。它不得运行
Bash、PowerShell 或任何 BriefLoop CLI，不得执行 Markdown-to-DOCX 转换，
不得写 reader delivery artifacts，也不得声称 reader-clean、finalize 成功、
交付成功或 gate 通过。Formatter 返回后，只有主会话可以按照最新 handoff 与
status 投影运行正式 `finalize`、finalize gate 和 finalize-complete 事务；角色返回
本身永远不等于阶段或事务通过。

只有当前 run 同时观察到以下全部确定性证据，才能说“正式 finalize 管线已完成”：

- handoff/status 投影已授权且当前 workspace-config 绑定的 finalize 事务成功；
- `finalize_report.json` 存在且结构有效；
- reader-clean 通过；
- `delivery_promotion == promoted`；
- 当前 `render_transaction_id` 存在；
- finalize quality gate 通过；
- handoff/status 投影已授权且当前 workspace 绑定、记录了 reason 的
  finalize-complete 事务成功；
- status 投影报告当前 run 的 `terminal_state`；
- `package_ready=true`；
- `delivered` / `terminal_state` 被按原值准确报告。

`package_ready=true` 只是交付资格，不是交付发生证据；只有 status 投影报告
当前 run `delivered=true` 才能支撑交付声明。

如果 WorkBuddy 或 generic helper 在正式 finalize 生命周期之外手写 Markdown /
DOCX，只能标为 `draft/manual/unverified`。不得移动、改名或描述为正式 BriefLoop
delivery，不得声称内部 ID / reader residue 已清理、模板已渲染，或后续口头回复
已经修复。如果 reader artifact 仍含 `CL-*`、`SRC-*`、`Claim Ledger`、本地路径
或其他禁止 residue，报告具体 residue，停止交付声明，并跟随确定性 repair /
finalize 路径；不得手改 frozen artifact 或绕过 reader gates。

## Work

对请求分类，确认或创建工作区路径，运行确定性 BriefLoop 命令，只为 handoff
指派的草稿工作调用角色子代理，在 stage 或工件动作前重读当前 handoff，在
gate、status、角色代理可用性或用户意图不明确时停下。细节使用 reference
文件，不要在这个入口文件里扩张权威。

## Handoff

把 `output/intermediate/agent_handoff.md` 和
`output/intermediate/agent_handoff.json` 当作这个工作区的执行契约。在每个
stage 或角色工件动作之前、以及每个确定性 CLI 事务之后，先重读相应的
handoff 步骤再继续。

## Required References

行动之前读相应的 reference：

- `references/quickstart.md`
- `references/workspace-workflow.md`
- `references/artifact-boundary.md`
- `references/status-and-gates.md`
- `references/repair-protocol.md`
- `references/workbuddy-safety.md`
- `references/workbuddy-delegation.md`

## 硬边界

- 不得直接编辑 `workflow_state.json`、`artifact_registry.json`、
  `runtime_manifest.json`、`event_log.jsonl`、gate 报告、release 报告或
  冻结工件。
- 不要用 WorkBuddy 的对话文字代替 BriefLoop 事务。
- 除非用户明确要求且当前 gate 允许，不要执行交付。
- 不要批准 release、人工审批账本或 memory 条目。
- 不要把可追溯性说成语义证明（semantic proof）或输出质量提升。
- 只有当前 handoff step 中 host-visible 的精确调用和返回，才能说
  `briefloop-analyst` 或 `briefloop-auditor` role 已返回。Analyst stage 完成
  必须读取当前确定性 stage/transaction truth；audit/gate 成功必须读取当前
  确定性 verdict/status。匹配工件、stale event、manual file 或旧事务单独都
  不能证明 role 执行、stage 完成或 audit 成功。
- 除非 `& $BriefLoop status --workspace "<workspace>" --json` 报告当前 run 的
  `delivered=true`，否则不要
  说"已交付"。`package_ready=true` 与 `terminal_state=draft_created` 都不是
  已交付。
- 不要打包或分享整个工作区。存在时使用 BriefLoop 生成的 delivery 或 audit
  bundle；绝不包含 `.env`。如需支持排查，只分享经人工确认的非敏感摘录，
  来自 `& $BriefLoop status --json` 或 doctor 输出。
- 工作区路径、生效二进制、gate 状态或交付意图不明确时，停下来询问。
