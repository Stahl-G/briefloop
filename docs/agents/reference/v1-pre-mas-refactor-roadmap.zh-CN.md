# multi-agent-brief-workflow v1.0 前置收敛与 MAS 重构准备路线图

> Public-safe agent reference copy. Sanitized from the maintainer planning draft; company-specific workflow names were replaced with generic reference-workflow names.

> 用途：本文件用于指导 `multi-agent-brief-workflow` 在进入真正 MAS Runtime 重构前，先完成 v1.0 参考实现的产品收敛、接口稳定、质量基线和版本冻结。
> 目标读者：项目维护者、Codex / Claude Code / OpenCode 等开发 Agent、后续参与重构的协作 Agent。
> 核心原则：**先把现有流水线打磨成稳定、可运行、可审计、可对照的 v1.0 参考引擎，再开始 MAS Runtime 的系统性重构。**

---

## 0. 总判断

当前不应立即进行 MAS 重构。

`multi-agent-brief-workflow` 目前已经具备较完整的多角色 briefing workflow、Claim Ledger、来源管理、审计门控、输出与部分可插拔模块。但它仍然是一个由中央 Pipeline 编排的顺序型 agentic workflow，而不是严格意义上的 MAS。

因此，当前阶段最重要的不是继续把功能堆到“什么都支持”，也不是立即把架构推倒重写，而是将现有系统打磨成一个：

> **功能边界清晰、接口稳定、质量可衡量、可以长期作为对照组的 v1.0 参考实现。**

未来进行 MAS 重构时，现有版本应当成为“已知正确的旧引擎”。新 MAS Runtime 应该能与 v1.0 运行同一套输入、生成可比较的输出、使用同一套质量指标，而不是在重构过程中一边改产品定义、一边改底层架构。

---

## 1. 当前 Roadmap 的重新分类

原 Roadmap 按“近期 / 中期 / 长期”组织，但现在项目已经快速迭代，部分事项已经完成，部分事项已不适合继续在旧架构中深做。

后续不应再简单按时间推进，而应按以下三类重新组织：

| 类别 | 目标 | 处理原则 |
|---|---|---|
| 重构前必须完成 | 冻结产品行为、数据合同和质量基线 | v1.0 前必须完成 |
| 重构前适合完成 | 与架构无关、可长期复用的用户体验能力 | 可做，但控制范围 |
| 重构后再做 | 会与旧流水线深度耦合、未来大概率重写的能力 | 暂缓，不要继续堆复杂度 |

核心判断：

1. 现阶段应从“功能扩张期”切换到“接口稳定与质量基线期”。
2. 继续增加搜索后端、交付渠道、专题模块的边际价值降低。
3. Claim 模型、Audit 合同、Run Manifest、Golden Dataset、Schema Versioning 的优先级应高于新功能。
4. v1.0 应作为未来 MAS Runtime 的参考引擎、回退引擎和质量对照组。

---

## 2. v1.0 前必须完成的 P0 项目

### P0-1. 冻结一个真正可运行的标准路径

#### 背景

当前仓库经历过：

- `run` 被弃用；
- `prepare` 被新增；
- `/generate-brief` 主路径被修复；
- pipeline 与 agent-assisted workflow 并存。

在进入 v1.0 前，必须冻结一条正式标准路径，避免新用户、开发 Agent 和未来重构 Agent 对入口理解不一致。

#### 标准路径

```text
自然语言初始化
→ 来源发现与确认
→ doctor
→ prepare
→ Analyst
→ Editor
→ Final Auditor
→ DOCX / Markdown
→ Human Review
```

#### 必须达到的标准

用户从全新环境开始：

- 不需要修改源代码；
- 不需要理解 YAML 内部字段；
- 不会遇到废弃命令；
- 所有失败均有明确错误说明；
- 最终能够生成一份真实可用的简报；
- 审计失败时不会误称成功；
- 可以清楚知道下一步该做什么。

#### Reference Workflow

建议设定一个正式 Reference Workflow：

```text
Reference Market Weekly Workflow
```

用途：

- 验证真实业务深度；
- 验证来源质量；
- 验证管理层简报风格；
- 验证 Claim Ledger 与审计门控；
- 作为 v1.0 和未来 MAS Runtime 的业务对照组。

同时保留一个完全合成数据的公开 Demo：

```text
Synthetic Solar Market Weekly Demo
```

用途：

- 保证公开仓库可运行；
- 避免真实公司、内部资料、隐私或敏感上下文进入仓库；
- 供 CI、测试和新用户体验使用。

#### 验收标准

