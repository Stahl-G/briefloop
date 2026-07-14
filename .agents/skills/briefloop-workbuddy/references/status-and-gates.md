# Status 与 Gates

把 status 和 gate 输出当作控制面诊断使用，不要把它们变成交付或 release
权威。

## 查看 Status

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop state check --workspace "<workspace>"
```

下一步、gate、finalize 和 delivery 路由只跟随 handoff/diagnose。raw status、
workflow state、event log、Registry、时间戳和文件存在性只用于审计，不用于
重构当前动作。如果 diagnose 报告阻塞、contamination、active repair、过期
工件或无效工件，停下并按它指示的事务路径处理。

## Quality Panel

CLI `finalize-complete` 成功时，会先完成权威事务和不可变 run archive，随后
自动生成静态 Quality Panel 三件套并通过 Artifact Registry 绑定。

如果自动投影缺失、过期或无效，用下面的命令显式修复或重新投影：

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
```

输出文件是：

- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`

`quality summarize` 不是唯一的正常 writer。这些是审计投影，不是 gate、
release 批准或交付批准。

## 交付

只有当用户明确要求、且当前 gate/status 路径允许时才执行交付。交付真值来自
`finalize_report.json` 和 completion projection（经
`& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json` 读取），
不是文件存在性。如果存在 reader-clean 或 gate 阻塞，不要绕开它打包或
交付；reader-clean 失败不会晋升交付，也不会改动之前的交付包。

“正式 finalize 管线已完成”要求当前 run 的成功 finalize 命令、结构有效的
Finalize Report、reader-clean pass、promoted、当前 render transaction、finalize
gate pass、成功 finalize-complete、diagnose 当前 finalize event、valid delivery
truth 与准确 delivery outcome 全部存在。任何手写 Markdown/DOCX 只能标为
`draft/manual/unverified`。若含 `CL-*`、`SRC-*`、`Claim Ledger`、本地路径或其他
forbidden residue，停止交付声明，走正式 repair/finalize，不得手改 frozen artifact。
