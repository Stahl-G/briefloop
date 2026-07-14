# 快速上手

本快速上手面向在本地操作 BriefLoop 的 WorkBuddy 用户。本版本的 Skill 包为
source-clone-only；Python wheel/sdist 包安装不包含这些 WorkBuddy 文件。

## 1. 确认生效的 CLI

Windows WorkBuddy 只使用 PowerShell，并在整次运行中复用同一个绝对路径：

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

改动任何东西之前，先报告解析出的命令路径和版本。`py -3 --version` 只是诊断，
不证明 `$BriefLoop` 的 Python 身份。不要自动混用或回退到 `bash`、`which`、
`command -v`、`export`、`/c/Users/...`、`source .venv/bin/activate` 或
`bash scripts/setup.sh`。如果宿主实际是 Git Bash，报告真实 shell，停止这个
PowerShell 路径；不要猜测路径翻译或混用两种 shell。本说明不支持 Git Bash。

## 2. 询问在线搜索

首次信源发现之前询问用户：

```text
是否要打开在线搜索？如果要打开搜索，强烈建议添加 Tavily API。
```

BriefLoop 推荐用 Tavily 做在线搜索，但生成的工作区在用户明确开启之前保持
`configure_later`。如果用户开启在线搜索，用 Tavily 作为推荐提供商：

```text
TAVILY_API_KEY=<user-provided-key>
```

先记录用户的搜索选择。新工作区必须在下一节创建成功后才能导入 workspace
secret；已有工作区也要先验证它存在。不要在 `& $BriefLoop new` 之前运行
`secrets import`。

如果用户拒绝在线搜索，创建工作区时显式关闭 web 搜索。

## 3. 创建工作区

如果用户说"跑周报"而且还没有工作区：

1. 用一句话解释：BriefLoop 工作区就是这份报告项目的本地文件夹。
2. 建议一个位于 BriefLoop 源码检出之外的安全本地文件夹，例如 macOS/Linux 的
   `~/Documents/BriefLoop/workspaces/<topic-slug>` 或 Windows 的
   `C:\Users\<User>\Documents\BriefLoop\workspaces\<topic-slug>`。
3. 创建之前请用户对目标路径做出明确确认。只建议；不要静默创建文件夹或
   工作区。
4. 根据用户的自然语言请求选择产品入口：
   - weekly、industry、market、competitor、周报、行业、竞品 ->
     `industry-weekly`
   - management monthly、管理月报、月报 -> `management-monthly`
   - file review、PDF review、document review、文件、PDF、审阅 ->
     `document-review`
5. 用户确认目标路径之后才运行 `& $BriefLoop new ...`。

一次只用一个产品入口，并把用户的搜索选择写明。

如果用户开启在线搜索，强烈推荐 Tavily：

```powershell
& $BriefLoop new industry-weekly "<workspace>" --search-backend tavily
& $BriefLoop new management-monthly "<workspace>" --search-backend tavily
& $BriefLoop new document-review "<workspace>" --search-backend tavily
& $BriefLoop new solar-periodic "<workspace>" --search-backend tavily
```

如果用户拒绝在线搜索，显式关闭：

```powershell
& $BriefLoop new industry-weekly "<workspace>" --web-search-mode disabled
& $BriefLoop new management-monthly "<workspace>" --web-search-mode disabled
& $BriefLoop new document-review "<workspace>" --web-search-mode disabled
& $BriefLoop new solar-periodic "<workspace>" --web-search-mode disabled
```

`industry-weekly`、`management-monthly`、`document-review` 是基线支持的
产品入口。`solar-periodic` 是实验性入口。

如果用户启用 Tavily，工作区创建成功后再持久导入密钥；不要临时 export、
修改 PATH 或把 key 注入单条命令：

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
指导用户先创建私有文件；不要打印、自动复制或在命令行展开密钥。只检查
`TAVILY_API_KEY` 是否存在，不要打印或提交密钥内容。除非用户要求非
Tavily 提供商，不要让用户在 Exa、Brave、Firecrawl、Serper 之间做选择。

## 4. 运行 CodeBuddy Handoff

运行：

```powershell
& $BriefLoop run `
  --workspace "<workspace>" `
  --runtime codebuddy `
  --repo-workdir "<canonical BriefLoop source checkout>"
```

然后立即运行 diagnose 并读取两个 handoff 文件：

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
```

开始角色工作前，核对 handoff/runtime capability 都是 `codebuddy`，
`delegation_supported=true`，`subagent_names` 精确等于七个 `briefloop-*` 角色，
并确认 canonical source checkout 含精确 `.codebuddy/agents/briefloop-*.md` 资产。
`delegation_supported=true` 只是声明能力，不证明实际委派。只有宿主可见的精确
角色调用与返回才是角色运行证据；generic Team、Expert、helper、Send Message
或叙述标签都不是 BriefLoop 角色证据。