- [ ] `README` 中只推荐一条正式主路径。
- [ ] 废弃命令不会出现在主流程中。
- [ ] `AGENTS.md`、`.claude/commands`、`.codex/agents` 与主路径一致。
- [ ] `prepare` 和 `/generate-brief` 的关系清晰。
- [ ] 审计失败时最终输出明确标记为 blocked / not ready。
- [ ] 新用户可以按 README 完成一次全流程。
- [ ] Reference Workflow 可在本地复现。
- [ ] Synthetic Demo 可在无私有凭据情况下运行。

---

### P0-2. 修正 Claim 的“知识类型”模型

#### 背景

当前 Claim 主要区分：

```text
fact / number / date / interpretation / forecast / risk
```

这个分类不足以支撑管理层简报、审计边界和未来 MAS ClaimGraph。

现有需求已经暴露三个关键问题：

1. 报告应区分事实、案例、假设、解释和建议动作；
2. 使用可比市场或历史类比时，必须说明为什么可比、哪里不可比；
3. 用户需要能够手动指定竞品、品类、市场和研究边界。

#### 新 Claim 模型应引入两个独立维度

##### 1. Epistemic Type：这句话是什么

```text
FACT
CASE
INTERPRETATION
HYPOTHESIS
ACTION
TO_VERIFY
```

建议语义：

| 类型 | 含义 | 是否必须有证据 | 是否允许进入管理层摘要 |
|---|---|---|---|
| FACT | 可验证事实 | 是 | 是 |
| CASE | 案例或对照 | 是 | 是，但需说明适用性 |
| INTERPRETATION | 基于事实的分析解释 | 是 | 是，但需保留不确定性 |
| HYPOTHESIS | 待验证假设 | 需要依据，但不能当事实 | 可以，但必须标记 |
| ACTION | 建议动作 | 必须有明确依据 | 谨慎使用 |
| TO_VERIFY | 待验证事项 | 不要求完整证据 | 可作为 Watch Item |

##### 2. Evidence Relation：证据与目标对象是什么关系

```text
DIRECT
COMPARABLE
HISTORICAL_ANALOGY
BACKGROUND
```

建议语义：

| 类型 | 含义 | 审计重点 |
|---|---|---|
| DIRECT | 直接关于目标公司、市场、政策或对象的证据 | 来源、日期、事实准确性 |
| COMPARABLE | 可比公司、市场、品类或行业证据 | 为什么可比、哪些地方不可比 |
| HISTORICAL_ANALOGY | 历史阶段类比 | 历史条件是否相似、是否过度迁移 |
| BACKGROUND | 背景材料 | 不得伪装成当期新增事实 |

#### 示例数据结构

```yaml
claim_id: CLAIM_001
statement: "A comparable U.S. manufacturing segment showed slower ramp-up after policy uncertainty increased."
claim_type: interpretation
epistemic_type: HYPOTHESIS
evidence_relation: COMPARABLE
source_id: SRC_001
evidence_text: "..."
applicability_reason: "The comparable segment has similar capex intensity and policy exposure."
limitations:
  - "The market structure differs from the target segment."
  - "The evidence does not directly prove the target company's future performance."
confidence: medium
requires_audit: true
```

#### 价值

该改造优先级高于继续增加搜索后端，因为它会直接影响：

- Analyst 如何区分事实和推断；
- Auditor 如何使用不同审计规则；
- 管理层如何一眼看懂确定性程度；
- 未来 MAS 中 Evidence Curator 如何判断 Claim 是否应被接受；
- ClaimGraph 如何表达支持、反对、类比和冲突关系。

#### 验收标准

- [ ] `Claim` schema 支持 `epistemic_type`。
- [ ] `Claim` schema 支持 `evidence_relation`。
- [ ] Comparable / Historical claims 必须包含 `applicability_reason` 或 `limitations`。
- [ ] `ACTION` 类型必须有来源依据或降级为 `TO_VERIFY`。
- [ ] `HYPOTHESIS` 不得被 Analyst 写成已确认事实。
- [ ] Auditor 能检查事实、解释、假设、动作之间的错配。
- [ ] 报告输出中能清楚区分 Fact / Interpretation / Hypothesis / To Verify。
- [ ] 旧 claim_type 向新模型兼容迁移。

---

### P0-3. 固定并版本化核心接口合同

#### 背景

“做完接口”不应只是创建抽象类，而应包括：

> **Schema + 验证规则 + 错误语义 + 合规测试 + 版本迁移策略。**

未来 MAS 重构最危险的情况是：

```text
底层运行机制改变
+ Claim / Audit / Module / Workspace 数据结构也同时改变
= 无法判断问题来自哪里
```

因此，重构前必须先冻结核心数据合同。

#### 必须版本化的核心合同

```text
WorkspaceConfig
OnboardingProfile
SourceQuery
SourceItem
CandidateItem
Claim
AnalysisPack
BriefSection
AuditFinding
AuditReport
OutputArtifact
DeliveryArtifact
RunManifest
```

#### 每个合同应具备

