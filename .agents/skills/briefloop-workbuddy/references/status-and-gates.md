# Status 与 Gates

把 status 和 gate 输出当作控制面诊断使用，不要把它们变成交付或 release
权威。

## 查看 Status

```bash
multi-agent-brief status --workspace <workspace>
multi-agent-brief status --workspace <workspace> --json
multi-agent-brief state check --workspace <workspace>
```

如果 `status` 报告阻塞、contamination、active repair、过期工件或无效
工件，停下并按指示的事务路径处理。

## Quality Panel

用这条命令生成静态 Quality Panel：

```bash
multi-agent-brief quality summarize --workspace <workspace>
```

输出文件是：

- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`

这些是审计投影，不是 gate、release 批准或交付批准。

## 交付

只有当用户明确要求、且当前 gate/status 路径允许时才执行交付。交付真值来自
`finalize_report.json` 和 completion projection（经
`briefloop workbuddy diagnose --workspace <workspace> --json` 读取），
不是文件存在性。如果存在 reader-clean 或 gate 阻塞，不要绕开它打包或
交付；reader-clean 失败不会晋升交付，也不会改动之前的交付包。
