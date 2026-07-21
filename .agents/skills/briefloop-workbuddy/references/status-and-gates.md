# Status 与 Gates

把 status 和 gate 输出当作控制面诊断使用，不要把它们变成交付或 release
权威。

## 查看 Status

```powershell
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop runtime next --workspace "<workspace>"
& $BriefLoop state check --workspace "<workspace>"
```

下一步、gate、finalize 和 delivery 路由只跟随 handoff/status 投影与
`runtime next`。raw
workflow state、event log、Registry、时间戳和文件存在性只用于审计，不用于
重构当前动作。如果 status 投影或 handoff 报告阻塞、contamination、active
repair、过期
工件或无效工件，停下并按它指示的事务路径处理。legacy completion projection /
`workbuddy diagnose` 面已退役，不要再调用它。

## Quality Panel

用这条命令生成静态 Quality Panel：

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
```

输出文件是：

- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`

这些是审计投影，不是 gate、release 批准或交付批准。

## 交付

只有当用户明确要求、且当前 gate/status 路径允许时才执行交付。交付真值的唯一
正典读取方是 Store-native status 投影（经
`& $BriefLoop status --workspace "<workspace>" --json` 读取，字段带 receipt
绑定），流程推进真值来自
`& $BriefLoop runtime next --workspace "<workspace>"`，不是文件存在性，
也不得从投影文件重构。如果存在 reader-clean 或 gate 阻塞，不要绕开它打包或
交付；reader-clean 失败不会晋升交付，也不会改动之前的交付包。

“正式 finalize 管线已完成”要求当前 run 的成功 finalize 命令、结构有效的
Finalize Report、reader-clean pass、promoted、当前 render transaction、finalize
gate pass、成功 finalize-complete、status 投影报告 `package_ready=true`
与准确 `delivered`/`terminal_state` 全部存在。任何手写 Markdown/DOCX 只能标为
`draft/manual/unverified`。若含 `CL-*`、`SRC-*`、`Claim Ledger`、本地路径或其他
forbidden residue，停止交付声明，走正式 repair/finalize，不得手改 frozen artifact。