- `schema_version`；
- JSON Schema 或明确的验证器；
- 示例 Fixture；
- 向后兼容测试；
- 明确 required / optional 字段；
- 未知字段处理规则；
- 版本升级方式；
- 合同测试；
- 错误类型；
- public-safe 示例。

#### 建议目录结构

```text
src/multi_agent_brief/contracts/
  schemas/
    claim.v1.json
    source_item.v1.json
    audit_report.v1.json
    analysis_pack.v1.json
    run_manifest.v1.json
  validators.py
  migrations.py
  errors.py

tests/contracts/
  fixtures/
    claim_v1.json
    source_item_v1.json
    audit_report_v1.json
  test_claim_contract.py
  test_source_contract.py
  test_audit_contract.py
```

#### 验收标准

- [ ] 所有核心对象都有 `schema_version`。
- [ ] 所有核心对象都有 Fixture。
- [ ] Fixture 通过验证器。
- [ ] 缺失 required 字段会明确失败。
- [ ] 未知字段策略明确。
- [ ] 旧版本数据可被迁移或明确拒绝。
- [ ] AnalysisModule、SourceProvider、AuditAgent、OutputRenderer 都有合同测试。
- [ ] 文档说明哪些字段对 v1.0 稳定，哪些仍为 experimental。

---

### P0-4. 完成真正影响可信度的 Harness Backlog

#### 背景

当前 Harness 已经包含大量确定性检查，但仍有关键 backlog：

- 可配置 Rule Packs；
- Source Tier Policy；
- 结构化章节与必需章节检查；
- 发布模式 Final Clean；
- DOCX/PDF 布局验证；
- 模型支持的语义证据审计；
- 区分 Editor 可修复问题与 Analyst 阻断问题。

这些属于治理规则，未来可以原样迁移到 MAS 架构下，因此应在重构前完成。

#### 建议优先顺序

1. 语义证据审计；
2. 结构化问题分类；
3. 发布模式 Final Clean；
4. DOCX 渲染质量验证；
5. 可配置 Rule Packs；
6. Source Tier Policy。

---

#### P0-4.1 Semantic Audit 不得再是 No-Op Pass

当前 No-Op Semantic Audit 如果未接模型却返回 `pass` 和 100 分，会造成误导。

#### 新状态语义

```text
not_configured
not_run
pass
warning
fail
error
```

建议规则：

| 情况 | 状态 |
|---|---|
| 未配置语义审计 | not_configured |
| 配置但未执行 | not_run |
| 执行且无问题 | pass |
| 有中风险问题 | warning |
| 有高风险不支持陈述 | fail |
| 审计器异常 | error |

#### 验收标准

- [ ] No-Op 不得返回 `pass`。
- [ ] 未配置语义审计时明确写入 `not_configured`。
- [ ] 语义审计结果不得被当作来源证据。
- [ ] Semantic Audit 输出结构化 findings。
- [ ] Analyst-blocking 与 editor-fixable 可区分。
- [ ] 语义审计失败时 Release Gate 可阻断发布。

---

#### P0-4.2 结构化问题分类

Audit Finding 应区分：

```text
editor_fixable
analyst_blocking
source_blocking
configuration_error
rendering_error
safety_blocking
```

建议字段：

```yaml
finding_id: FINDING_001
severity: high
finding_type: unsupported_claim
blocking_level: analyst_blocking
repair_owner: analyst
related_claim_id: CLAIM_001
description: ...
recommendation: ...
```

#### 验收标准

- [ ] 每个 Finding 都有 `blocking_level`。
- [ ] 每个 Finding 都有 `repair_owner`。
- [ ] Final Auditor 能汇总哪些问题可由 Editor 修，哪些必须回到 Analyst 或 Source。
- [ ] Release Gate 能基于阻断等级决策。

---

#### P0-4.3 发布模式 Final Clean

Final Clean 应检查并阻断：

- `[src:]` 空引用；
- 不存在的 Claim ID；
- `SRC:` / `SOURCE:` 残留；
- Claude / Codex 过程残留；
- “Thought for...”；
- “Agent completed”；
- Bash / shell 输出残留；
- 内部路径；
- 占位符；
- 未替换模板变量；
- 低质量免责声明；
- 不应出现的投资建议措辞。

#### 验收标准

- [ ] Final Clean 独立于 Draft Audit。
- [ ] Final Clean 可单独运行。
- [ ] Final Clean 输出结构化报告。
- [ ] Final Clean fail 时不得生成 distribution-ready 状态。

---

#### P0-4.4 DOCX 渲染质量验证

DOCX 不是附属功能，而是管理层交付的重要入口。

至少应检查：

- 文件是否存在；
- 文件是否可打开；
- 标题是否存在；
- 表格数量是否符合预期；
- 页脚是否存在；
- 关键章节是否存在；
- 文档中无残留 `[src:CLAIM_ID]`，除非是审计版；
- 文档文本与最终 Markdown 主要内容一致；
- 不出现空标题、空列表、破损表格。

