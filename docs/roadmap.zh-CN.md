# 路线图

本路线图从“继续扩张功能”切换为“先冻结 v1.0 可信参考实现，再启动 v2.0 MAS Runtime”。公开文档只保留可执行方向；完整 agent 参考见 [v1 前置收敛路线图](agents/reference/v1-pre-mas-refactor-roadmap.zh-CN.md)。

## 总原则

v1.0 之前不应推倒现有 Pipeline，也不应继续无限增加搜索后端、专题模块或交付渠道。当前优先级是：

```text
Claim 知识模型
→ Schema / Contract / Run Manifest
→ 审计与发布门控
→ Reference Workflow
→ Golden Dataset
→ v1.0 Stable Baseline
→ v2.0 MAS Runtime
```

v1.0 发布后，现有顺序型 Pipeline 应成为未来 MAS Runtime 的参考引擎、回退引擎和质量对照组。

## v0.4：Knowledge & Governance Contracts

目标：先把未来 Shared World 中最重要的数据结构定义正确。

范围：

- Claim 知识类型升级：区分 `FACT`、`CASE`、`INTERPRETATION`、`HYPOTHESIS`、`ACTION`、`TO_VERIFY`。
- Evidence Relation：区分 `DIRECT`、`COMPARABLE`、`HISTORICAL_ANALOGY`、`BACKGROUND`。
- 核心对象版本化：`SourceItem`、`CandidateItem`、`Claim`、`AnalysisPack`、`AuditReport`、`OutputArtifact`、`RunManifest` 等。
- `run_manifest.json`：记录运行 ID、配置 hash、来源/claim 数、模块、审计状态、错误、输出 artifact 与 hash。
- Semantic Audit 状态修正：未配置不得伪装成 `pass`，需要明确 `not_configured` / `not_run` / `pass` / `warning` / `fail` / `error`。
- Audit Finding 结构化：区分 editor 可修复、analyst 阻断、source 阻断、配置错误、渲染错误和安全阻断。
- Rule Packs：将关键 harness 规则从硬编码逐步迁移为可配置、可测试规则包。
- 预留事件与字段级治理钩子：Claim 可选关联 `event_id`、`field_name`、`fact_role`；`EventRecord` / `EventField` 只作为实验性 contract/interface，不在 v0.4 实现完整事件引擎。

不做：

- 不做 MAS Runtime。
- 不做完整 RAG。
- 不做复杂模型路由。
- 不新增更多搜索后端。
- 不新增大量专题模块。
- 不做完整事件完整性引擎、自动补搜循环或垂直场景经营诊断模块。

完成标准：

- Claim 模型可以表达事实、解释、假设、动作、类比和历史背景的边界。
- 所有核心合同有 schema/version/fixture/test。
- 运行结果可以通过 manifest 追踪和对比。
- 审计状态不会把“未运行”伪装成“通过”。

## v0.4.x / v0.5 桥接：Event Completeness & Editorial Governance

定位：把实际报告中暴露的事件完整性、反馈污染、事实密度和可比案例问题纳入路线图，但不把它们全部塞进 v0.4 主线。

v0.4 只沉淀可复用地基：

- Claim Schema v2 预留事件、字段和事实角色关联，用于后续表达“某个字段来自哪个 Claim”。
- Contracts Package 可以包含实验性的 `EventRecord`、`EventField`、输入内容类型和来源覆盖摘要 schema。
- Audit Finding / Rule Packs 可以预留 `feedback_contamination`、`instruction_leakage`、`editorial_comment_leakage`、`low_factual_density`、`unsupported_business_advice` 等问题类型。
- Run Manifest 可以记录事件数量、来源覆盖摘要和未来补搜统计字段，但这些字段在 v0.4 不得成为强制运行依赖。

v0.5 正式消费这些契约：

- Final Clean 阻断用户反馈、Prompt、版本说明、工作区配置和 Agent 过程信息进入 reader-facing 报告。
- Audience Profiles 定义管理层、研究、IR、法务合规等读者需要的事实密度、禁用风格和 must-preserve facts。
- Comparable Case Contract 要求可比案例说明具体事实、可比维度、不可比边界、适用性理由和本地待验证问题。
- Source Coverage Report / `research_gaps.md` 区分直接本地来源、区域可比来源、全球背景来源、官方来源、社交讨论等覆盖情况。覆盖维度必须可配置，不能把越南、TikTok、Shopee、Lazada 等场景硬编码为核心能力。
- 缺失事实必须显性化为 `not_found`、`not_disclosed`、`not_verified`、`conflicting` 或 `not_applicable`，不得由模型按常识补齐。

v0.5 之后的候选扩展：

