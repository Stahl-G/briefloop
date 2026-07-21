# BriefLoop 架构参考 v0.6.1：候选文献筛选记录

**筛选日期**：2026-07-19  \
**用途**：为 v0.6.1 中英文架构报告选择同一组参考文献；本文件不是“全部资料都必须引用”的清单。  \
**原则**：正文只保留能够直接支撑核心论证、实现定位、评测设计或关键能力边界，且在同类资料中具有不可替代性的来源。

## 决策词汇

- **纳入**：在 v0.6 正文或附录承担明确论证功能。
- **延伸阅读**：相关但与已纳入来源重复，或对应能力尚未实现；不进入本版正式参考文献。
- **背景资料**：可帮助理解产业语境，但证据强度或稳定性不足以承担架构论证。
- **排除**：聚合页、产品推广页或与本报告主论点距离较远；不引用。

## 学术论文与预印本

| ID | 候选来源 | 决策 | v0.6.1 中的学术功能或排除理由 |
|---|---|---|---|
| P01 | LIFE-HARNESS | 纳入 | 证明运行时接口可以成为冻结模型之外的独立优化对象；用于界定确定性 benchmark 与开放域简报的差异。 |
| P02 | Self-Harness | 纳入 | 支撑失败挖掘、有界提案、域内/留出回归的改进协议。 |
| P03 | Agentic Context Engineering | 延伸阅读 | 与上下文演化有关，但 v0.5 不实现自主上下文进化；核心论点已由 P01、P02、P05 覆盖。 |
| P04 | Meta Context Engineering | 延伸阅读 | 技能演化机制与 BriefLoop 当前人工批准、未来运行生效的边界不等价，且与 P05 重复。 |
| P05 | Meta-Harness | 纳入 | 提供端到端 harness 优化的最新研究对照；用于说明“可优化”不等于“可自批”。 |
| P06 | DRA Multi-Turn | 纳入 | 直接支撑过程级反馈、定向修复和多轮回归检查。 |
| P07 | CHAP | 纳入 | 支撑工件化、追加式证据日志和可审计人机协作协议。 |
| P08 | Precision Is Not Faithfulness | 纳入 | 支撑反古德哈特与覆盖—精度配对门禁。 |
| P09 | AutoGen | 纳入 | 作为多智能体对话式框架基线。 |
| P10 | CAMEL | 纳入 | 作为角色扮演与通信协作基线。 |
| P11 | MetaGPT | 纳入 | 作为 SOP 编码和角色流水线基线。 |
| P12 | FActScore | 纳入 | 支撑把长文本拆成原子事实再评估支持情况。 |
| P13 | ALCE | 纳入 | 支撑引用正确性与引用完整性应被分开评估。 |
| P14 | G-Eval | 纳入 | 为语义质量评估提供 LLM 评审方法，同时保留其偏差边界。 |
| P15 | MT-Bench | 纳入 | 支撑 LLM-as-a-judge 的可扩展性及位置、冗长、自偏好等已知偏差。 |
| P16 | Self-Refine | 纳入 | 作为模型内部自反馈式迭代修订基线，与外部门禁闭环形成对照。 |
| P17 | Reflexion | 纳入 | 作为语言反馈和情节记忆式改进基线，与人类批准、冻结快照相区分。 |
| P18 | ReAct | 延伸阅读 | 是推理—行动范式的重要基础，但不直接解释 BriefLoop 的控制面、证据门禁或修订协议。 |
| P19 | AI Agents That Matter | 纳入 | 支撑成本控制、基准捷径、复现性和面向真实用途的评测取向。 |
| P20 | ResearchLoop | 纳入 | 直接对照外置证据门禁、声明绑定与研究工件审计。 |
| P21 | EvoMAS | **纳入（用户指定必引）** | 支撑从执行轨迹出发、在结构化配置空间中进化多智能体系统；同时明确不证明 BriefLoop 已实现自动架构进化。 |
| P22 | Knowledge Conflicts for LLMs: A Survey | 纳入 | 建立 context-memory、inter-context、intra-memory 三类知识冲突的正式分类；不外推为 BriefLoop 已实现冲突检测。 |
| P23 | StreamingQA | 纳入 | 以 14 年带时间戳新闻直接支撑“更新检索空间有帮助，但过时底层模型仍然重要”的平衡论点。 |
| P24 | DYNAMICQA | 纳入 | 支撑动态事实更易形成参数记忆内部冲突、且更难被新上下文更新；不把采样诊断当成通用检测器。 |
| P25 | Tug-of-War between Knowledge | 纳入 | 支撑正确证据进入上下文后仍可能被旧记忆、数量偏差和确认偏差压过；不声称所有商业模型行为相同。 |
| P26 | QACC | 纳入 | 为真实网页检索中的 inter-context conflict 提供数据；正文必须把约 25% 限定在论文的 Google Search 设置。 |
| P27 | When Facts Change | 纳入 | 直接支撑“口头识别时间变化不等于最终裁决正确”；不外推成所有 prompt 方法无效。 |
| P28 | Who's Who | 纳入 | 支撑冲突缺乏充分裁决依据时应透明暴露，而不是模型静默选择；不声称所有冲突都必须升级人工。 |
| P29 | Harmful Factuality | 纳入 | 支撑事实正确性与来源忠实度分离，并为 source-fidelity fixture 提供研究依据。 |
| P30 | Time-Aware Language Models | 纳入 | 为事实具有时间有效域和模型训练时间快照提供基础研究；只承担背景与评测设计，不证明现行实现。 |
| P31 | FreshLLMs | 纳入 | 作为平衡证据说明搜索增强可以改善快速变化知识问答；避免把相关研究写成“RAG 没用”。 |
| P32 | Astute RAG | 纳入 | 说明冲突感知、来源感知 RAG 是有效研究方向；不把 benchmark 答案选择升级为企业发布权威。 |
| P33 | Credibility-Aware Generation | 纳入 | 支撑把来源可信度作为显式信号；明确来源等级不能自动决定真相。 |
| P34 | Don't Ask the LLM to Track Freshness | 纳入（预印本） | 为显式版本标记、可全序 current-value 冲突提供确定性微基线；正文必须保留管线级效应、合成任务和 LongMemEval 非胜出边界。 |
| P35 | ConflictRAG | 纳入（预印本） | 作为显式冲突检测—分类—处置—生成的邻近方法和计划基线；不采用其检索优先、近期性排序或 CARS 作为发布权威。 |