#### 验收标准

- [ ] DOCX 生成后自动进行基础校验。
- [ ] 渲染失败不得被包装成成功。
- [ ] 渲染问题不得通过修改事实内容掩盖。
- [ ] 三种正式模板均通过 layout validation。

---

### P0-5. 建立 Golden Dataset 与基准测试

#### 背景

大量单元测试不等于拥有可验证业务质量的重构基准。

未来 MAS Runtime 必须与 v1.0 在同一套 Fixture 上比较。如果没有 Golden Dataset，即使新架构更先进，也无法证明最终简报质量是否改善。

#### 至少准备 5 套固定测试集

| 测试集 | 验证内容 |
|---|---|
| 正常高质量周报 | 常规端到端能力 |
| 信息稀缺市场 | 可比市场、历史类比和限制说明 |
| 来源冲突 | 冲突识别和置信度处理 |
| Quiet Week | 没有重大事项时不编造内容 |
| 高风险输入 | 过期来源、弱证据、缺失引用、敏感信息 |

#### 建议目录

```text
benchmarks/
  golden_datasets/
    normal_weekly/
      input/
      expected/
      config.yaml
      evaluation.yaml
    sparse_market/
    conflicting_sources/
    quiet_week/
    high_risk_input/

  run_benchmark.py
  metrics.py
```

#### 每次运行记录指标

```text
source_count
source_coverage_rate
claim_count
claim_acceptance_rate
citation_coverage_rate
unsupported_statement_count
high_risk_finding_count
audit_status
runtime_seconds
model_call_count
estimated_cost
artifact_hashes
```

#### 验收标准

- [ ] 至少 5 套 Golden Dataset。
- [ ] 每套数据均 public-safe 或 synthetic。
- [ ] 可一键运行 benchmark。
- [ ] 输出机器可读 metrics。
- [ ] v1.0 结果可被保存为 baseline。
- [ ] 未来 MAS Runtime 可复用同一套 benchmark。

---

### P0-6. 增加 `run_manifest.json`

#### 背景

在完整 EventLog 出现前，现版本至少应生成一个运行清单。它是未来 MAS `EventLog` 和 `BriefRun` 的过渡版本。

#### 建议字段

```json
{
  "schema_version": "run-manifest/v1",
  "run_id": "...",
  "workflow_version": "1.0.0",
  "workspace_schema_version": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "pass",
  "enabled_providers": [],
  "enabled_modules": [],
  "models_used": [],
  "config_hash": "...",
  "source_count": 0,
  "claim_count": 0,
  "candidate_count": 0,
  "audit_status": "...",
  "audit_score": 0,
  "errors": [],
  "warnings": [],
  "artifacts": {
    "brief_md": "...",
    "brief_docx": "...",
    "claim_ledger": "...",
    "audit_report": "...",
    "source_map": "..."
  },
  "artifact_hashes": {}
}
```

#### 价值

立即解决：

- 一次运行到底用了什么；
- 哪个 Provider 或 Module 失败；
- 为什么两次输出不同；
- 哪份输出对应哪个配置；
- 重构前后如何比较结果。

#### 验收标准

- [ ] 每次正式运行都生成 `run_manifest.json`。
- [ ] 所有 artifact 路径和 hash 写入 manifest。
- [ ] Provider / Module 失败写入 manifest。
- [ ] Audit 状态写入 manifest。
- [ ] Manifest 有 schema 和合同测试。

---

### P0-7. 再做一个真正不同的 Analysis Module

#### 背景

当前已有 Market & Competitor Intelligence Module，但只有一个模块时，很难判断 `AnalysisModule` 接口是真的通用，还是只是围绕竞对模块抽象了一层。

#### 建议新增模块

```text
Policy & Regulatory Risk Module
```

原因：

- 与竞对模块差异足够大；
- 更适合验证 AnalysisModule 的通用性；
- 与管理层周报强相关；
- 可长期迁移到 MAS 中的 Policy Analyst / Regulatory Auditor。

#### 模块关注点

```text
policy_entity
jurisdiction
legal_level
effective_date
proposal_status
affected_industry
affected_company_type
compliance_relevance
business_relevance
uncertainty_level
watch_item
```

#### 与竞对模块的差异

| 维度 | Market & Competitor | Policy & Regulatory |
|---|---|---|
| 核心实体 | 公司、产能、市场事件 | 政策、法规、机构、司法辖区 |
| 核心问题 | 谁在竞争、状态如何变化 | 什么规则变化、何时生效、影响谁 |
| 审计重点 | 实体证据、比较口径、覆盖缺口 | 生效日期、适用范围、不确定性、法律层级 |
| 输出 | competitor matrix / watchlist | policy risk cards / applicability matrix |