- Event Family Registry 与 Event Completeness MVP：先覆盖 M&A、融资、IPO、重大投资、技术发布、专利/法院/法规事件等少量 public-safe 事件族。
- Missing-Fact Search Planner：只允许一轮有预算上限的缺失字段补搜，不新增搜索后端，不做无限 recrawl。
- Field-Level Provenance & Conflict Handling：关键数字、日期、地点、专利号、金额、产能和法规状态必须能追溯到字段级来源，并显式标记冲突。
- Operating Data Hypothesis Framework：把订单取消、退款、支付失败、COD 拒收等经营问题表达为 `hypothesis`、`required_data`、`test_method`、`possible_action`、`confidence_level`，不能把缺数据的猜测写成已验证诊断。

## v0.5：Production Reference Workflow

目标：形成一个真实可用、可审计、可复现的高质量参考工作流。

范围：

- 冻结单一正式主路径：交互初始化 → 来源发现与确认 → doctor → prepare → Analyst → Editor → Final Auditor → Markdown / DOCX → Human Review。
- Reference Workflow：一个维护者本地参考工作流，加一个 public-safe synthetic demo。
- Audience Profiles：至少支持 management、research、IR、legal-compliance，并定义必需章节、禁用风格和审计要求。
- DOCX 模板：支持 Executive Brief、Research Note、Formal Internal Report 三种模板，并做基础 layout validation。
- Final Clean：最终交付文本必须清除内部过程残留、空引用、无效 Claim ID、模板变量、内部路径、不应出现的投资建议措辞、用户反馈污染和 meta 内容泄漏。
- Editorial Governance：加入事实密度、must-preserve facts、可比案例适用性、研究缺口分离和来源覆盖质量门。
- Policy & Regulatory Risk Module：作为第二个与竞对模块性质不同的 Analysis Module，用来验证模块接口通用性。
- HistoryStore 基础接口：支持上一期 brief、上一期 claim ledger、entity history、repeat / novelty 检查；不引入复杂向量数据库。
- Effort Budgets：`low` / `medium` / `high` / `xhigh` 只展开为搜索、来源、claim、语义审计、运行时间和模型调用预算。

完成标准：

- 新用户可以按 README 跑通一次正式流程。
- Reference Workflow 和 synthetic demo 都可复现。
- Markdown 与 DOCX 都有发布级质量门。
- Reader-facing 报告不会把用户反馈、Agent 过程说明、来源缺口说明或下期研究建议混入正式业务正文。
- 至少两个性质不同的 Analysis Module 使用同一 Registry。
- 历史信息进入新报告时不会伪装成当期事实。

## v1.0：Stable Baseline

目标：冻结现有顺序型 Pipeline，作为长期维护版本和 v2.0 MAS Runtime 的质量基线。

范围：

- Golden Dataset：至少包含 normal weekly、sparse market、conflicting sources、quiet week、high-risk input 五类 public-safe 数据集。
- Benchmark Metrics：记录 source count、claim count、citation coverage、unsupported statements、high-risk findings、audit status、runtime、cost、artifact hashes。
- Contract Compliance Tests：覆盖 SourceProvider、AnalysisModule、AuditAgent、OutputRenderer、DeliveryConnector。
- Release Consistency Gate：检查 package version、CHANGELOG、README、Git tag、agent configs、schema versions 和 release notes。
- Formal Support Matrix：明确 Supported / Experimental / Interface Only / Deprecated。
- 建立 `v1-maintenance` 分支：只修 bug、治理漏洞、兼容性和文档，不做大架构重构。

完成标准：

- v1.0 可以从全新安装跑通正式支持能力。
- v1.0 有稳定接口、基准数据和回归指标。
- v1.0 可作为未来 MAS Runtime 的对照组和回退引擎。
- README、Changelog、Git tag、schema version 和 agent configs 不再漂移。

## v2.0：MAS Runtime 候选方向

v2.0 不应在 v1.0 前启动为主路径。v1.0 冻结后，可新建 `mas-runtime` / `v2` 分支探索真正 MAS。

建议第一阶段只做 `mas-runtime-foundation`：

- Shared World / SQLite Event Store。
- Typed Event / AgentMessage envelope。
- TaskBoard、lease、task bidding 或最小 Contract Net。
- AgentState / inbox cursor / capability registry。
- ClaimProposal 状态机。
- 确定性 ClaimReducer，将 proposal 转为正式 Claim Ledger。
- Run replay 与 v1 Claim Ledger 兼容导出。

暂不做：

- 不迁移完整 Analyst / Editor / Auditor / Formatter。
- 不做多服务器、Kafka、Redis 或复杂部署。
- 不把所有 connector 和 analysis module 一次性迁移。
- 不把 v2 作为 README 主路径。

评估详见 [v2.0 MAS Runtime 重构评估](mas-v2-evaluation.zh-CN.md)。

## 暂缓事项

以下事项在 v1.0 前应谨慎控制范围，避免扩大重构成本：

- 更多搜索后端和交付渠道。
- 完整模型路由。
- 完整 RAG / 长期记忆系统。
- 大量专题模块。
- 调度、多租户、团队权限和企业部署。
- 未完成的 PDF / Email / Slack / Telegram 等能力。

对于尚未稳定的能力，README 和 CLI 输出必须明确标记为 Experimental 或 Interface Only。
