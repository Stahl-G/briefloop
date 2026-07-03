# BriefLoop v0.11 产品黄金路径

这是一份给普通 BriefLoop 用户看的最短产品路径。它不是实验 harness，不是
benchmark protocol，也不是 reference run 展示。它只回答一个实际问题：怎样从
零开始创建、运行、检查并交付一份可追溯的业务简报，同时不绕过控制脊柱。

当你要做下面三类 v0.11 产品基线工作区时，走这条路径：

| 产品入口 | 内部 ReportPack | 适合什么 |
|---|---|---|
| `industry-weekly` | `market_weekly` | 行业、市场、政策、竞品等周期性周报 |
| `management-monthly` | `management_monthly` | 管理层月度复盘和经营简报 |
| `document-review` | `evidence_extract` | 有明确范围的本地文档证据审阅 |

`solar-periodic` 仍是实验性 Product OS 扩展。它可以用于 dogfood，但不是稳定
v0.11 产品基线的一部分。

## 边界

BriefLoop 帮你生成带有可追溯主张、来源纪律、质量门禁、事件记录和人工交付边界
的业务简报。它不证明语义真实性，不消除幻觉，不授权公开发布，不自动发布报告，
也不替代人工审核。

产品层只能包装控制脊柱，不能绕过控制脊柱。Claim Ledger、artifact registry、
quality gates、event log、archive、source appendix、support records、human
delivery approval 和 frozen artifact integrity 都必须保留。

## 1. 创建工作区

按工作类型选择产品入口。

```bash
briefloop new industry-weekly ./weekly-brief \
  --company "ExampleCo" \
  --industry "industrial equipment" \
  --audience "management team" \
  --title "ExampleCo Industry Weekly" \
  --language en-US

briefloop new management-monthly ./monthly-review \
  --company "ExampleCo" \
  --audience "executive team" \
  --title "ExampleCo Management Monthly" \
  --language en-US

briefloop new document-review ./document-review \
  --company "ExampleCo" \
  --audience "review team" \
  --title "ExampleCo Document Review" \
  --language en-US
```

生成的 workspace 是 local-first。它会写入 `report_spec.yaml`、`config.yaml`、
`sources.yaml`、`user.md`、`input/` 和 `.gitignore`。它不会运行 stage，不会
隐藏抓取来源，也不会交付文件。

## 2. 放入来源材料

`industry-weekly` 和 `management-monthly` 第一次建议只放几份整理好的本地文本：

```bash
cp ./sources/*.md ./weekly-brief/input/sources/
```

`document-review` 要显式登记来源和审阅范围：

```bash
briefloop extract \
  --workspace ./document-review \
  --sources "./docs/*.md" \
  --scope "contracts, permits, production capacity, dates, named obligations"
```

二进制 / PDF 文件不会因为选择了产品入口就自动变成可用证据。如果某个二进制来源
只是 registered-only，先通过受支持的输入路径把它转换或抽取成可读文本，再让
runtime 使用其中内容作为 evidence。

## 3. 启动 runtime handoff

创建或刷新 runtime handoff：

```bash
briefloop run --workspace ./weekly-brief
```

Claude Code 里的 writer 命令是：

```text
/briefloop run ./weekly-brief
```

然后按照生成的 handoff 执行。Claude writer 路径里，完整 delegated workflow
通常用：

```text
/generate-brief ./weekly-brief
```

`run` 是 handoff launcher。它本身不完成 stage，也不会绕过确定性 transaction。

## 4. 先看状态，再行动

不确定下一步时先看 status：

```bash
briefloop status --workspace ./weekly-brief
briefloop status --workspace ./weekly-brief --json
```

`status` 是只读的。它显示当前 stage、缺失 artifact、blocker、gate 状态、产品
projection 和下一步安全动作。如果控制 artifact 缺失或过期，按它提示的确定性命令
处理，不要手工编辑 artifact。

## 5. 把反馈当反馈处理

草稿需要修改时，记录 feedback，而不是直接改 frozen artifact：

```bash
printf '%s\n' "先讲业务影响，再列新闻。" > ./weekly-brief/input/feedback/human-feedback.md
briefloop feedback ingest \
  --workspace ./weekly-brief \
  --source human \
  --feedback ./weekly-brief/input/feedback/human-feedback.md
```

Feedback 不是 source evidence，也不会自动进入 Improvement Memory。事实或来源问题
走 repair、audit 或 gates；稳定的读者偏好必须由人批准后，才会在后续 run 中复用。

## 6. 门禁通过后再交付

run 通过必要门禁并完成 finalize 状态后，再交付：

```bash
briefloop deliver --workspace ./weekly-brief
```

读者可见文件在：

```text
output/delivery/brief.md
output/delivery/<named-brief>.docx
```

审计和控制 artifact 继续保留在 workspace 中，用于追溯和复盘。它们不是第二份读者
交付文件：

```text
output/intermediate/claim_ledger.json
output/intermediate/audit_report.json
output/source_appendix.md
event_log.jsonl
```

如果 reader-final gate 失败，不要手工搬走或发布文件。打开对应 gate 或 finalize
report，按 workflow 修复，然后重新走确定性交付路径。

## 7. 第一次产品运行 checklist

第一次产品运行建议收窄范围：

- 只选一个产品入口：`industry-weekly`、`management-monthly` 或
  `document-review`；
- 放三到五份本地文本来源；
- 不做隐藏 web crawling；
- 不手工编辑 frozen control files；
- 不存在 force-deliver 路径或 delivery override flag；
- 读者文件分享前必须人工 review。

如果这条路径仍然让人困惑，把困惑当作文档缺陷记录下来。不要为了补救文档缺陷而
绕过 ledger、gate、event、archive 或 human delivery。