#### 完成后应暂停新增专题模块

完成第二个模块后，应停止继续做 earnings、patent、litigation、price 等更多模块，直到 MAS Runtime 重构完成。

#### 验收标准

- [ ] Policy Module 使用同一 `AnalysisModule` Registry。
- [ ] 不破坏 Market Module。
- [ ] 产生结构化中间产物。
- [ ] 有专项审计。
- [ ] 有 Golden Dataset。
- [ ] 有 public-safe 示例。
- [ ] 文档说明该模块如何验证 AnalysisModule 通用性。

---

### P0-8. 解决版本、文档和发布状态漂移

#### 背景

快速开发阶段容易出现版本漂移。重构前必须解决，否则未来 v1 / v2 并存时会非常混乱。

#### 需要统一

```text
pyproject.toml version
package __version__
CHANGELOG latest version
README current version
Git tag
agent configs
schema versions
docs references
```

#### 建议新增 Release Consistency Gate

```text
python scripts/check_release_consistency.py
```

检查：

- `pyproject.toml` 版本；
- `src/multi_agent_brief/__init__.py` 版本；
- `CHANGELOG.md` 最新版本；
- `README.md` 当前版本；
- `AGENTS.md` 是否重新生成；
- `.claude/agents` 是否与 `configs/agent_roles.yaml` 一致；
- `.codex/agents` 是否与 `configs/agent_roles.yaml` 一致；
- schema version 是否记录；
- release notes 是否存在。

#### 验收标准

- [ ] 版本信息一致。
- [ ] CI 执行 release consistency check。
- [ ] 发布前必须更新 Changelog。
- [ ] Agent config 生成物无漂移。
- [ ] README 不再展示过期版本。

---

## 3. 重构前适合完成的 P1 项目

这些能力与未来 MAS Runtime 不强绑定，可以在 v1.0 前完成，但要控制范围。

---

### P1-1. Audience Profiles，而不是简单 Prompt 风格切换

#### 背景

Roadmap 中的“智能语气调节”值得做，但不要只做 Prompt 文案切换。它应成为稳定的 `AudienceProfile` 合同。

#### 示例

```yaml
schema_version: audience-profile/v1
audience_type: management
detail_level: concise
risk_tolerance: cautious
required_sections:
  - executive_summary
  - key_changes
  - implications
  - watch_items
forbidden_styles:
  - trading_signal
  - unsupported_recommendation
  - overconfident_strategy
citation_policy:
  require_source_dates: true
  require_claim_ids_in_audit_version: true
```

#### 价值

未来 MAS 中：

- Planner 读取 audience profile 规划报告；
- Analyst 根据 audience profile 写作；
- Auditor 根据 audience profile 进行审计；
- Editor 根据 audience profile 控制语气。

#### 验收标准

- [ ] 至少支持 management / research / IR / legal-compliance 三种 profile。
- [ ] 每种 profile 有 required sections。
- [ ] Auditor 能检查 required sections。
- [ ] Editor 不能通过风格优化改变事实边界。

---

### P1-2. 三种 DOCX 模板与渲染验证

#### 建议正式支持三种模板

```text
Executive Brief
Research Note
Formal Internal Report
```

#### 不建议无限增加模板

只做三种足以覆盖主要场景：

- 管理层简报；
- 研究笔记；
- 正式内部报告。

#### 验收标准

- [ ] 三种模板均可从 config 选择。
- [ ] 模板不改变实质内容。
- [ ] 三种模板均通过 DOCX Layout Validation。
- [ ] 模板差异集中在标题、页脚、样式、章节展示方式，不改变事实。

---

### P1-3. Effort 设置，但只实现预算合同

#### 背景

`low / medium / high / xhigh` 很适合未来 MAS，因为它最终可转化为 Agent 预算。但现在不应做复杂模型路由。

#### 建议语义

```yaml
effort: high

budgets:
  max_search_tasks: 30
  max_sources: 150
  max_claims: 80
  require_second_source_for_high_risk: true
  semantic_audit: true
  output_depth: detailed
  max_runtime_seconds: 900
  max_model_calls: 20
```

#### 验收标准

- [ ] Effort 能展开为明确预算。
- [ ] Budget 写入 `run_manifest.json`。
- [ ] Provider 和 Module 能读取预算。
- [ ] 超出预算时有明确状态。
- [ ] 不引入复杂阶段式模型路由。

---

### P1-4. HistoryStore 基础接口，而不是完整 RAG

#### 背景

Roadmap 中的 RAG 不建议在重构前完整实现。现在只需要建立最小历史记忆接口。

#### 建议接口

```text
HistoryStore
- save_run()
- get_previous_brief()
- get_previous_claims()
- get_entity_history()
- find_similar_past_items()
```

底层可继续使用：

```text
JSON
JSONL
local file store
```

#### 不应做的事

