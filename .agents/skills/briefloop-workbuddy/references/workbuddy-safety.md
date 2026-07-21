# WorkBuddy 安全边界

WorkBuddy 是 BriefLoop 的本地操作外壳，不是新的 BriefLoop 权威层。

## 要做

- 把缺少工作区路径的请求分类为"已有工作区"或"首次运行"，创建之前先确认
  文件夹路径；
- Windows 中用 PowerShell 一次绑定 `$BriefLoop` 绝对路径，并让 doctor、run、
  secrets import、status 和 runtime next 全部复用它；
- 信源发现之前询问用户是否开启在线搜索；
- 开启在线搜索时优先使用 Tavily，并在不显示密钥值的前提下验证
  `TAVILY_API_KEY`；
- 仅从用户确认且经 `Test-Path -LiteralPath $SecretSource -PathType Leaf` 验证的
  私有 `$SecretSource` 文件导入 Tavily secret；
- 用户拒绝在线搜索时，继续之前显式关闭 web 搜索；
- 完整工作流 handoff 使用 `--runtime codebuddy`；
- 在 CodeBuddy/WorkBuddy host 中按精确名称调用匹配的项目角色子代理；
- 用户同意后运行确定性 BriefLoop CLI 命令；
- 在关键命令、角色返回、repair、gate、finalize 尝试、quality 摘要和
  打包/导出请求之后打印机器事实 Run Card；
- 在每个 stage 或角色工件动作之前，重读相应的
  `agent_handoff.md` / `agent_handoff.json` 步骤；
- 每次启动、CLI、角色返回或中断后，重读 handoff、运行 status 投影与
  `runtime next`，并只跟随 handoff/status/runtime next 的当前动作；
- 角色委派的说法保持字面准确；
- 把 Quality Panel 解释为审计附件。

## 不要做

- 从仓库路径猜测工作区；
- 在 `TAVILY_API_KEY` 缺失时继续启用 Tavily 的在线信源发现；
- 在 Windows PowerShell 路径中混用 `bash`、`which`、`command -v`、`export`、
  `/c/Users/...`、`source .venv/bin/activate` 或 `bash scripts/setup.sh`；
- 临时修改 PATH 或把 API key 注入单条命令，而不是使用 workspace secrets import；
- 把环境变量本身当作 secrets import 的文件来源，或在私有 `$SecretSource`
  不存在时继续导入；
- 未经用户要求替代方案就让用户在所有搜索提供商之间做选择；
- 直接编辑控制文件或冻结工件；
- 在 WorkBuddy 没有真正委派时说专家子代理已运行；
- 对完整工作流静默切换到 `--runtime operator`（silently fall back）；
  切换 operator 运行时必须由用户明确决定，并重新生成 operator handoff；
- 在 codebuddy handoff 下由主会话代写角色专属工件；
- 建议修改角色子代理 frontmatter 的 tools 清单来绕过派发失败；
- 在 Run Card 里自行宣布 `run_integrity=contaminated`——完整性由 Python
  判定，只能引用 status 投影的输出；
- 没有当前 handoff step 中 host-visible 的精确调用和返回，却声称 Analyst /
  Auditor role 已返回；或没有当前确定性 transaction/verdict truth，却声称
  stage / audit 已通过；匹配工件、stale event、manual file 或旧事务都不充分；
- 把 status 投影的 `package_ready=true` 当成已经交付；它只表示当前 run 的
  reader package 可进入交付决策；
- 说 `delivered`、`delivery complete` 或 `交付完成`，除非
  `& $BriefLoop status --workspace "<workspace>" --json` 报告当前 run 的
  `delivered=true`；`package_ready=true` 和 `terminal_state=draft_created`
  都不是 delivered；
- 不要从 `run_integrity` 推断恢复阶段或下一步；恢复与下一步动作必须读取
  `& $BriefLoop runtime next --workspace "<workspace>"` 给出的当前动作与原因；
- 在恢复状态仍为 `awaiting_recovery`、`repair_in_progress`、
  `downstream_rerun_pending` 或 `invalid_recovery_state` 时交付、导出或分享；
- 对 `completed_non_reference` 的 run，不要再次运行 finalize；
  在 status 投影报告 `package_ready=true` 之前也不要交付。终态恢复仍永久不具备
  reference 资格；
- 从 raw workflow state、event log、Registry、时间戳、投影文件或文件存在性重构下一步、
  gate、finalize 或 delivery 真值；这些 raw controls 只能作为审计证据；
  legacy completion projection / `workbuddy diagnose` 面已退役，不要再调用它；
- 在叙述里降级 `doctor` 错误，或用 `request_human_review`、用户确认、另一
  shell/环境中先前的 standalone pass 把 error 改成 pass；必须修复并在同一
  `$BriefLoop` 执行上下文重新通过；
- 让 `briefloop-formatter` 运行 shell/CLI、转换 Markdown-to-DOCX、写 reader
  delivery artifacts，或声称 reader-clean、gate/finalize/delivery 成功；
- 把正式 finalize 生命周期之外的手写 Markdown/DOCX 改名或描述成正式交付；
  它只能标为 `draft/manual/unverified`，reader residue 必须报告并进入确定性
  repair/finalize；
- 打包或分享整个工作区；附件里绝不包含 `.env`、token 或私有规划文件；
- 批准交付、release、gate 或 memory 条目；
- 声称语义证明（semantic proof）、自动真值检查、幻觉消除或输出质量提升；
- 在示例中暴露私有本地路径、私有规划文件、token 或公司敏感材料。

## 拿不准时

如果没有给出工作区路径，先把请求分类为已有工作区或首次运行。解释
BriefLoop 工作区就是这份报告项目的本地文件夹。只在创建新工作区时建议一个
安全的本地文件夹，然后在创建之前请求明确确认。不要靠手写 BriefLoop 控制
记录来填补缺口。

如果 WorkBuddy 会话在用中文，可按需要用中文解释生成的 handoff，但要严格
按 handoff 执行。逐字保留命令名、工件名与 handoff 义务。不要因为翻译而
跳过步骤、隐藏阻塞，或声称子代理已运行。

如果用户要求执行交付：只有当 status 投影报告
`package_ready=true` 时才使用 BriefLoop 生成的 delivery 或 audit
bundle。执行后只有 status 投影报告当前 run `delivered=true` 可以报告已交付；
`terminal_state=draft_created` 只能报告草稿已创建。如果 status 投影没有报告
`package_ready=true`，
仅当 `output/intermediate/audited_brief.md` 存在时说
"只有草稿"；否则说目前既没有草稿也没有交付。任何打包候选里出现 `.env`，
停止，并在分享任何东西之前建议轮换密钥。
