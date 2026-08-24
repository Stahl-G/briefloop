# BriefLoop v0.15.3 SQLite 产品黄金路径

这是一份给普通 BriefLoop 用户看的最短产品路径。它不是实验 harness，不是
benchmark protocol，也不是 reference run 展示。它只回答一个实际问题：怎样从
零开始创建、运行、检查并交付一份可追溯的业务简报，同时不绕过控制脊柱。

当你要做下面三类 v0.11 产品基线工作区时，走这条路径：

| 产品入口 | 内部 ReportPack | 适合什么 |
|---|---|---|
| `industry-weekly` | `market_weekly` | 行业、市场、政策、竞品等周期性周报 |
| `management-monthly` | `management_monthly` | 管理层月度复盘和经营简报 |
| `document-review` | `evidence_extract` | 有明确范围的本地文档证据审阅 |

`solar-stock-periodic` 是 v0.15.3 发布的 fresh schema-19 实验性资本市场
周报入口；更广的旧 `solar-periodic` Product OS 扩展也仍是实验性。
两者都不是稳定 v0.11 产品基线的一部分。

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

需要人工检查上传与来源清单时，使用一次性本地初始化页面并在提交前确认 canonical
source manifest：

```bash
briefloop init ./document-review --web
```

退役的 `briefloop extract` 和 `briefloop sources ...` 在 SQLite workspace 上不可用。
如果 `runtime continue` 要求 Human source pack，只执行它返回的精确 Store-bound 请求。

二进制 / PDF 文件不会因为选择了产品入口就自动变成可用证据。如果某个二进制来源
只是 registered-only，先通过受支持的输入路径把它转换或抽取成可读文本，再让
runtime 使用其中内容作为 evidence。

## 3. 启动 runtime handoff

创建或刷新 runtime handoff：

```bash
briefloop run --workspace ./weekly-brief --runtime codex
```

`run` 是 handoff launcher。它本身不完成 stage，也不会绕过确定性 transaction。

随后只沿 Store 给出的动作继续：

```bash
briefloop runtime continue --workspace ./weekly-brief
```

只完成返回的精确 role proposal 或 deterministic action，然后再 continue。遇到
`needs_human`、`needs_attention`、`finalized_local` 或 `terminated` 就停止。

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

finalized brief 需要读者反馈时，打开受保护的本地 Review Session，不要直接修改
frozen artifact：

```bash
briefloop quality laj review-open --workspace ./weekly-brief
```

Human observation 不是 source evidence，也不是 model finding。guidance 必须单独批准，
并在 successor 中显式 opt-in 才能复用。退役的 `briefloop feedback` 命令在 SQLite
workspace 上不可用。

## 6. 门禁通过后再交付

finalize 和之后的 package/delivery 都是 typed Store action。继续使用有界 continuation：

```bash
briefloop runtime continue --workspace ./weekly-brief
```

达到 `finalized_local` 时，本地读者文件通常包括：

```text
output/brief.md
output/brief_pages.html
```

只有显式授权的 package/delivery 才可能额外生成 `output/delivery/brief.md` 与命名
DOCX。SQLite workspace 不提供独立的 `briefloop deliver` 或 force-deliver 路径。

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