- 不要引入复杂向量数据库；
- 不要做长期记忆系统；
- 不要将 RAG 深度绑定旧 Pipeline；
- 不要把历史记忆作为未经审计的事实来源。

#### 验收标准

- [ ] 能读取上一期 brief。
- [ ] 能读取上一期 claim ledger。
- [ ] 能支持 novelty / repeat detection。
- [ ] 历史内容进入当前报告时必须标为 background 或 previous context。
- [ ] HistoryStore 有合同测试。

---

## 4. 重构前不建议继续做的项目

### 暂停增加更多搜索后端

当前已经有多个搜索后端、SEC Filing、MinerU、飞书和其他来源接口。继续增加新的搜索 Provider 对核心质量提升有限，且会扩大未来迁移负担。

应转向：

- Provider 合规测试；
- 错误语义统一；
- 来源质量；
- 日期质量；
- 可重试性；
- 去重和追溯。

---

### 暂缓完整模型路由

可以先定义：

```text
ModelProvider
ModelRequest
ModelResponse
ModelUsage
```

但不要构建复杂的按角色、按阶段模型路由系统。

原因：

- 未来 MAS 中 Agent 会动态选择行动；
- 不同任务预算不同；
- 模型调用不再严格对应固定 Pipeline Stage；
- 现在构建复杂阶段式路由，重构后很可能重写。

---

### 暂缓完整 RAG

只做 HistoryStore 合同，不做完整 RAG。

---

### 暂缓大量专题模块

完成第二个模块验证接口后停止。不要继续堆：

- earnings；
- patent；
- litigation；
- price；
- supply chain；
- social media；
- product review。

这些等 MAS Runtime 后再模块化接入。

---

### 暂缓调度、全渠道分发和企业部署

以下能力都容易绑定现有运行方式，建议重构后再做：

- 每日 / 每周调度；
- Telegram / SMS / Email 等全渠道分发；
- 私有来源；
- 企业部署；
- 团队权限；
- 多租户。

飞书已经可作为一个完整交付路径。其他渠道只保留接口与状态说明。

---

### PDF、Email 等接口必须明确状态

对于当前未完整实现的输出或交付能力，必须明确状态：

```text
Supported
Experimental
Interface Only
Deprecated
```

v1.0 建议正式支持：

```text
Markdown
DOCX
Feishu
```

其余保持：

```text
Experimental / Interface Only
```

并且不得在 README 主流程中表现为正式可用。

---

## 5. 建议版本编排

### v0.4 — Knowledge & Governance Contracts

目标：先把未来 Shared World 中最重要的数据结构定义正确。

#### 范围

- Claim 的 Epistemic Type；
- Direct / Comparable / Historical Evidence；
- 手动研究范围与竞品输入；
- Schema Versioning；
- Run Manifest；
- 统一错误类型；
- Rule Packs；
- 真实 Semantic Audit Adapter。

#### 不做

- 不做 MAS Runtime；
- 不做完整 RAG；
- 不做复杂模型路由；
- 不新增大量专题模块；
- 不新增更多搜索后端。

#### 完成标准

- [ ] Claim 模型具备未来 ClaimGraph 基础。
- [ ] 所有核心合同版本化。
- [ ] Semantic Audit 不再 No-Op Pass。
- [ ] Run Manifest 可用于未来对照测试。

---

### v0.5 — Production Reference Workflow

目标：形成一个真实可用的高质量版本。

#### 范围

- 标准端到端运行路径；
- Audience Profiles；
- 三种 DOCX 模板；
- Final Clean；
- DOCX Layout Validation；
- Policy & Regulatory Risk Module；
- HistoryStore 基础接口；
- `low / medium / high / xhigh` 预算合同；
- Reference Workspace；
- 公开 Synthetic Demo。

#### 完成标准

- [ ] 新用户可从 README 完成一次正式工作流。
- [ ] Reference Workflow 可稳定运行。
- [ ] DOCX 可作为管理层交付件。
- [ ] 两个 AnalysisModule 验证接口通用性。
- [ ] 历史基线可用于 novelty / repeat detection。

---

### v1.0 — Stable Baseline

目标：冻结现有流水线，作为未来 MAS 的参考引擎。

#### 范围

- CLI 和核心接口冻结；
- Golden Dataset；
- 完整回归基准；
- Connector / Module / Audit 接口合规测试；
- Release Consistency Gate；
- 安装、升级、兼容性文档；
- 所有正式支持能力均可从全新安装运行；
- 建立 `v1-maintenance` 分支。

#### 完成标准

- [ ] v1.0 可作为长期维护版本。
- [ ] v1.0 可作为 MAS Runtime 的对照组。
- [ ] v1.0 输出质量可度量。
- [ ] v1.0 有明确支持范围。
- [ ] v1.0 有基准数据和回归指标。

---

