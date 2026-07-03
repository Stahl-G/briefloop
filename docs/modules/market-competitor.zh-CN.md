# 市场与竞争情报分析模块

> v0.3.0 新增可插拔分析模块

## 概述

市场与竞争情报分析模块（Market & Competitor Intelligence Module）是 `multi-agent-brief-workflow` 的首个可插拔分析模块。它在 Screener/事实账本（事实账本） 与 Analyst 之间运行，将零散的竞对信息转化为结构化分析，支持三种运行模式。

## 三种模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `weekly_monitor` | 周报模式：本周竞对发生了什么新变化 | 每周管理层简报 |
| `competitor_deep_dive` | 深度研究：单家竞对的完整画像 | 战略研究、投资分析 |
| `market_landscape` | 市场全景：行业结构与竞对格局 | 进入新市场、年度战略 |

## 全链路工作流

```text
Onboarding: 用户填写市场范围和竞对偏好
       ↓
competitors init: 创建空白候选模板
  /propose-competitors: LLM subagent 推荐候选竞对 → competitor_candidates.yaml
competitors merge: 用户确认 → competitor_universe.yaml
       ↓
每期运行:
  Source Collection / Scout: 按通用来源策略收集并提取 Claim
  Screener / Claim Ledger: 冻结可追溯的竞对相关 Claim
  MarketCompetitorModule: 基于已冻结 Claim 和竞对配置构建结构化中间产物
  Analyst: 读取 analysis_cards.json 撰写竞对章节
  Editor: 润色管理层表达
  Auditor: 通用审计 + 6 种竞对专项审计
  Formatter: 最终输出
```

## 配置

### competitor_universe.yaml

```yaml
target:
  entity_id: my_company
  name: My Company
  aliases: []
market_scope:
  geographies: [United States]
  products: [HJT cell, solar module]
entities:
  - entity_id: comp_a
    name: Competitor A
    relation: direct_competitor
    priority: primary
    geographies: [United States]
    technologies: [HJT]
mode: weekly_monitor
enabled: false
```

### config.yaml

```yaml
modules:
  market_competitor:
    enabled: true
    mode: weekly_monitor
    max_events: 20
    max_events_per_entity: 4
```

## CLI 命令

```bash
# 创建空白候选模板
multi-agent-brief competitors init --config workspace/config.yaml

# 在 Claude Code 中让 LLM subagent 推荐竞对：
# /propose-competitors workspace/

# 审核后：编辑 competitor_candidates.yaml，将确认的条目改为 approved: true

# 合并已确认竞对

# 查看待审核候选
multi-agent-brief competitors list --config workspace/config.yaml

# 合并已确认竞对
multi-agent-brief competitors merge --config workspace/config.yaml
```

## 中间产物

| 文件 | 说明 |
|------|------|
| `events.json` | 本期所有 MarketEvent |
| `competitor_matrix.json` | 实体 × 维度比较矩阵 |
| `coverage_report.json` | 实体/维度覆盖缺口 |
| `watchlist.json` | 跨期跟踪清单 |
| `evidence_pack.json` | 结构化证据（供 LLM subagent 消费） |
| `analysis_cards.json` | 分析卡片（LLM subagent 生成） |

## 审计规则

| 审计类型 | 说明 |
|---------|------|
| `comparison_missing_entity_evidence` | 比较式判断双方必须有证据 |
| `capacity_status_missing` | 产能事件必须有状态（宣布/建设/投产） |
| `metric_basis_missing` | 数字必须有期间+单位 |
| `unsupported_market_trend` | 趋势判断至少需 2 条 supporting claims |
| `single_source_interpretation` | 单一来源解释必须标记 confidence=low |
| `competitor_coverage_gap` | Primary 竞对覆盖不足时必须报警 |

## 设计原则

1. **不在 Python 层调用外部模型** — 竞对配置、结构化产物和专项审计保持确定性；语义判断留给 agent / human 层
2. **不绕过 事实账本** — AnalysisCard 的 supporting_claim_ids 全部可追溯
3. **分析判断与来源事实分离** — 事实账本（事实）→ AnalysisCard（判断）→ Brief（表达）
4. **禁用时零影响** — 模块关闭时管道行为完全不变
