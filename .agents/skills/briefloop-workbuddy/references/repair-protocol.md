# Repair 协议

Repair 是围绕 owner-stage 工件编辑的确定性事务。WorkBuddy 不得靠编辑
控制文件来"发明"一次 repair。

## 查看当前 gate 指引

```powershell
& $BriefLoop gates show --workspace "<workspace>" --json
```

按输出的 `required_commands` 执行。如果命令以 `briefloop` 或
`multi-agent-brief` 开头，只允许把这个首 token 替换为已经绑定的绝对
`$BriefLoop`；subcommand、option 和参数值必须原样保留。禁止
`Invoke-Expression`、`cmd /c`、Bash/Git-Bash fallback、PATH 重新解析、删改
参数或执行未知首命令。无法安全重绑定时停止并原样展示命令，不要猜测。如果
没有路由，如实报告该结果，不要启动一个没有归属的 repair。

## 启动当前 gate 的 repair

使用 `gates show` 给出的加 scope 的 repair start 命令。当前 gate 的
repair start 必须带 `--gate-stage` 和 `--gate-artifact`；不要对当前 gate
阻塞使用未加 scope 的 repair start（do not use unscoped repair start for
current-gate blockers）。

## 启动非 gate 的 repair

对来自 audit_report、finalize_report、artifact_registry 或
transaction_integrity 的非 gate owner-stage 修复路由，先查看：

```powershell
& $BriefLoop repair route --workspace "<workspace>" --json
```

用 `--finding-id <finding_id>` 或 `--route-index <route_index>` 启动选中的
非 gate 路由。不要使用裸的
`repair start --workspace <workspace>`。

事务开始后，只编辑 active repair 记录允许的工件，且只做 BriefLoop 显示的
repair owner/stage 的工作。

## 完成 repair

```powershell
& $BriefLoop repair complete --workspace "<workspace>" --reason "<reason>"
```

然后按 BriefLoop 报告的下游 status/gate 路径重跑。

## 污染恢复（supersede）

如果冻结的 owner-stage 工件在没有 active repair 的情况下被改动、run 已经
contaminated，不要清除 contamination，也不要编辑
`artifact_registry.json`。当操作者/人类决定接受当前字节作为新的
owner-stage 修订时，记录这笔恢复事务：

```powershell
& $BriefLoop repair supersede-stage --workspace "<workspace>" --stage "<owner_stage>" --artifact "<artifact_path>" --reason "<reason>" --json
```

这会记录旧的注册哈希、当前字节哈希与原因（old registered hash, current
bytes hash, and reason），保留原始 contamination 事件（original
contamination event），保持 `reference_eligible=false`，并要求下游 stage
重跑。supersede 之后，下游工件按记录的基线标记为过期：未重新生成的下游
工件在 `state check` 重算和拓扑满足中保持过期，字节真正重新生成之前下游
stage 无法完成。冻结事务拥有的工件（例如 `claim_ledger.json`）不能绕过其
冻结事务被 supersede。

当 Store-native status 投影（`& $BriefLoop status --workspace "<workspace>"
--json`）或 handoff 报告恢复待决（例如 `awaiting_recovery` /
`request_recovery_decision`）时，先读取
其 contamination / owner-revision 绑定，再由操作者决定受控 repair、
supersede 或新 run；不要从 `run_integrity` 猜恢复通道，也不要手改控制文件。
下一步动作只由 `briefloop runtime next --workspace <workspace>` 给出；
legacy completion projection / `workbuddy diagnose` 面已退役。

## 边界

- `repair route` 是只读的。
- `repair start` 创建 `workflow_state.active_repair`。
- `repair supersede-stage` 记录一次 contaminated owner-stage 修订；它不会
  让 run 变回 clean 或恢复 reference-eligible（does not make the run
  clean or reference-eligible）。
- 工作区级 `repair route` 用于非 gate 路由查看；裸的
  `repair start --workspace <workspace>` 不是合法的当前 gate 或非 gate
  repair 命令。
- `active_repair` 存在期间，stage 完成、finalize 完成、交付和 gate 报告
  写入必须 fail closed。
- 没有 active repair 时直接编辑冻结工件仍是 contamination。
- Repair 不会让 contaminated 的 run 变回 clean 或 reference-eligible。

## 硬停

出现以下情况时停下并请求人工审阅：

- 轨迹调控把可选决策收窄到 `request_human_review` 或 `block_run`；
- repair 需要在没有记录的 repair 或 supersede 事务的情况下直接改冻结
  工件；
- 路由要求的 stage 或工件是 WorkBuddy 无法安全执行的；
- 用户要求绕过 gate、交付检查或审批记录。