## 6. v1.0 后的分支策略

v1.0 发布后建立：

```text
v1-maintenance
```

用途：

- 修 Bug；
- 修治理漏洞；
- 修兼容性；
- 更新文档；
- 不做大架构变更。

同时新建：

```text
mas-runtime / v2
```

用途：

- Event Store；
- Shared World；
- Agent Message；
- TaskBoard；
- Contract Net；
- ClaimGraph；
- AgentState；
- Audit Challenge / Revision Loop；
- MAS Runtime。

规则：

- v2 不应破坏 v1-maintenance。
- v2 必须复用 v1 Golden Dataset。
- v2 必须输出可与 v1 比较的 metrics。
- v2 不得一开始就重写全部功能。
- v2 先做 Runtime Foundation，再逐步迁移 Agent 行为。

---

## 7. 何时可以正式开始 MAS 重构

满足以下条件后，才应开始 MAS Runtime 重构：

- [ ] 从全新安装可以完整生成一份高质量 Reference Brief；
- [ ] 没有需要用户修改源代码才能完成的正式流程；
- [ ] Claim、Source、Audit、Module、Output 合同全部版本化；
- [ ] Fact、Interpretation、Hypothesis、Action 可以被明确区分；
- [ ] Direct、Comparable、Historical Evidence 可以被明确区分；
- [ ] 至少两个性质不同的 AnalysisModule 使用同一接口；
- [ ] Semantic Audit 不再是 No-Op Pass；
- [ ] 最终 Markdown 和 DOCX 有发布级质量门控；
- [ ] 所有正式支持的 Connector 有合同测试；
- [ ] 每次运行生成 `run_manifest.json`；
- [ ] 已建立 Golden Dataset 和质量 / 成本基准；
- [ ] 版本、README、Changelog 和 Git Tag 一致；
- [ ] 已发布并冻结 v1.0；
- [ ] 已建立 `v1-maintenance` 分支；
- [ ] 已明确 v2 / MAS Runtime 的最小启动范围。

---

## 8. MAS Runtime 启动时的最小范围

正式进入 MAS 重构后，第一阶段不应重写全部系统。

建议 v2 第一个 PR / milestone 只做：

```text
mas-runtime-foundation
```

### 最小范围

- Event Store；
- AgentMessage；
- Task；
- TaskBoard；
- AgentState；
- ClaimProposal；
- ClaimReducer；
- Run replay；
- 与 v1 Claim Ledger 的兼容导出。

### 不做

- 不做完整 Analyst；
- 不做完整 Auditor；
- 不做复杂 UI；
- 不做多服务器部署；
- 不做所有 Connector 迁移；
- 不做完整 RAG；
- 不做全渠道分发。

### 第一阶段目标

将以下逻辑从“共享内存 + 顺序调用”改为“事件 + 状态变更”：

```text
SourceItem
→ ClaimProposal
→ ClaimReducer
→ ClaimLedger
```

也就是先重构 Claim 生成与接受机制，而不是先重构写作、DOCX 或交付。

---

## 9. Agent 执行规则

后续开发 Agent 在执行本文件时，应遵守以下规则。

### 9.1 优先级规则

优先级从高到低：

1. 数据合同和质量基线；
2. 可运行 Reference Workflow；
3. 审计与发布门控；
4. 用户体验改进；
5. 新模块；
6. 新连接器；
7. 新交付渠道；
8. MAS Runtime 重构。

如果某个任务会扩大未来重构成本，但不提高 v1.0 质量基线，应暂缓。

---

### 9.2 不得做的事

开发 Agent 不得：

- 立即推倒现有 Pipeline；
- 在 v1.0 前引入 MAS Runtime 作为主路径；
- 继续无限增加搜索后端；
- 继续无限增加专题模块；
- 将 No-Op Audit 标记为 pass；
- 将接口占位能力包装成正式支持；
- 绕过 Claim Ledger；
- 绕过 Screener；
- 绕过 Final Auditor；
- 在 README 主路径中推荐未稳定能力；
- 在 public repo 中加入公司内部资料、真实凭据、内部路径、客户数据或未授权材料；
- 为了通过测试削弱审计规则；
- 把模型判断当作事实来源；
- 把历史背景当作当期新增事实；
- 把类比证据当作直接证据；
- 把待验证动作写成确定性建议。

---

### 9.3 必须做的事

开发 Agent 必须：

- 修改接口时同步更新 schema / fixture / tests / docs；
- 修改角色配置时重新生成 agent configs；
- 修改正式功能时更新 README 与 Changelog；
- 修改输出逻辑时更新 Golden Dataset 或说明不影响基准；
- 修改审计规则时新增正反测试；
- 新增 Connector 时提供合同测试和错误语义；
- 新增 AnalysisModule 时证明不破坏现有模块；
- 新增正式输出格式时提供渲染验证；
- 每次重要运行写入 `run_manifest.json`；
- 保持 public-safe 示例；
- 保持 v1.0 可作为未来 MAS 对照组。

