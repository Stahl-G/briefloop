# 架构说明

这个项目是一个 subagent-first 简报工作流：Python CLI 负责工作区管理、来源治理、质量门禁和最终渲染；AI agent runtime 通过运行交接单（Runtime Handoff）协调角色子智能体执行简报生成。

## 核心流程

```mermaid
flowchart LR
  A["用户需求<br/>onboarding + init"] --> B["来源治理<br/>sources decide + doctor + inputs extract/classify"]
  B --> C["Scout<br/>信息侦察员"]
  C --> D["Screener<br/>筛选师"]
  D --> E["Claim Ledger<br/>事实账本"]
  E --> F["Analyst<br/>分析师"]
  F --> G["Editor<br/>编辑师"]
  G --> H["Auditor<br/>审计师"]
  H --> I["Formatter / finalize<br/>定稿器"]
  I --> J["输出<br/>brief.md, brief.docx,<br/>claim_ledger.json, audit_report.json"]
```

灰底步骤（来源治理、finalize）由 Python CLI 执行；白底步骤（Scout → Auditor）由 runtime 子智能体按运行交接单执行。

## 运行时

### Claude Code（一等写作者路径）

Claude Code 是 first-class writer / five-verb path。`/mabw` 暴露 `new`、`run`、`status`、`feedback`、`deliver` 五个写作者动词；`/generate-brief` 仍是委派子智能体工作流命令。

### Hermes（委派 / 定时运行时路径）

Hermes 使用 `delegate_task` 原生子代理管线：scout → screener → claim-ledger → analyst → editor → auditor。Python CLI 提供 init、doctor、输入抽取/分类、finalize 工具；cron 处理定时调度。

### OpenCode / Codex

通过 `multi-agent-brief run --workspace <path> --runtime opencode|codex` 生成 `agent_handoff.md`，由对应平台的斜杠命令和子智能体配置执行。

## 输入治理

`input/` 下有四个约定子目录：

| 目录 | 角色 | 是否进入事实账本 |
|---|---|---|
| `input/sources/` | 证据文件 | ✅ |
| `input/feedback/` | 编辑反馈 | ❌ |
| `input/instructions/` | 任务要求 | ❌ |
| `input/context/` | 背景参考 | ❌ |

`multi-agent-brief inputs extract --config <path>` 会用 MinerU 把受支持的 PDF/DOCX/PPTX/XLSX/图片输入转换为相邻的 `.mineru.md` 文件。随后 `multi-agent-brief inputs classify --config <path>` 自动分类原始文件和抽取文件，并产出 `input_classification.json`。Scout 被约束只从 `input/sources/` 和 `input/` 根目录（向后兼容）提取声明。`input/context/`、`input/instructions/`、`input/feedback/` 下抽取出的 Markdown 仍然是非证据材料。ManualProvider 代码层阻止非证据目录作为 source。

## 各角色职责

### 信息侦察员（Scout）

读取证据文件、来源包、搜索输出，抽取候选可报告事项，写入 `candidate_claims.json`。不负责分析写作。

### 筛选师（Screener）

按新颖度、来源层级、主题容量、历史重复筛选候选声明，写入 `screened_candidates.json`。

### 事实账本（Claim Ledger）

将筛选后候选转为稳定、可追溯的 claim，写入 `claim_ledger.json`。每个 claim 有唯一 ID、证据文本、来源引用。这是整个流程的控制点：重要表述必须能追溯到 claim。

### 分析师（Analyst）

只使用事实账本中的 claim 写草稿，生成带 `[src:<claim_id>]` 引用的 `audited_brief.md`。不写投资建议，不编造事实。

### 编辑师（Editor）

改善结构、可读性和管理层表达。不发明新事实、不添加无支撑数字。清除 `[SRC:]` 等过程残留，保留有效 `[src:<claim_id>]`。

### 审计师（Auditor）

检查引用支撑、来源新鲜度、数字准确性、投资建议措辞、敏感信息泄漏、过程残留。委托给 `CompositeAuditAgent`（`DeterministicAuditAgent` + `QualityHarnessAuditAgent` + 可选 `SemanticAuditAgent`），写入 `audit_report.json`。

### 定稿器（Formatter / finalize）

`multi-agent-brief finalize` 从 `audited_brief.md` 生成 reader-facing 输出，剥离 `[src:<claim_id>]`，渲染 Markdown/DOCX。

## 质量门禁（Quality Gate）

| 门禁 | 位置 | 简述 |
|---|---|---|
| Doctor | `sources/doctor.py` | 来源配置健康检查 |
| Inputs Classify | `cli/input_commands.py` | 输入文件角色分类 |
| Deterministic Audit | `audit/deterministic.py` | 引用完整性、来源新鲜度 |
| Editorial Governance | `audit/editorial_governance.py` | 事实密度、必须保留事实、读者匹配 |
| Final Quality | `audit/final_quality.py` | 发布前最终文本清理 |
| Limitation Hygiene | `audit/limitation_hygiene.py` | 局限性声明的完整性和准确性 |

## 分析模块

| 模块 | 位置 |
|---|---|
| Market Competitor | `analysis_modules/market_competitor/` |
| Policy & Regulatory | `analysis_modules/policy_regulatory/` |

两个模块通过同一 `analysis_modules/registry.py` 注册，验证模块接口通用性。

## 能力状态

| 能力 | 状态 |
|---|---|
| Claude Code subagent workflow | Supported |
| OpenCode subagent workflow | Supported |
| Codex subagent workflow | Supported |
| Hermes adapter | Supported |
| Manual source (md/txt/json) | Supported |
| Web search (Tavily/Exa/Brave/Firecrawl/Serper) | Supported |
| RSS | Supported |
| SEC Filing resolver | Supported |
| MinerU document parsing | Experimental |
| Local signal discovery | Experimental |
| OpenCLI provider | CLI-only |
| DOCX output | Supported |
| PDF output | Experimental |
| Feishu delivery | Experimental |
| Slack delivery | Interface Only |
| Email delivery | Interface Only |
| Homebrew formula | CLI-only |
| curl installer | CLI-only |