handoff 之后，只报告 handoff/diagnose 给出的确定性进度，例如：

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
```

只有当前 handoff step 中 host-visible 的精确调用和返回，才能说
`briefloop-analyst` 或 `briefloop-auditor` role 已返回。Analyst stage 完成
必须读取当前确定性 stage/transaction truth；audit/gate 成功必须读取当前
确定性 verdict/status。匹配工件、stale event、manual file 或旧事务单独都
不能证明这些事实。

每个关键命令或角色返回之后，用机器事实打印这张 Run Card：

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

如果 `doctor` 报告任何错误，停下并展示完整 doctor 输出。环境或配置修复且
同一 `$BriefLoop` 的 doctor 重新通过之前不得继续；用户确认不能把 error 改成
pass，另一 shell/环境中先前的 standalone pass 也不能替代本次执行。随后
diagnose 的 `doctor.status=not_run_read_only` 不能清除、替代或绕过该失败，
其 completion action 也不得执行；中断后或会话连续性不确定时，用同一
`$BriefLoop`、workspace 和 config 重跑 doctor。不要从
`run_integrity` 推断恢复进度；读取 diagnose 的 `recovery_state.status` 和
`recommended_recovery_action`。恢复非终态或无效时只执行机器给出的恢复动作，
不要交付、导出或分享。`recovery_status=finalize_render_required` 和
`finalize_completion_pending` 分别只允许投影指定的 finalize / gate 完成事务。
`recovery_status=completed_non_reference` 时不要再次运行 finalize；仅当
`delivery_truth.valid=true` 时才可本地交付，并且永久不具备 reference 资格，
否则停止交付。对更早的角色工作阶段，报告 Run Card，并只继续 handoff
允许的非交付工作流步骤。
`delivery_truth.valid=true` 只表示当前 reader bundle 可进入交付动作。只有
`delivery_event=delivery_succeeded` 才允许声称已交付；
`delivery_bundle_prepared` 只能报告本地包已准备，`delivery_draft_created` 只能
报告草稿已创建。如果 WorkBuddy 诊断没有报告 valid bundle，不要执行交付。
仅当 `output/intermediate/audited_brief.md` 存在时才说
run 里有草稿；否则说目前既没有草稿也没有交付。只有 handoff 允许时才继续
更早的阶段。

WorkBuddy 主会话必须为 handoff 指派的草稿工作调用匹配的角色子代理：

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

如果这些角色子代理不可用，在完整工作流执行之前停下。不要退回手写
BriefLoop JSON 工件，也不要静默切换到 `--runtime operator`。

如果已有 operator handoff 而用户要求 subagents，停止使用它，用上面的
`--runtime codebuddy --repo-workdir` 命令重新生成 handoff，再重读
`agent_handoff.md/json`。不要继续 operator 并承诺以后委派；operator handoff
不得声称任一 `briefloop-*` 角色已经运行。

每次启动、每条 CLI、每个角色返回和中断后：重读 handoff -> diagnose -> 跟随
当前动作 -> 仅当当前动作明确指派 role-owned draft work 时调用该精确角色；如果
当前动作是 deterministic-only，不调用任何角色，由主会话直接运行获授权事务 ->
再 diagnose。不要从 raw
workflow state、event log、Registry、时间戳或文件存在性重构下一步或 gate /
finalize / delivery 真值；raw controls 仅供审计。

Formatter 只报告 readiness。手写 Markdown/DOCX 一律标为
`draft/manual/unverified`，不能冒充正式 finalize 或 delivery。只有实际 finalize、
有效 Finalize Report、reader-clean/promoted/current-render、finalize gate、成功
finalize-complete、当前 finalize event、valid delivery truth 和准确 delivery
outcome 全部成立，才可声称正式 finalize 完成。发现 `CL-*`、`SRC-*`、
`Claim Ledger`、本地路径等 residue 时停止交付声明并走确定性 repair/finalize。

## 5. 生成质量摘要

当工作区已有足够工件可以总结时：

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
```

打开 `output/intermediate/quality_panel.html` 查看静态审计视图。
Quality Panel 是可追溯性和过程问责，不是语义证明、交付批准或 release
授权。

## 6. 安全分享输出

不要打包或分享整个工作区。完整工作区可能包含 `.env`、token、私有规划
笔记、控制文件和未完成工件。存在时使用 BriefLoop 生成的 delivery 或
audit bundle。如果某个打包或附件候选包含 `.env`，停止，移除该包，并建议
轮换任何暴露的密钥。