---

## 10. 推荐任务拆分

### Milestone 0.4.0：Knowledge & Governance Contracts

#### Task 0.4-1：Claim Schema v2

- 增加 `epistemic_type`；
- 增加 `evidence_relation`；
- 增加 `applicability_reason`；
- 增加 `limitations`；
- 增加兼容迁移；
- 增加审计规则。

#### Task 0.4-2：Manual Market Scope / Competitor Input

- 支持手动输入公司、品牌、SKU、品类、价格带、渠道、市场、关系说明；
- 写入 workspace；
- 来源发现优先围绕手动输入展开；
- 不只存在对话上下文中。

#### Task 0.4-3：Schema Versioning

- 核心对象加 `schema_version`；
- 建立 contracts 目录；
- 建立验证器；
- 建立 fixture。

#### Task 0.4-4：Run Manifest

- 每次正式运行生成；
- 包含配置、模块、来源、审计、产物、hash；
- 写入 output/intermediate 或 output 根目录。

#### Task 0.4-5：Semantic Audit Adapter

- No-Op 不得 pass；
- 增加真实 adapter 接口；
- 增加 not_configured / not_run / pass / warning / fail / error 状态；
- 增加 findings 结构。

#### Task 0.4-6：Rule Packs

- 将硬编码 harness regex 迁移为可配置规则包；
- 支持 audience / industry / report_type 选择；
- 保持默认规则 public-safe。

---

### Milestone 0.5.0：Production Reference Workflow

#### Task 0.5-1：Reference Workflow

- Reference Market Weekly Workflow；
- Synthetic public demo；
- 端到端验证；
- README 主流程更新。

#### Task 0.5-2：Audience Profiles

- management；
- research；
- IR；
- legal-compliance；
- required sections；
- forbidden styles；
- audit policy。

#### Task 0.5-3：DOCX Templates

- Executive Brief；
- Research Note；
- Formal Internal Report；
- Layout Validation。

#### Task 0.5-4：Policy & Regulatory Risk Module

- 使用 AnalysisModule Registry；
- 结构化产物；
- 专项审计；
- Golden Dataset；
- 文档。

#### Task 0.5-5：HistoryStore

- previous brief；
- previous claims；
- entity history；
- repeat / novelty；
- 本地 JSON / JSONL 实现。

#### Task 0.5-6：Effort Budgets

- low / medium / high / xhigh；
- 展开为预算；
- 写入 run_manifest；
- 不做复杂模型路由。

---

### Milestone 1.0.0：Stable Baseline

#### Task 1.0-1：Golden Dataset

- normal weekly；
- sparse market；
- conflicting sources；
- quiet week；
- high risk input；
- metrics 输出。

#### Task 1.0-2：Contract Compliance Tests

- SourceProvider；
- AnalysisModule；
- AuditAgent；
- OutputRenderer；
- DeliveryConnector。

#### Task 1.0-3：Release Consistency Gate

- pyproject；
- package version；
- CHANGELOG；
- README；
- Git tag；
- agent configs；
- schema versions。

#### Task 1.0-4：Formal Support Matrix

明确：

```text
Supported:
- Markdown
- DOCX
- Feishu
- Manual / RSS / Web Search / SEC / MinerU as configured

Experimental:
- PDF
- Email
- Slack
- Telegram
- Scheduling

Interface Only:
- Not-yet-implemented connectors
```

#### Task 1.0-5：v1 Freeze

- 发布 v1.0；
- 建立 `v1-maintenance`；
- 文档声明 v1 为 MAS 重构对照组；
- 开始 v2 / mas-runtime 分支规划。

---

## 11. 最核心的优先级总结

正式重构前，真正值得投入的顺序是：

```text
知识结构与 Claim 模型
→ 质量治理与语义审计
→ 稳定接口与版本合同
→ 端到端 Reference Workflow
→ Golden Dataset 与基准测试
→ 再开始 MAS 重构
```

不应采用的顺序是：

```text
更多搜索引擎
→ 更多 Agent 名称
→ 更多专题模块
→ 更多交付渠道
→ 最后才考虑合同和质量
```

完成以上准备后，旧 Pipeline 不会成为需要被抛弃的历史包袱，而会成为新 MAS 系统最重要的测试基准、回退引擎和可信度来源。

---

## 12. 给后续 Agent 的一句话指令

> 在进入 MAS Runtime 重构前，不要继续扩张功能面。先完成 v1.0 的数据合同、Claim 知识类型、审计门控、Reference Workflow、Golden Dataset、Run Manifest 和版本一致性。v1.0 必须成为未来 MAS Runtime 的稳定对照组，而不是被重构抛弃的临时脚手架。
