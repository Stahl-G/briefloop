# 工件边界

BriefLoop 把草稿/内容工作和确定性控制记录分开。

## WorkBuddy 可以协助

- 理解 `user.md`、`config.yaml`、`sources.yaml`；
- 在用户提供时添加公开安全的本地信源；
- 准备 handoff 点名的角色专属草稿/内容工件；
- 解释 status、gate 发现、repair route 输出和 Quality Panel；
- 在用户要求时运行 BriefLoop CLI 命令。

## WorkBuddy 不得直接编辑

- `output/intermediate/workflow_state.json`
- `output/intermediate/artifact_registry.json`
- `output/intermediate/runtime_manifest.json`
- `output/intermediate/event_log.jsonl`
- gate 报告（gate reports）
- release 就绪报告（release reports）
- 人工审批账本
- 冻结的 Claim Ledger 修订
- 交付归档或 bundle 清单——不得为了"看起来有效"而改

改动一律走对应的 owning 命令或事务。如果某个控制文件看起来不对，使用已
绑定的 PowerShell 可执行文件并报告失败结果：

```powershell
& $BriefLoop state check --workspace "<workspace>"
```

## 证据边界

信源和引用提供可追溯性，不自动证明 claim 被支持。不要把 BriefLoop 描述成
真值证明系统、幻觉消除器、输出质量提升器或交付批准引擎——它不是语义证明
（semantic proof），也不是 gate、release 批准或交付批准。
