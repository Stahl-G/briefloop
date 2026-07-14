# 工作区工作流

BriefLoop 工作区靠确定性 CLI 事务和角色专属草稿工件推进。WorkBuddy 可以
帮用户操作这个循环，但不得手改控制文件。

## 正常循环

1. 确认工作区路径。
2. 运行 CodeBuddy handoff：

   ```powershell
   & $BriefLoop run `
     --workspace "<workspace>" `
     --runtime codebuddy `
     --repo-workdir "<canonical BriefLoop source checkout>"
   ```

3. 阅读 `output/intermediate/agent_handoff.md` 和
   `output/intermediate/agent_handoff.json`。
4. 在每个 stage 或角色工件动作之前，重读相应的
   `agent_handoff.md` / `agent_handoff.json` 步骤。
5. 为 handoff 指派的角色工件工作调用匹配的角色子代理。
6. 工件就绪时使用对应的 owning CLI 事务。
7. 每条 CLI 命令之后，先重读相应的 handoff 步骤再继续。
8. 每次启动、CLI、角色返回或中断后运行 diagnose；在 repair、finalize、
   quality 摘要或交付之前只跟随 handoff/diagnose 的当前动作。

## 常用查看命令

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop state check --workspace "<workspace>"
& $BriefLoop quality summarize --workspace "<workspace>"
```

## 进度更新

每个确定性 CLI 事务之后，向用户总结 handoff/diagnose 中可见的进度。raw
workflow state、event log、Registry、时间戳和文件存在性只能作为审计证据，
不能替代 action router 或用于重构 gate、finalize、delivery 与 next action。

在每个关键 CLI 命令、角色返回、repair 动作、gate 检查、finalize 尝试、
quality 摘要或打包/导出请求之后使用 Run Card：

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

这些字段从 `& $BriefLoop workbuddy diagnose --workspace "<workspace>"
--json` 读取；该命令格式化的是规范 completion projection，只对
`next_allowed_action` 叠加 WorkBuddy 的 doctor/密钥安全覆盖。不要从文件
存在性检查或叙述文字重构交付、gate、finalize 或下一步动作的真值。
`recovery_status` 和 `recovery_action` 分别读取
`recovery_state.status` 与 `recovery_state.recommended_recovery_action`；不要
从 `run_integrity` 重构恢复进度。

允许的示例：

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
Quality Panel 已生成。
```

只有当前 handoff step 中 host-visible 的精确调用和返回，才能说 Analyst 或
Auditor role 已返回。Stage 完成和 audit/gate 成功必须读取当前确定性
transaction/verdict truth。匹配工件、stale event、manual file 或旧事务单独
都不能证明这些事实。

`delivery_truth.valid=true` 只表示当前 reader bundle 可进入交付动作。不要说
`交付完成`、`delivered` 或 `delivery complete`，除非 WorkBuddy 诊断报告
`delivery_event=delivery_succeeded`。`delivery_bundle_prepared` 表示本地包已
准备，`delivery_draft_created` 表示草稿已创建；两者都不是 delivered。

## 硬停

- 如果 `doctor` 报告任何错误，停止。展示完整 doctor 输出、工作区路径、
  当前用户、输出路径存在性/可写性结果、以及权限或 ACL 输出。不要自行降级
  该错误。用户确认、`request_human_review` 或另一环境中的 standalone pass
  不能将它改成 pass；必须修复并用同一 `$BriefLoop` 重新通过。随后 diagnose
  的 `doctor.status=not_run_read_only` 不能清除或绕过该失败，也不得跟随其
  completion action；中断后或会话连续性不确定时重跑 doctor。
- 恢复非终态或无效时，只执行 `recovery_action` / `next_allowed_action` 指定的
  事务；不要从 `run_integrity` 推断恢复通道，也不要交付、导出或分享。
- 如果 `recovery_status=completed_non_reference`，不要再次运行 finalize；仅当
  `delivery_truth.valid=true` 时才可本地交付，并且永久不具备 reference
  资格，否则停止交付。
- 对于早期阶段的草稿工作，报告 Run Card，并只继续 handoff 允许的非交付
  工作流步骤。
- 如果 WorkBuddy 诊断没有报告 `delivery_truth.valid=true`，不要执行交付；
  如果 `delivery_event` 不是 `delivery_succeeded`，不要声称已交付。仅当
  `output/intermediate/audited_brief.md` 存在时才
  报告"仅有草稿"；否则说目前既没有草稿也没有交付。这在 finalize 之前是
  正常状态，本身不阻塞更早的 handoff 指派阶段。
- 如果 zip、导出或附件候选包含 `.env` 或密钥，停止。不要分享；建议轮换
  任何暴露的密钥。

## 角色委派

先读 `workbuddy-delegation.md`。CodeBuddy/WorkBuddy 主会话必须显式委派
角色专属草稿工作，并严格使用签入的项目角色名：

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

角色子代理只起草 handoff 指派的工件。它们不运行 BriefLoop CLI 命令、
不编辑控制文件、不执行 gate、不完成 stage、不冻结 Claim Ledger、不 finalize、
不批准或汇报交付、不授权 release。角色返回不等于 stage 通过；返回之后由
WorkBuddy 主会话运行确定性 CLI 事务并再次 diagnose。

Formatter 只是只读 finalize-readiness reporter：不得运行 shell/CLI、执行
Markdown-to-DOCX、写 reader delivery artifacts，或声称 reader-clean、gate、
finalize、delivery 成功。

手写 Markdown/DOCX 只能标为 `draft/manual/unverified`。正式 finalize 完成声明
必须绑定 actual finalize、有效 Finalize Report、reader-clean/promoted/current
render、gate、finalize-complete、当前 finalize event、valid delivery truth 与
准确 outcome；任何 residue 都必须走确定性 repair/finalize。

除非 WorkBuddy 确实委派了这些角色，否则不要声称 Scout、Screener、
Claim Ledger、Analyst、Editor、Auditor 或 Formatter 子代理已运行。

如果 host 没有实际派发精确的 `briefloop-*` 项目角色，在 codebuddy 完整工作流
执行之前停下。你仍可以运行确定性 setup、`status`、`state check`、
`quality summarize`、`doctor` 或 demo 命令，但不得把 generic helper 的工作
描述成 BriefLoop 项目角色执行，也不得由主会话代写 codebuddy handoff 指派的
角色工件。继续通道必须由用户明确选择：换到能派发项目角色的
CodeBuddy/WorkBuddy 会话，或用 `--runtime operator` 重新生成 handoff。

如果用户在用中文交流，可在需要时用中文解释下一步动作，但要严格按生成的
handoff 执行。逐字保留命令名、工件名与 handoff 义务。翻译不得漏掉步骤、
弱化 gate/阻塞语言，或把主会话的工作说成子代理已运行。