## 理论、标准与架构基础

| ID | 候选来源 | 决策 | v0.5 中的学术功能或排除理由 |
|---|---|---|---|
| T01 | Workflow Patterns | 纳入 | 为阶段、分支、同步与完成语义提供工作流理论谱系。 |
| T02 | Blackboard Architecture | 纳入 | 为共享工件状态与专门角色协作提供架构谱系。 |
| T03 | Design by Contract | 纳入 | 支撑前置条件、后置条件和不变量从软件对象扩展到智能体工件边界。 |
| T04 | NIST AI RMF 1.0 | 延伸阅读 | 可提供治理语境，但 v0.5 不做 NIST 对照评估，也不应暗示合规。 |
| T05 | ISO/IEC 42001:2023 | 延伸阅读 | 属于组织级管理体系标准；项目未做符合性或认证评估。 |
| T06 | W3C PROV-DM | 纳入 | 为实体、活动、责任主体及派生关系提供 provenance 词汇基础；不声称完整兼容。 |

## 产业与工程资料

| ID | 候选来源 | 决策 | v0.5 中的学术功能或排除理由 |
|---|---|---|---|
| E01 | OpenAI — Building Self-Improving Tax Agents with Codex | 纳入 | 一手生产案例：专家修正、生产追踪、评测目标和有界工程修复。 |
| E02 | Anthropic — Enterprise AI Category | 排除 | 聚合页，不是稳定、可定位的单篇证据。 |
| E03 | The Claude Cowork Product Guide | 背景资料 | 支撑产品形态但不承担架构论证；与 E04、E05 等产品资料重复。 |
| E04 | Best Practices for Getting Started with Claude Cowork | 背景资料 | 周期性知识工作建议，证据层级偏产品最佳实践。 |
| E05 | How Anthropic Enables Self-Service Data Analytics with Claude | 延伸阅读 | 企业分析案例有启发，但其内部指标不能外推到 BriefLoop。 |
| E06 | How Kepler Built Verifiable AI for Financial Services with Claude | 延伸阅读 | 确定性验证案例相关，但本版已有更直接的架构与评测来源。 |
| E07 | How Claude Code Works in Large Codebases | 背景资料 | 代码库类比有用，但不是简报控制面的直接证据。 |
| E08 | Zero Trust for AI Agents | 延伸阅读 | 安全语境相关；v0.5 尚不提供完整威胁模型或零信任实现证明。 |
| E09 | Centrally Manage Authorization for MCP Connectors | 背景资料 | 产品特定授权资料；不证明完整连接器治理。 |
| E10 | Building Multi-Agent Systems: When and How to Use Them | 纳入 | 为何使用/不使用多智能体的工程边界，与三篇学术框架形成互补。 |
| E11 | Multi-Agent Coordination Patterns | 延伸阅读 | 与 E10 及 P09–P11 重复，避免产业资料压过学术基线。 |
| E12 | Claude Managed Agents | 背景资料 | 产品资料，不能证明通用架构或性能。 |
| E13 | The Evolution of Agentic Surfaces | 背景资料 | 产品表面演进资料，与本报告控制面主线距离较远。 |
| E14 | Equipping Agents for the Real World with Agent Skills | 延伸阅读 | 技能封装相关，但不直接支撑发布权威或证据门禁。 |
| E15 | Introducing Citations on the Anthropic API | 背景资料 | API 产品能力，不等同于引用正确性或语义支持。 |
| E16 | Claude Enterprise, Now Available Self-Serve | 排除 | 产品发布资料，与架构论点相关性低。 |
| E17 | Building Agents That Reach Production Systems with MCP | 背景资料 | 连接器接入案例；不构成权限与治理完整证据。 |
| E18 | Cowork and Plugins for Teams Across the Enterprise | 排除 | 产品推广资料，与本版核心研究问题重复度高、证据功能弱。 |
| E19 | Lilian Weng — Harness Engineering for Self-Improvement | 纳入 | 研究综述/技术文章，用于领域定义和风险综合；不承担单项实验结果。 |
| E20 | Addy Osmani — Loop Engineering | 纳入 | 只用于术语与工程范式归因，不作为 BriefLoop 实验依据。 |
| E21 | Anthropic — Building Effective Agents | 纳入 | 支撑 workflow/agent 区分和清晰阶段边界的工程取向。 |
| E22 | Hermes Agent | 纳入 | 作为人类可编辑文件记忆表面的设计来源；用于说明 BriefLoop 额外增加的批准、冻结和生效证据。 |

## 筛选结果

- 候选对象：63。
- v0.6.1 正式纳入：42。
- 延伸阅读：12。
- 背景资料：6。
- 排除：3。
- 正式纳入不等于每条都支持 BriefLoop 的实现或性能；每条仍必须在唯一引用索引中写明 `supports` 与 `does_not_support`。

最终参考文献与正文使用位置由 `docs/tech-report-v0.6.1/reference-index.md` 统一管理。若正文删除某一论点，对应来源也应从正式索引撤下或降级，避免“孤儿引用”。
