**MABW：合约治理多智能体企业简报工作流**

*架构参考 v0.3.0 --- 技术报告\
代码快照: v0.8.3 \| 分支: main \| 日期: 2026-06-19*

## 架构参考 v0.3.0 --- 技术报告

**代码快照**：v0.8.3 **分支**：main **日期**：2026-06-19

> **历史命名说明。** 该文档曾误存为
> `mabw-architecture-reference-v0.2.0.md`，但正文标题、摘要和关于本报告
> 一节均描述 v0.3.0 架构参考。旧路径现仅作为兼容指针保留。

> **当前版本边界。** 本报告撰写 v0.2.0 设计框架并将其映射到 v0.8.3 实现基线。各版本关键能力落地：运行时状态与工件注册表（v0.6.x）、反馈/修复生命周期与确定性质量门禁报告（v0.6.2--0.6.3）、公开安全评测用例（v0.6.4）、溯源投影（v0.6.5）、受众画像运行时界面及每运行冻结快照（v0.6.6）、Orchestrator 控制总开关（v0.6.7）、面向读者的来源附录与交付包（v0.6.8）、运行时资产安装器（v0.6.9）、带 SHA-256 链式哈希与人类门禁物化的改进账本（v0.7.0）、阶段范围质量门禁报告与运行完整性污染追踪（v0.7.5）、确定性修复路由器（v0.7.5）、快速重跑导入事务与事实层归档（v0.8.1），以及 Claim Freeze Transaction------claim_drafts.json 作为 agent 起草输入、确定性 CL-#### ID 分配、Python 持有 claim ledger 冻结权限（v0.8.3）。质量门禁报告是确定性检查，不进行语义事实验证。反馈问题与修复计划被记录------选择不等同于执行。这些控制面不是长期记忆系统，也不是语义证明。

## 摘要

企业简报依赖一门让新人分析师花费数月才能掌握的手艺：写出不仅事实正确，还符合部门隐性品味与未明说的编辑偏好的简报。单 LLM 方案在这里双重失败------它无法保证事实正确性（没有审计轨迹，不可修复），也无法积累品味偏好（每次运行从零开始）。

2025--2026 年间，企业 AI 的前沿实践正在从对话式交互走向可交付、可审计、可治理的 agent 工作流。Anthropic 将 Claude Cowork 定位为"knowledge work agent"，可以读写本地文件、跨连接应用工作、执行多步骤任务，并生成带有来源引用的可交付成果 [A2]。其最佳实践文档将 recurring、multi-input、file-output 的知识工作定义为一种与 chat 截然不同的任务形态 [A3]。在金融领域，Kepler 将确定性基础设施作为信任与验证层，将 Claude 作为推理与解释层，每个数字追溯到具体 filing、page 和 line item [A5]。在数据分析领域，Anthropic 发现准确性主要是上下文与验证问题，而非代码生成问题------三类主要错误是概念歧义、数据过时和检索失败 [A4]。

MABW（多智能体简报工作流）在 recurring enterprise briefing 这个垂直场景里，把这条产业趋势具体化。正确性由四类合约范畴治理，通过文件状态控制面执行。品味被捕获在 audience_profile.md 和一个带 SHA-256 链式哈希的改进账本中------人类撰写、人类批准、冻结为每次运行的快照。证据由分阶段 claim pipeline 治理，最终收敛于 v0.8.3 Claim Freeze Transaction：agent 起草不含 ID 的 claim_drafts.json；Python 分配确定性 CL-#### ID 并冻结权威 claim_ledger.json。Orchestrator 委派专家角色；阶段完成事务与质量门禁在每个边界执行流程完整性。

v0.9 从 source-level traceability 推进到 evidence-span-level support sufficiency。目标不是证明真相或消除幻觉。目标是使质量随机性成为可观察、可归因、可阻断、可复现、可比较和可裁决的。v0.9 的最小路径是：Atomic Claim Graph → Evidence Span Registry → Claim-Support Matrix → Semantic Assessment as Proposal → Human Adjudication Queue → Coverage Gate → Release Eligibility Scorecard。语义模型可以评估、质疑、提出标签，但不能直接决定发布资格、声明支持真相或归档等级------这些由 schema、policy、adjudication state 和 blocking rules 推导。

在改进闭环层面，MABW 与 OpenAI Tax AI 的生产级模式独立收敛：自我改进不是 agent 自己反思自己，而是生产系统把失败变成可验证的工程任务 [A+1]。领导修改、审计发现、引用错配、支持不足，会被转成结构化 finding；只有可复现、可归类、可测试的问题，才进入改进队列，经过有边界的修复、同证据回归验证和人类审查后更新发布资格。

本报告记录了将企业简报转变为一个**可审计、可追溯、人类门禁的改进循环**------而非一个更聪明的提示词------的设计哲学、架构与实现基线。MABW 操作化语义支持充分性。MABW 不证明真相或消除幻觉。

> **关于本报告。** v0.3.0 在 v0.2.0 基础上新增：（1）产业实践证据章节（§10.7），引入 OpenAI Tax AI [A+1]、Claude Cowork [A2][A3]、Anthropic 数据分析 [A4]、Kepler 金融服务 [A5]、Claude Code 大型代码库 [A6]、Zero Trust [A7]、多智能体系统 [A9][A10] 等 18 篇官方文献作为企业 AI 从 chat 走向 deliverable agent、harness、governance、verification layer 和 production self-improvement 的产业趋势证据；（2）v0.9 设计原理文档（docs/tech-report-v0.3.0/v09-design-rationale.md），将 Claude/Anthropic/OpenAI 产业证据与 Atomic Claim Graph、Evidence Span Registry、Claim-Support Matrix、Semantic Assessment as Proposal、Human Adjudication Queue、Coverage Gate、Release Eligibility Scorecard、Finding Candidate System 八个控制面建立连接；（3）摘要重写，显式声明 v0.9 的 transition 定位和口径纪律。当前版本状态请使用 docs/architecture-status.md 和 docs/support-matrix.md。

## 1. 核心洞察

### 1.0 架构宪章与运营纪律

以下宪章不是口号。它们是 MABW 中每一个架构决策必须满足的设计约束。它们是从真实参考运行中观察到的失败模式中凝结而成的------当规则被托付给指令时崩溃的部分，以及当规则被确定为机器执行时坚守的部分。

#### 架构宪章

**1. 聪明的无权，有权的确定，生效的过人，过人的留痕。** LLM / agent 可以理解、建议、分解和起草------但不能直接产生效果。只有确定性控制面可以写入状态、推进阶段、冻结证据、通过门禁和签发事务。任何影响后续运行的变更必须经过人类确认并留下记录。

**2. 机器能管的，不交给记忆。** Schema、验证器、门禁、事务、事件日志和测试是可靠的。留在提示词、交接指南或口头约定中的规则会在真实运行中漂移。如果某条规则可以被确定性检查捕获，它就不应停留在指导层面。

**3. 同一个字段只许有一个写者。** 每个控制面字段必须有唯一权威写者。Python 写状态、账本、事件、哈希和门禁。LLM 写内容草稿。人类批准偏好与最终交付。多个模块\"善意地\"更新同一字段会破坏审计能力、回滚能力和归因能力。

**4. 有来源，不等于被支持；能追溯，不等于被证明。** 来源记录只证明一个声明*何时*、*经过哪个步骤*进入工作流。它并不自动证明来源在语义上支持每个子声明。检索计划、候选来源、模型摘要和搜索摘要属于发现线索，不是事实证据。支持必须按强度、来源层级和时效性------在独立的维度上------分别记录。新鲜不代表权威。链接不代表已被证明。

**5. 冻住的不许改；要变就新增，要坏就标脏。** 一旦被确定性控制面冻结，工件不能被静默覆写。合法的变更必须产生新版本、新工件、新事件，或显式的取代/撤销/污染记录。旧的冻结状态绝不能被改写以显得\"本来就正确\"。

**6. 冲突按层级，不按聪明。** 当用户请求、agent 建议、受众偏好、改进记忆、修复计划、门禁、schema 与合约之间发生冲突时，系统不会让模型来\"解释哪个更合理\"。它遵循预先声明的优先级。事实合约和确定性门禁优先于风格偏好。本次运行的修复优先于跨运行的品味记忆。控制面职责不能被提示词、交接指南或临时用户请求覆盖。

#### 运营纪律

**1. Product Spine：加速不偷问责。** MABW 可以通过复用冻结证据、减少重复推理、优化路由和并行化非依赖工作来加速。绝不能以减少账本、门禁、人类确认、事件、快照或归档来加速。轻量路径可以轻量化外壳，但绝不可移除骨干。

**2. Public Claims Discipline：不说 artifact 支撑不了的话。** MABW 的公开文档、README、发布说明、演示、论文草稿和社交媒体帖子不得声称超过当前工件能够证明的范围。未测量的就写 NOT MEASURED。可追溯就称为可追溯，不称为语义证明。人类评审发现的错误不能被重新包装为模型自我纠正。影响能力边界的失败案例应该成为公开系统证据的一部分。

**3. Data Boundary：私有事实不为公共机制背书。** MABW 可以从真实工作流中提炼模式、失败类型、控制面规则和测试形态。私有业务事实、客户材料、雇主数据、投资者关系内容和非公开信息绝不能进入仓库、fixture、公开演示或未经批准的外部 API。公共机制必须能用公开语料或合成材料复现。

### 1.1 为什么编程 Agent 进步如此之快

编程 agent（Claude Code、Codex、Cursor）的进步并不是因为模型突然变得聪明很多。它们进步是因为编程作为实践已经具备了一个**闭合的改进循环**：

  -----------------------------------------------------------------------------------
  **机制**                            **作用**
  ----------------------------------- -----------------------------------------------
  **测试套件**                        二元的通过/失败信号------代码是否正确毫无歧义

  **Git 历史**                        每次变更关联到一个提交、一个作者、一个原因

  **Bug → 提交可追溯性**              一个失败的测试可以追溯到导致问题的具体变更

  **CI/CD 流水线**                    变更在合入前自动验证

  **代码评审门禁**                    每次变更需要人类批准
  -----------------------------------------------------------------------------------

这五种机制构成一个完整的循环。模型提供原始能力；**基础设施提供改进信号。**

### 1.2 业务工作流缺乏什么

企业简报缺乏所有这些机制：

• 没有二元的正确性信号：一篇简报\"不太对\"，但没有人能指出哪项测试失败

• 没有可追溯性：简报中的一个过时市场数据无法追溯到是哪个来源检索步骤出错

• 没有累积记忆：一个初级分析师的错误被口头纠正后就遗忘了；下一个新人重复同样的错误

• 没有结构化反馈：\"这个部分感觉不对\"在会议结束后就蒸发了

结果：业务工作流只通过个人学习来改进------缓慢、不可传递、人走即失。

### 1.3 MABW 的核心论点

**让编程 agent 能够持续改进的那些基础设施，同样可以为企业简报构建------但这需要从一开始就为可审计性、可追溯性和结构化反馈而设计。**

MABW 不试图让一个 LLM 变得更聪明。它构建这五种机制：

  ----------------------------------------------------------------------------------------------------------
  **编程机制**                        **MABW 等价物**
  ----------------------------------- ----------------------------------------------------------------------
  测试套件                            工件验证 + 质量门禁（阶段范围，确定性）

  Git 历史                            event_log.jsonl------每次决策记录时间戳、操作者、原因

  Bug → 提交可追溯性                  artifact_registry.json------每个工具有 producer_stage、producer_role

  CI/CD 流水线                        Orchestrator 控制循环 + 阶段完成事务

  代码评审门禁                        request_human_review 决策 + RepairPlan 人类批准 + 改进账本
  ----------------------------------------------------------------------------------------------------------

**内容/控制解耦------决定性的实证发现。** 在 v0.7.1 参考运行中，一个 LLM Orchestrator 完成了完整的内容流水线（产出 8 个工件），同时系统地跳过了整个控制流水线：零次 decide 调用、没有运行门禁、workflow_state 停在 doctor、所有工件 not_checked。LLM 自己的诊断：*\"我把 Orchestrator 合约当成背景文档来读，而不是可执行的 API。我把控制循环简化成了阶段列表。\"*

这直接推动了事务架构的产生：将记账从指令层面迁移到执行层面。LLM 保留*做什么决策以及为什么*的权限；Python 处理*确定性记录该决策已做出*。如果一条规则真的重要，它就不能住在提示词里。它必须变成 schema、验证器、门禁、事务、事件日志或测试。

### 1.4 运行时接口收敛

在 2026 年 5--6 月，两个独立的研究项目从不同方向验证了 MABW 的核心论点。

**LIFE-HARNESS（Xu et al., 2026 年 5 月）。** 一个从训练轨迹演化而来的结构化运行时 harness，应用于冻结的 LLM agent，在 18 个 backbone 上改善了 126 个模型-环境设置中的 116 个，平均相对提升 88.5%。其论点------适配接口，而非适配模型------被 MABW（从运营简报需求出发）和 LIFE-HARNESS（从受控实验室实验出发）独立得出。它们的四层 harness（Environment Contracts、Procedural Skills、Action Realization、Trajectory Regulation）按生命周期拦截点组织；MABW 的四类合约范畴按治理域组织。这是互补的分解方式，不是结构上等价的映射。完整比较见 §10.1。

**Self-Harness（Zhang et al., 2026 年 6 月）。** 一个三阶段循环从执行轨迹中挖掘模型特定失败、生成最小 harness 修改、只接受回归测试通过的编辑。在三个模型族上，留出测试通过率分别从 40.5%→61.9%、23.8%→38.1%、42.9%→57.1% 提升。Self-Harness 针对确定性奖励域（Terminal-Bench）；MABW 针对开放域简报，奖励信号是人的判断。这是同一范式内互补的设置。完整比较见 §10.1。

## 2. 设计哲学

### 2.1 三层质量体系

MABW 的质量不是单一维度。对 v0.7.x 参考运行的实证分析区分了三层，每层的证据强度与天花板不同：

**第一层 --- Law（确定性门禁）。** 机器可检查的规则：来源支持引用、时效性窗口、数字与账本对齐、日期覆盖、建议安全用语、流程残留、脱敏风险。证据强度：**A**（SHA-256 记录、事件日志时间线、门禁报告状态序列）。结论：门禁是实时执行的------在真实运行中它们拦截了三次才通过。天花板：只能捕获规则可描述的错误，不能捕获分析质量。

**第二层 --- Honesty（交付格式）。** 最终输出是否干净、读者可读？没有裸露的内部 \[CL-XXXX\] ID、没有\"Analyst subagent\"之类的流程用语、没有空的引用表单元格。证据强度：**B**（已知差距------Flash 模型泄漏了内部 ID；v4 Pro 没有；相同指令，不同模型）。通过将裸 ID 检测加入读者最终门禁来修复。天花板：交付的整洁度，不是分析质量。

**第三层 --- Wisdom（内容分析）。** 简报的分析质量好不好？能否证明比单模型基线更好？当前状态：**NOT MEASURED**。因果归因无法进行，因为各运行的 claim-layer 工件不同（混淆实验）。这是 v0.8 受控实验必须填补的空白。

修复优先级：先稳定 Law 层（确实能拦截的门禁），再稳定 Honesty 层（确实能剥离内部工件的交付），然后在一个干净的基线上测量 Wisdom------因为在交付都不够干净时就做内容质量比较，会引入太多不可控变量。

### 2.2 三个维度：正确性、品味与证据

企业简报质量横跨三个正交维度：

  -------------------------------------------------------------------------------------------------------------------------------------------------------------
  **维度**          **治理内容**                                   **治理机制**                                                         **写者**
  ----------------- ---------------------------------------------- -------------------------------------------------------------------- -----------------------
  **正确性**        无事实错误、过时数据、归因错配、结构性违规     合约执行------在阶段边界经过 schema 验证、机械检查                   Python 门禁 + 事务

  **品味**          部门编辑偏好、文化规范、未明说期望             audience_profile.md + 改进账本------人类编辑、人类批准、每运行冻结   人类写；LLM 语义解释

  **证据**          来源到声明的绑定、支持强度、时效性、权威层级   Claim pipeline（草稿 → 冻结 → 账本）+ 来源附录                       LLM 起草；Python 冻结
  -------------------------------------------------------------------------------------------------------------------------------------------------------------

正确性可以被机制化。品味不能------它通过数月的反馈习得、因组织而异、必须保持人类可编辑。证据介于两者之间：LLM 可以发现和起草声明，但声明 ID、冻结和支持强度元数据是确定性控制面。v0.8.3 Claim Freeze Transaction 就是 agent 起草与系统在证据上的权威之间的架构边界。

### 2.3 治理域与控制面

MABW 的架构使用两个互补的概念：

**四类合约范畴定义治理域**------即被执行的边界属于哪种质量类型：

  -----------------------------------------------------------------------
  **范畴**                            **治理域**
  ----------------------------------- -----------------------------------
  Behavior                            Orchestrator 与专家角色边界

  Process / Artifact                  阶段就绪与预期工件类别

  Fact-Grounding / Evidence           实质性陈述对受支持声明的可追溯性

  Quality / Audience                  与读者语境对齐的交付决策
  -----------------------------------------------------------------------

**28 个操作控制面**在文件、写者、冻结/重置和事务层面实现这些治理域。合约范畴定义*治理什么*；控制面定义*谁写、何时冻结、如何验证、违规时发生什么*。它们是互补的，不是替代的。完整控制面清单见 docs/control-surfaces.md。

控制面按作用域组织：

  -------------------------------------------------------------------------------------------------------------------------------
  **作用域**               **数量**                **示例**
  ------------------------ ----------------------- ------------------------------------------------------------------------------
  运行范围流程控制         \~9                     runtime_manifest.json、workflow_state.json、event_log.jsonl、完成事务

  运行范围证据与正确性     \~10                    claim_ledger.json、阶段范围门禁报告、audit_report.json、feedback_issues.json

  工作空间范围品味与记忆   \~8                     audience_profile.md、improvement/ledger.jsonl、冻结快照

  仓库范围治理             \~8                     合约 YAML 配置、评测用例、支持矩阵、红线
  -------------------------------------------------------------------------------------------------------------------------------

### 2.4 单一写者原则

每个控制面字段有且只有一个权威写者。系统分为三个写入域：

• **Python** 写控制状态、账本、事件、哈希、门禁、事务和归档。它从不调用 LLM。

• **LLM 运行时**写内容草稿：候选声明、筛选候选项、声明草稿、审计简报章节、审计发现。

• **人类**写批准、受众指导、交付决策和显式运行请求。

多个模块\"善意地\"更新同一字段，是审计轨迹和回滚能力消亡的方式。claim_drafts.json 和 claim_ledger.json 是两个独立的工件、有独立的写者，正是出于这个原因：LLM 起草不带 ID 的声明；Python 分配 ID 并冻结账本。两个写者都不能触碰对方的工件。

### 2.5 速度原则

速度只能来自去除重复推理、并行化非依赖工作和复用哈希验证的冻结工件。速度绝不能来自更少的记录、更少的门禁、更少的批准、跳过的事务或更弱的归档/审计轨迹。

这一原则在快速重跑导入事务（v0.8.1，Experimental）中得到操作化：一个完整的冻结事实层可以被导入一个新工作空间、经过哈希验证、直接复用------跳过从来源发现到 Claim Ledger 的阶段，同时保留下游的 writer/auditor/gate/finalize-complete/human delivery 路径。速度来自复用，而非省略。

### 2.6 运营纪律

三条运营纪律（§1.0）转化为具体的设计约束：

• **Product Spine** → 快速重跑导入保留门禁执行。归档是强制性的；快速归档绝不能通过跳过归档来实现。

• **Public Claims Discipline** → 本报告中的每一声明都引用自 docs/architecture-status.md、docs/support-matrix.md、当前源代码或已发布的参考运行摘要。Planned 或 Deferred 的内容被显式标注。

• **Data Boundary** → 所有公开参考运行使用合成 fixture 或公开域材料（太阳能政策/市场数据）。类器官产业失败研究只发布不可逆摘要和失败分类，不发布原始工作空间数据。

## 3. 架构：五条骨干

架构不是组件的平坦列表。它是五条控制骨干，每条骨干有明确的写者、冻结点、验证点和失败轨迹。

### 3.1 运行时状态骨干

> runtime_manifest.json → workflow_state.json → artifact_registry.json → event_log.jsonl

**写者**：Python（运行时状态命令、阶段完成事务、交接准备）。 **冻结点**：每次运行在 output/intermediate/ 中产生完整的运行时状态快照。 **验证**：run_integrity 标记------单次运行标记 clean；当检测到重置、过时阶段重放或冻结工件修改时标记 contaminated。污染事件记录在事件日志中。 **v0.8.1 新增**：控制轨迹计时投影------status 和归档的运行清单暴露从事件日志派生的计时桶，报告未知、不完整或污染状态，不估算确切模型运行时间。

### 3.2 证据/声明骨干

> source evidence → durable source evidence → input classification\
> → candidate_claims.json → screened_candidates.json\
> → claim_drafts.json → freeze → claim_ledger.json\
> → audited_brief.md → audit_report.json → source_appendix.md

**写者**：LLM 运行时起草内容工件（候选项、筛选结果、声明草稿、审计简报、审计报告）。Python 在 claim_drafts → claim_ledger 边界验证和冻结。

**Claim Freeze Transaction（v0.8.3）** ------ 这条骨干的核心：

**1.** Agent 写 claim_drafts.json------不带 claim_id 字段的声明条目。任何层级携带 claim_id 的草稿在验证时被拒绝。

**2.** Python freeze-claim-ledger 读取已验证的草稿，分配确定性 CL-#### ID（相同冻结输入产生稳定 ID），写入权威 claim_ledger.json，在运行时状态中记录冻结元数据和哈希，发出 claim_ledger_frozen 事件。

**3.** stage-complete \--stage claim-ledger 要求匹配的冻结记录。哈希漂移、缺少冻结元数据、无效草稿或账本字节陈旧都将失败关闭。

**4.** Analyst 和 Auditor 读取冻结的 claim_ledger.json。它们不读 claim_drafts.json，也不得编辑账本。

**为什么这很重要**：LLM 可以做语义声明工作，但它们不铸造控制 ID 也不冻结账本。在 v0.8.3 之前，一个 Claim Ledger agent 可以发明 CL-0001 到 CL-0020 并直接写入 claim_ledger.json------使得在不经人工检查的情况下无法区分冻结账本与半成品草稿。

### 3.3 门禁骨干

> CompositeAuditAgent\
> ├── DeterministicAuditAgent （来源支持、时效性、数字、日期、建议安全、流程残留、脱敏）\
> ├── QualityHarnessAuditAgent （实质性事实、时效性、目标相关性门禁）\
> └── NoOpSemanticAuditAgent （占位；v0.8.3 Auditor 角色合约要求运行时 auditor 检查 support calibration；未交付基于模型的语义审计）\
> → gates/auditor_quality_gate_report.json\
> → gates/finalize_quality_gate_report.json

**写者**：确定性和 harness agent 是 Python------无 LLM 调用。语义审计槽（NoOpSemanticAuditAgent）是占位符；v0.8.3 Auditor 角色合约要求运行时 auditor 检查 support calibration，但没有基于模型的语义审计器交付。 **阶段范围**：gates/auditor_quality_gate_report.json 阻止 auditor 完成。gates/finalize_quality_gate_report.json 阻止 finalize 完成。旧版 quality_gate_report.json 是最新/兼容投影，不是冻结权威。 **v0.8.3 新增**：Auditor support calibration------对冻结账本进行夸大陈述、支持强度校准、置信度错配、证据关系错配和局限性泄漏的显式检查。

### 3.4 记忆/改进骨干

> audience_profile.md → output/intermediate/audience_profile_snapshot.md （每运行冻结）\
> improvement/ledger.jsonl → improvement/memory.md → output/intermediate/improvement_memory_snapshot.md （每运行冻结）

**写者**：人类写 audience_profile.md 并批准指导进入 improvement/ledger.jsonl。Python 从账本投射 improvement/memory.md、冻结每运行快照、在 runtime_manifest.json.improvement 中记录 materialized_entry_ids 和 SHA-256 哈希。 **冻结规则**：运行中账本变更（批准/撤销）不影响当前运行。物化在下一次运行开始时发生。 **SHA-256 链**：每个修订通过 previous_revision_sha256 链接到前一修订。清单记录 ledger_sha256、memory_sha256、snapshot_sha256 和 materialized_entry_ids------确定性记录哪些指导在运行中是生效的。 **延期控制面**：improvement/intake.jsonl（原始反馈接收）和 improvement/candidates.jsonl（候选暂存区）延迟到未来版本。它们不影响当前运行。

### 3.5 交付/归档骨干

> output/delivery/brief.md + output/delivery/\<named\>.docx\
> → output/source_appendix.md （审计/控制副本）\
> → output/runs/\<run_id\>/ （不可变归档）\
> → output/intermediate/finalize_report.json

**写者**：Python finalize 从 audited_brief.md 生成读者交付包，剥离 \[src:\<claim_id\>\] 标记，将来源附录附加在交付文件内部，渲染 DOCX。source_appendix.md 是审计/控制副本，不是独立的读者交接文件。 **归档**：已完成的运行归档至 output/runs/\<run_id\>/，包含交付、中间工件、控制记录和 SHA-256 清单条目。归档不可变------最新的 output/ 表面可以前进而不擦除前一运行的证据链。 **快速重跑导入（v0.8.1）**：state import-fact-layer 将完整的归档事实层复制到新工作空间，验证哈希，在运行时状态中记录导入，将上游事实层阶段标记为已通过导入满足。run \--recipe fast-rerun 从 Analyst 开始，使用导入的事实层；finalize-complete 对照目标工作空间重新检查来源时效性。

## 4. 控制事务

### 4.1 阶段完成事务

stage complete 和 finalize complete 是内容/控制解耦失败问题的架构答案。它们将阶段完成记账从提示词层面指令迁移到确定性执行：

• 验证预期工件在 artifact_registry.json 中存在且有效

• 更新 workflow_state.json 的阶段状态和转换

• 向 event_log.jsonl 追加 stage_completed / finalize_completed 事件

• 阶段特定条件的门禁（例如 claim-ledger 阶段需 freeze-claim-ledger）

LLM Orchestrator 决定*做什么以及为什么*；Python 记录*该决策已做出，附带什么工件，在什么条件下。*

### 4.2 Claim Ledger Freeze Transaction（v0.8.3）

Claim Freeze Transaction 是 v0.8.3 的定义性架构新增。它在 agent 起草声明与系统治理声明身份之间建立了硬边界：

  --------------------------------------------------------------------------------------------------------------
  **操作**                **写者**                   **工件**
  ----------------------- -------------------------- -----------------------------------------------------------
  从来源起草声明          LLM（Claim Ledger 角色）   claim_drafts.json（无 claim_id 字段）

  验证草稿                Python                     拒绝任何层级的 claim_id

  分配确定性 ID           Python                     CL-####（排序-顺序，相同输入稳定）

  冻结权威账本            Python                     claim_ledger.json + 冻结元数据 + claim_ledger_frozen 事件

  执行完成                Python                     stage-complete \--stage claim-ledger 无匹配冻结记录则失败
  --------------------------------------------------------------------------------------------------------------

冻结后，Analyst 和 Auditor 角色只读冻结的 claim_ledger.json。它们不能读 claim_drafts.json，也不得编辑账本。

### 4.3 运行完整性污染

workflow_state.json.run_integrity 记录一次运行是否保持干净的单次参考证据。当以下情况发生时运行被标记 contaminated：

### - 阶段执行后运行状态被重置

### - 在过时状态上重放阶段

• 冻结后工件被修改

每次污染写入一个 run_integrity_contaminated 事件，附带检测到的原因。被污染的运行被排除在 A 级受控实验之外；它们仍可产生有效交付，但不能作为参考证据。

### 4.4 不可变归档

已完成的运行归档至 output/runs/\<run_id\>/，包含：

• 交付工件（brief.md、.docx）

• 中间工件（claim ledger、门禁报告、审计报告）

• 控制记录（workflow_state.json、event_log.jsonl、runtime_manifest.json）

• SHA-256 清单条目

归档不可变------最新的 output/ 表面向前推进，不擦除证据链。

### 4.5 快速重跑导入

multi-agent-brief state import-fact-layer 将完整的归档事实层（持久来源证据、输入分类、候选声明、筛选声明、claim ledger）复制到新工作空间。它复制字节、验证哈希、记录导入、标记上游事实层阶段为已满足。run \--recipe fast-rerun 从 Analyst 开始；finalize-complete 对照目标工作空间重新检查导入来源的时效性。速度来自哈希验证的复用，而非省略。

## 5. 证据与声明治理

### 5.1 来源到声明流水线

证据流水线分六个阶段推进：

> Source Discovery → Durable Source Evidence → Input Classification\
> → Scout（candidate_claims.json）\
> → Screener（screened_candidates.json）\
> → Claim Ledger（claim_drafts.json → freeze → claim_ledger.json）

只有持久来源文件和受支持的来源配置条目才算作证据。source_candidates.yaml 仅用于规划/评审------它不能满足来源发现完成要求，也不能合并到 sources.yaml。跨度级原始摘录、retrieved_at、source_tier 和摘录哈希是 v0.9 的证据充分性方向；当前来源记录要求 source_id、source_name、source_type、title 和 content，附加字段为可选或放在元数据中。

### 5.2 声明草稿合约

claim_drafts.json 是一个实验性的冻结输入工件。草稿条目在顶层或元数据中不得包含 claim_id 字段。验证器拒绝任何携带 claim ID 的草稿------ID 属于系统，不属于 agent。

冻结事务使用 sorted_sequential_v1 分配确定性 CL-#### ID：声明按稳定键排序，分配顺序 ID。相同冻结输入产生相同 ID。这并不承诺在草稿被增量编辑、添加或在冻结之间删除时现有 ID 保持稳定。

### 5.3 支持度校准与失败分类

v0.7.4 类器官产业失败研究（见 §9.2）暴露了对支持强度校准的需求。一位外部评审者（ChatGPT 5.5 Thinking Max）识别出五类失败：

**1. 支持强度膨胀**：FDA 用语被夸大为类器官监管认可

**2. 来源权威膨胀**：会议新闻变成国家计划事实

**3. 声明混同**：有效的主事实与未验证的子声明合并

**4. 归因错配**：一个来源承载了过多的子结论

**5. 未经验证的预测认证**：二级市场预测作为核心证据使用

这些失败不是缺少来源。它们是校准失败------来源存在，但不支持最终文本中的每一个限定词、确定度或子声明。v0.8.3 的 Auditor support calibration 检查（夸大陈述、支持强度错配、置信度错配、证据关系错配）是对这一分类的首次控制面回应。分级支持关系（explicitly_supported、raw_supported、summary_supported、directionally_supported、partially_supported、supportive_but_overextended、attribution_mismatch、needs_primary_source、unsupported）仍属于 v0.9 设计工作。

### 5.4 来源附录作为追溯

output/source_appendix.md 在 finalize 期间从引用的 Claim Ledger 来源生成。它被附加在交付文件内部（output/delivery/brief.md + DOCX），并保留为独立的审计/控制副本。它为读者提供质疑声明的入口------不是事实正确性的证书。可追溯，而非语义证明。

## 6. 门禁与修复

### 6.1 阶段范围质量门禁

v0.7.5 引入了阶段范围的门禁报告：

  ------------------------------------------------------------------------------------------------------
  **门禁报告**                              **阻止**                **内容**
  ----------------------------------------- ----------------------- ------------------------------------
  gates/auditor_quality_gate_report.json    Auditor 阶段完成        实质性事实、时效性、目标相关性发现

  gates/finalize_quality_gate_report.json   Finalize 完成           读者最终检查、裸 ID 泄漏、流程残留
  ------------------------------------------------------------------------------------------------------

旧版 quality_gate_report.json 保留为最新/兼容投影。阶段范围报告是冻结权威。门禁可以阻止，但不能被强制跳过------完成事务路径中没有 \--force 标志。

### 6.2 确定性审计栈

> auditor subagent\
> → CompositeAuditAgent\
> → DeterministicAuditAgent （来源支持、时效性、数字、日期、建议安全、流程残留、脱敏）\
> → QualityHarnessAuditAgent （实质性事实、时效性、目标相关性、裸 ID 泄漏）\
> → NoOpSemanticAuditAgent （占位；auditor 角色合约要求运行时 auditor 检查 support calibration；未交付基于模型的语义审计）\
> → audit_report.json

确定性和 harness agent 是 Python------无 LLM 调用。语义审计槽（NoOpSemanticAuditAgent）是占位符；v0.8.3 Auditor 角色合约要求运行时 auditor 检查 support calibration。没有基于模型的语义审计器交付。语义审计发现（未来启用时）不能覆盖确定性发现，也不能凭自身权威阻止发布资格。

### 6.3 修复路由器

multi-agent-brief repair route 是一个确定性只读命令。它将已知的门禁、审计、注册表和工作流发现映射到所属阶段和允许的工件类别。它不创建修复计划、不修改内容、不调用 agent。修复路由是诊断工具------它告诉 Orchestrator *应该把修复指向哪里*，而不是*修复应该说什么*。

### 6.4 反古德哈特门禁设计规则

*Precision Is Not Faithfulness* 论文（Santillana, 2026 年 6 月）实证证明了古德哈特定律作用于评估：最精确的前沿模型（精度 0.89）只覆盖了 0.46 的相关事实，按 F1 排名垫底。对 MABW 的门禁设计而言，这意味着：

> 每一个阻塞型精度门禁在部署前必须回答：一个优化 agent 的最便宜通过策略是什么。如果最便宜策略是删除内容，该门禁必须配对一个机械的覆盖率侧检查。

这是门禁设计规则，不是观察。生产中已有部分缓解措施（人类触发的 finalize 门禁会捕获严重过薄的简报），但 agent 循环内部的机械配对是必需的。

### 6.5 覆盖率门禁（Planned v0.8.x/v0.9）

覆盖率门禁比较 screened_candidates.json 中的候选 ID 与审计简报中的引用参考文献。它检测筛选后的静默丢失------通过了筛选但被 analyst 起草或 editor 精炼时丢弃的事实。它不保证 screener 召回了所有相关事实（screener 召回率仍是开放 NLP 问题）。它捕获的是 DRA 24% 回归数据显示在 analyst/editor 改写中高频发生的那条丢失路径。

## 7. 受控记忆

### 7.1 受众画像

audience_profile.md 是一个纯文本、人类可编辑的工作空间文件。它携带编辑偏好、结构惯例、部门词汇和累积反馈模式。每次运行将当前画像冻结到 output/intermediate/audience_profile_snapshot.md。Orchestrator 读取快照并为专家角色交接生成语义品味摘要。运行时对实时画像的编辑仅适用于后续运行。画像没有 schema 执行------它是语义解释的，而非机械验证的。

### 7.2 改进账本

improvement/ledger.jsonl 是一个追加写入、工作空间本地、修订链式、人类门禁的审计账本，用于读者偏好指导。生命周期：

> propose（人类或 agent → proposed 状态）\
> → approve（人类 → approved 状态，SHA-256 修订链接）\
> → rebuild（Python → improvement/memory.md 投射）\
> → 下一次运行开始 → improvement_memory_snapshot.md（每运行冻结）\
> → revert（人类 → reverted；从下一次记忆投射中移除）

**关键不变量**：

• propose 记录一个偏好。不影响任何运行。

• approve 追加状态记录。不影响*当前*运行。

• 物化在*下一次*运行开始时发生，产生一个清单引用哈希的冻结快照。

• 被撤销的条目从下一次 improvement/memory.md 投射和下一次运行快照中移除。

• 运行清单的 materialized_entry_ids 是哪些指导生效的确定性记录。

### 7.3 指导物化测量（Planned v0.8.5）

080 实验框架（v0.8.1 注册；v0.8.5 计划评测）将在冻结的事实层上测量两个指标：

• **指导物化率**：获批账本条目的可观察反映到运行输出中的比例

• **指导回归率**：先前物化的条目在后续运行后不再反映的比例

这些指标仅为观察性------它们不阻止 finalize，也不写账本状态。guidance_manifestation_report.json 表面是计划中的 v0.8。

### 7.4 延期表面

以下表面出现在控制面分类中，但当前版本未实现：

  ----------------------------------------------------------------------------
  **表面**                            **状态**
  ----------------------------------- ----------------------------------------
  improvement/intake.jsonl            Deferred------含派生链接的原始反馈接收

  improvement/candidates.jsonl        Deferred------偏好/规则候选暂存区

  reference_samples/manifest.jsonl    Planned v0.8------品味证据的已接受样本
  ----------------------------------------------------------------------------

它们被延迟以允许核心账本生命周期（propose → approve → materialize → snapshot → manifest → revert）在加入接收路由和候选提升路径之前先稳定下来。

## 8. 实现基线 v0.8.3

### 8.1 版本演进

  -------------------------------------------------------------------------------------------------------------------
  **版本**                **主题**                   **核心声明完整性**
  ----------------------- -------------------------- ----------------------------------------------------------------
  v0.7.4                  流程问责硬化               过时审计阻止 finalize；交付语义更清晰；公开失败研究

  v0.7.5                  运行时完整性与修复路由     阶段范围门禁；run_integrity 标记；不可变归档；修复路由器

  v0.8.1                  快速重跑与实验注册         事实层归档；哈希验证导入；计时投影；080 注册

  v0.8.3                  Claim Freeze Transaction   claim_drafts → 确定性 CL-#### → Python 持有冻结 → 阶段完成门禁
  -------------------------------------------------------------------------------------------------------------------

### 8.2 当前能力状态

**Supported（已实现、已测试、CI 保护）：**

• 跨 Claude Code（一线写者路径）、Hermes、OpenCode、Manual 的子 agent 优先工作流

• 运行时状态控制骨干：workflow_state.json、artifact_registry.json、event_log.jsonl、runtime_manifest.json

• 阶段完成事务（stage complete、finalize complete）

• Claim Freeze Transaction（claim_drafts.json → claim_ledger.json）

• 阶段范围质量门禁报告（auditor + finalize）

• 确定性审计栈（CompositeAuditAgent --- DeterministicAuditAgent + QualityHarnessAuditAgent）

• 修复路由器（repair route）

• 运行完整性污染追踪

• 带 SHA-256 清单的不可变完成运行归档

• 带 SHA-256 链式哈希的改进账本（propose/approve/revert 生命周期）

• 带每运行冻结快照的受众画像

• 溯源投影

• 公开安全评测用例（11+ 打包 fixture）

• 读者交付包（output/delivery/brief.md + DOCX + 来源附录）

• 输入治理（四类别分类）

• 1500+ 收集的确定性测试（CI 中零 LLM 调用）

**Experimental / Limited：**

• MinerU 文档解析与输入提取

• 快速重跑导入事务（state import-fact-layer + run \--recipe fast-rerun）；从 Analyst 开始；writer/auditor/gates/finalize-complete/human delivery/archive 保持为下游循环

• MABW-080 实验用例验证与运行注册（验证归档字节；不评分、不总结、不证明质量）

• Codex 自定义 agent 运行时

• PDF 输出

• 飞书交付

• 本地信号发现

**Planned / Deferred：**

• 覆盖率门禁（v0.8.x/v0.9）

• 指导物化/回归测量（v0.8.5）

• 改进接收/候选流水线（v0.7.3+）

• 参考样本清单（v0.8）

• 轨迹调控------代码级重试计数与决策收窄（v0.8）

• 分级支持关系（v0.9）

• 原子声明图（v0.9.0）

• 证据跨度注册表（v0.9.1）

• 声明-支持矩阵（v0.9.2）

### 8.3 v1.0 冻结候选

控制面分类（docs/control-surfaces.md）标识了有资格获得 v1.0 向后兼容性承诺的表面。关键前提：完成事务语义必须稳定；角色拓扑必须定型；改进账本 schema 必须稳定；读者最终门禁和覆盖率侧门禁必须定案。太年轻而无法冻结的表面（intake、candidates、manifestation report、reference samples）被显式延迟到 v1.0 之后。

## 9. 参考证据与失败研究

### 9.1 v0.7.2 公开太阳能集成运行（等级：B+）

一次两轮公开太阳能简报运行证明：

• 改进账本可以将已批准的指导物化为冻结的每运行快照

• 质量门禁主动阻止了运行（3 次），并在修复后通过------门禁不是装饰

• Orchestrator 可以在真实运行中使用冻结的改进记忆快照

• materialized_entry_ids: \[\"AG-0001\"\] 出现在运行时清单中，带有 SHA-256 验证

**此运行不能证明的**：输出质量改善、跨模型稳定物化、严格因果归因。各运行之间的 claim-layer 工件不同（不同的 candidate_claims、screened_candidates 和 claim_ledger 哈希），使得指导成为混淆变量而非受控变量。等级 B+------集成参考，而非 A 级因果实验。

### 9.2 v0.7.4 类器官产业失败研究

一次私有的类器官产业研究运行由 ChatGPT 5.5 Thinking Max 进行外部评审。该运行产出了一份具有完整流程表面的可读简报，但外部评审者发现了五类失败（见 §5.3）。该简报不是一个输出质量展示，但它是 MABW 核心声明的一个有用的失败研究：

> 在直接 LLM 起草中，错误通常只留在最终文本中。在 MABW 中，错误留下一条传播路径。

每个错误都可以追溯路径：来源摘要 → 候选声明 → 筛选候选项 → Claim Ledger → 审计简报 → 最终交付。问题没有消失在输出中------中间工件保留下来可供审查。

### 9.3 内容/控制解耦（v0.7.1）

一个 LLM Orchestrator 完成了完整的内容流水线，同时跳过了整个控制流水线。这是事务架构背后的决定性实证发现------它直接催生了 stage complete、finalize complete 和 Claim Freeze Transaction。指令不等于执行保证。如果一条规则重要，它必须是 schema、验证器、门禁、事务、事件日志或测试。

### 9.4 这些证据证明了什么，没有证明什么

  -------------------------------------------------------------------------------------------
  **证据**                **证明**                               **未证明**
  ----------------------- -------------------------------------- ----------------------------
  Solar B+                门禁执行是有效的；改进记忆链路闭合     输出质量改善；因果指导归因

  Organoid 失败           错误传播路径被保留；失败分类可被归类   MABW 输出优于单模型基线

  内容/控制解耦           LLM 是不可靠的低层事务执行器           该问题是模型特定的
  -------------------------------------------------------------------------------------------

## 10. 相关工作

MABW 处于若干研究线索的交汇点，按架构相关性组织。本节替代 v0.1.3 独立的相关工作章节；完整引用表（18 条）见该文件。

### 10.1 Harness 适配

**LIFE-HARNESS（Xu et al., 2026 年 5 月）**和**Self-Harness（Zhang et al., 2026 年 6 月）**独立验证了核心论点------适配接口，而非适配模型。LIFE-HARNESS 在具有二元奖励信号的确定性 agent benchmark 上运行；Self-Harness 以自动化 harness 演化针对确定性奖励域。MABW 研究更难的开放域简报设置，其中奖励信号是人类判断，需要结构化人类反馈和人类门禁的批准路径。轨迹调控差距（LIFE-HARNESS 中承重最大的层）在 MABW 中仍是 v0.8 工作。

### 10.2 反馈下的多轮改进

**DRA Multi-Turn（Sabharwal et al., ICML 2026）**发现自我反思产生的净改进可以忽略不计，而流程级反馈产生显著的单轮提升------但提升不会复合，因为 agent 在改写时会回归高达 24% 先前满足的条件。这直接验证了 MABW 的每阶段定向修复、冻结的每运行快照，以及使用确定性外部门禁而非模型自我批评的决策。MABW 的 v0.8 协议定义了其自身的指标------指导物化率和指导回归率------因为 MABW 的机制（持久冻结上下文）与 DRA 的一次性修订指令不同。

### 10.3 可审计人机协作协议

**CHAP（Shahid et al., 2026 年 6 月）**定义了一个结构化的多人类、多智能体协作开放协议。其核心抽象（workspaces、tasks、artefacts、append-only evidence log）在结构上映射到 MABW 的控制面。CHAP 是一个协议规范；MABW 是一个面向企业简报的工作流引擎。CHAP 标准化了 agent 间通信层；MABW 实现了工作流内治理层。

### 10.4 评估方法论

**Precision Is Not Faithfulness（Santillana, 2026 年 6 月）**证明了无参考忠实度指标只测量精度并奖励放弃回答。这为 MABW 的覆盖率侧门禁补充和反古德哈特门禁设计原则（§6.4）提供了理论基础。

**ResearchLoop（2025--2026）**是一个独立的证据门禁研究工作流线索。ResearchLoop 和 MABW 收敛于相同的结构需求：一个以证据充分性为门禁的研究循环不能信任 LLM 来执行自己的门禁。v0.8.3 Claim Freeze Transaction 是 MABW 的直接回应：claim_drafts.json（LLM 起草，无 ID）→ 确定性冻结 → claim_ledger.json（Python 持有）。研究循环的证据门禁有一条非 LLM 的、确定性的执行路径。

### 10.5 多智能体框架与工作流系统

MABW 通过其合约治理、文件状态黑板协调模型与基于对话的多智能体框架（AutoGen、CAMEL、MetaGPT）区分开来。它在 LLM agent 语境中实例化了工作流模式（van der Aalst et al.）和黑板架构（Nii）。

### 10.6 记忆与偏好系统

MABW 的受众画像借鉴了 Hermes 的 USER.md 表面模式，但治理基础设施------带 SHA-256 链式哈希的改进账本、approve/materialize 分离、每运行冻结以及清单引用的 applied_entry_ids------是专为 MABW 需求构建的。五条架构边界阻止了直接采用 Hermes 记忆：无工作空间本地可审计账本、无原生品味/正确性/证据分离、无运行级别冻结、无物化证据、无稳定执行保证。完整五边界分析见 v0.1.3 相关工作。

### 10.7 产业实践证据：企业级 Agent Harness、可验证工作流与治理控制面

> **关于本节引用。** 本节引用的 Claude/Anthropic/OpenAI 官方文献属于产业实践证据（practitioner evidence / official case study），不构成学术同行评审证明。它们用于支撑以下论点：企业 AI 的前沿实践正在从对话式交互走向可交付、可审计、可治理的 agent 工作流。MABW 的设计与这一趋势一致，但本节不声称这些文献"证明"MABW 的正确性。

#### 10.7.1 从对话式 AI 到可交付的知识工作 Agent

Anthropic 在 2026 年将 Claude Cowork 定位为"knowledge work agent"：它可以读写本地文件、跨 Slack 和 Google Drive 等连接应用工作、执行多步骤任务，并生成带有实际文件和消息引用的可交付成果（deliverables）[A2]。常见工作流包括 research briefs、meeting prep、recurring reports，输出形式涵盖文档、演示文稿、电子表格和邮件 [A2]。

这一转变的核心不是模型能力的提升，而是任务形态的改变。Anthropic 的最佳实践文档明确区分了 chat 和 Cowork 的适用场景：chat 适用于问答、解释和头脑风暴；Cowork 适用于多输入、多步骤、跨应用、产出文件的任务委派 [A3]。其判断标准有五个维度：多个输入源、产出文件型交付物、重复发生、用户知道什么叫好、中间过程无聊 [A3]。

> "Chat is for when the output is a thought in your head, and Claude Cowork is for when the output is something you'll hand to someone else." [A3]

MABW 的目标交付物——weekly business briefing、management memo、policy tracker、board memo、IR note——恰好符合这一任务形态：多来源输入、结构化输出、重复运行、有质量标准、中间过程（来源检索、声明提取、证据校对）适合委派。

#### 10.7.2 企业分析作为上下文与验证问题

Anthropic 在其内部数据分析 agent 的工程实践中得出了一个关键判断：agentic business analytics 的准确性主要是**上下文与验证问题**（context and verification problem），而非单纯的代码生成问题 [A4]。三类主要错误被识别为：

1. **概念/实体歧义**（concept/entity ambiguity）：agent 无法选择正确字段回答用户问题
2. **数据过时**（data staleness）：数据源、业务定义和 schema 持续变化，agent 知识过期
3. **检索失败**（retrieval failure）：正确信息存在于搜索空间中但未被定位 [A4]

Anthropic 的解决路径是：canonical datasets（权威数据集）、sources of truth（四层真相源：语义层、血缘图、查询语料、业务上下文）、freshness/validation（将 skill markdown 与 transformation model 同置，CI hooks 标记无 skill 更新的 schema 变更）、fixed offline eval set（固定评估集，锚定到快照日期或稳定事实表）、ablation（每次只改一个组件比较 pass rate）[A4]。

> "Analytics accuracy is a context and verification problem, not a code generation issue." [A4]

在金融服务领域，Kepler 的实践将这一判断推向了更严格的边界。Kepler 明确将模型放在 pipeline 的一个阶段，而非整个系统："In finance, the model can't be the whole system. We treat it as one stage in a pipeline." [A5]。其架构将确定性基础设施作为信任与验证层，Claude 作为推理与解释层 [A5]。每个数字都需要能追溯到具体 filing、page 和 line item [A5]。技术实现包括：结构化领域知识与硬边界、专有本体（ontology）、确定性执行环境、阶段级评估、完整审计日志和端到端溯源 [A5]。

> "Prompt engineering optimizes a call while content engineering optimizes the system around it." [A5]

MABW v0.9 将同样的分离原则应用到 enterprise briefing 领域：LLM 起草和评估；Python 冻结、验证、通过门禁、记录和投影发布资格。概念定义变成 claim roles；新鲜度变成 source metadata；语义层变成 evidence span registry 和 claim-support matrix；固定评估变成 same-evidence rerun / semantic regression。

#### 10.7.3 从 Prompt 到 Harness

Claude Code 在大型代码库中的部署经验揭示了一个关键事实：成功的企业 agent 部署依赖于配置、工具化、所有权、评审流程和治理，而不仅仅是模型能力 [A6]。

Anthropic 识别出五个扩展点构成 harness 层：CLAUDE.md（项目约定）、hooks（自动化一致行为）、skills（领域专业知识打包）、plugins（将配置分发到组织层）、MCP servers（工具和数据连接），以及两个补充能力：LSP integrations 和 subagents（分离探索与编辑）[A6]。

企业部署需要 DRI（Directly Responsible Individual）来维护 settings、permissions policy、plugin marketplace、CLAUDE.md conventions；没有集中化的所有权，"knowledge will stay tribal and adoption will plateau" [A6]。受监管行业需要 approved skills、required code review processes、limited initial access [A6]。

> "Successful enterprise agent deployments depend on configuration, tooling, ownership, review processes, and governance—not only model capability." [A6]

Managed Agents 的实践进一步确认了这一判断。从原型到生产的差距在于基础设施：安全（凭证隔离、prompt injection 防护）、状态管理（持久会话、断点续传）、权限控制（vaults、signed request tokens）、harness 调优（随模型演进适配）[B2]。

> "Infrastructure is what separates a prototype from a production agent." [B2]

Agent Skills 的设计体现了 progressive disclosure 原则：启动时只加载 skill 名称和描述，相关时读取完整 SKILL.md，需要时才访问子文件------保持上下文使用效率 [B3]。但 Skills 也引入安全风险：恶意 skill 可能引入漏洞或引导 agent 泄露数据 [B3]。

MABW 是一个 briefing harness：contracts、control surfaces、gates、frozen artifacts、event logs 和 human approval 扮演的角色，与软件工作流中的 tests、hooks、plugins 和 code review 相同。

#### 10.7.4 多智能体系统：适用场景与协调成本

Anthropic 对多智能体系统持审慎态度。其工程文章明确指出，多 agent 只在三种约束下稳定优于单 agent：

1. **上下文污染**（context pollution）：一个子任务的信息对后续子任务无关或有害，subagent 提供干净的隔离上下文
2. **可并行化任务**（parallelizable tasks）：多个 agent 并行探索更大搜索空间
3. **专业化**（specialization）：专注的 toolset 匹配特定职责，避免单 agent 拥有 20+ 工具时的性能退化 [A9]

> "Outside these situations, the coordination costs typically exceed the benefits." [A9]

Anthropic 观察到团队投入数月构建精心设计的多 agent 架构，"only to discover that improved prompting on a single agent achieved equivalent results" [A9]。每个额外的 agent 代表更多故障点、更多需要维护的 prompt、更多意外行为来源。token 成本通常是单 agent 的 3-10 倍 [A9]。

在协调模式层面，generator-verifier 模式只有在评估标准明确时才有效 [A10]。Anthropic 警告：

> "A verifier told only to check whether output is good, with no further criteria, will rubber-stamp." [A10]

团队最常见的失败方式是"implementing the loop without defining what verification means"，这"creates the illusion of quality control without the substance" [A10]。

MABW 的核心不是"多 agent"，而是 contract-backed workflow；多 agent 只是角色隔离和阶段治理的一种执行形态。更重要的是，MABW v0.9 不让 semantic assessor 直接成为真相裁判，而是让它提出 support rows、uncertainty、disagreement，再进入 schema、policy、adjudication 和 release eligibility------这正是 Anthropic 所说的"explicit verification criteria"在 briefing 领域的实现。

#### 10.7.5 从工作流自动化到企业治理

当 agent 接入企业数据和工具时，治理需求从质量控制扩展到安全控制。Anthropic 在 Zero Trust 框架中识别了 agentic 系统的独特风险面：tool access、autonomous decision-making、context persistence、multi-agent coordination，以及当前威胁态势中的 prompt injection、tool poisoning、identity/privilege abuse、memory poisoning、supply chain attacks [A7]。

> "Frontier AI models are compressing the timeline between vulnerability and exploit from months to hours." [A7]

传统访问控制无法阻止 agent 滥用合法权限 [A7]。Anthropic 提出的 Zero Trust 原则是："trust nothing, verify everything, and assume breach has already occurred" [A7]。架构要求包括密码学根身份、任务范围权限、受保护内存和沙箱 [A7]。

在连接器层面，企业 agent 需要集中化的身份、授权和撤销表面 [A8]。MCP 标准化了 agent 如何连接工具和数据，Vaults 处理 OAuth token 管理 [B6]。Skills 和 MCP 互补：MCP 提供工具和数据，Skills 教 agent 如何使用工具完成真实工作 [B6]。

企业管理层需要 SSO、SCIM、审计日志、Compliance API、权限管理、数据留存和使用分析 [B5]。

MABW 的 improvement ledger、human approval、frozen snapshots、event log、delivery gate、connector governance 不仅应被理解为质量控制，也应被理解为企业安全控制。

#### 10.7.6 生产追踪作为自我改进的基底

上述 Claude/Anthropic 文献描述了企业 agent 的任务形态、验证需求、harness 架构和治理框架。但它们没有回答一个关键问题：**agent 系统如何从自己的失败中学习？**

OpenAI 在 2026 年 5 月与 Thrive Holdings 和 Crete 会计师网络共同发布的 Tax AI 案例 [A+1]，提供了目前最完整的生产级回答。Crete 网络包含 30 多家会计师事务所，Tax AI 在报税季处理了 7,000 份报税表，节省约三分之一准备时间，起草准确率最高 97%，吞吐量提升约 50% [A+1]。

但这些数字不是最重要的。最重要的是 OpenAI 围绕三个支柱设计的改进闭环：

1. **专家从业者反馈**（expert practitioner feedback）：会计师在实际使用中修正 agent 的输出
2. **生产追踪**（production tracing）：不只保存输入输出，而是保存从源材料、字段提取及其引用、下游提交，到专家修正的完整路径
3. **Codex 驱动的定制评测迭代**（Codex-driven custom eval iteration）：反复出现的问题被归并成评测目标，再交给 Codex 作为有边界的工程任务 [A+1]

> **自我改进不是 agent 自己反思自己，而是生产系统把失败变成可验证的工程任务。**

OpenAI 明确指出，并非每个从业者修正都会自动变成 Codex 任务。修正可能代表提取遗漏、映射问题、产品不支持、税务判断，或者工作流噪声；只有反复差异经过审查并归并成可执行发现后，才会转成有边界、成功条件明确的任务 [A+1]。

Codex 不是拿一个模糊报错去修改代码，而是拿到一个结构化的任务环境：repo、task.yaml、EXEC_PLAN.md、RESULTS.md、相关产品代码、eval datasets、eval suites、graders、skills、docs、只读 production trace 和 source artifacts [A+1]。

**Tax AI 与 MABW 的结构映射**：

| Tax AI | MABW 对应物 |
|--------|------------|
| 源文件（source documents） | source pack |
| 字段提取（field extraction） | atomic claim extraction |
| 字段引用（field citations） | evidence span |
| 税务引擎映射（tax engine mapping） | claim-support matrix |
| 从业者修正（practitioner correction） | human adjudication / feedback issue |
| 字段级 review row | support row / finding |
| 重复修正模式（recurring correction pattern） | eval target |
| Codex 修复 PR | scoped MABW workflow repair |
| 回归评测（regression eval） | semantic regression / same-evidence rerun |
| 已提交税表（filed return） | delivered management brief |

MABW 不应该宣传"自进化 agent"。应该说：领导修改、审计发现、引用错配、支持不足，会被转成结构化 finding；只有可复现、可归类、可测试的问题，才进入改进队列。这与 MABW v0.9 roadmap 的纪律完全一致：v0.9 不是 semantic scoring release，而是从 traceability 到 support sufficiency；semantic model 可以评估、质疑、提出标签，但不能直接决定 release eligibility、claim support truth 或 archive grade。

MABW 的类器官失败研究已经展示了错误传播路径的可追溯性：在直接 LLM 起草中错误通常只留在最终文本里，而在 MABW 中，错误能沿着来源摘要 → 候选声明 → 筛选候选项 → Claim Ledger → 审计简报 → 最终交付回溯。Tax AI 的闭环结构为这种回溯提供了从"发现"到"修复"到"验证"的完整工程路径。

## 11. 局限性与未来工作

### 11.1 已知边界（v0.8.3）

MABW v0.8.3 不声称：语义证明、输出质量改善、自主修复、跨模型稳定物化、或自动化事实核查。

精确声称更窄：**每个实质性声明链接到一个注册的来源条目------可追溯，尚非语义证明。** 类器官产业失败研究（§9.2）是诚实的当前边界。按设计能通过门禁的错误（因为它们是支持度校准失败，而非缺少来源失败）留下了一条传播路径。v0.8.x 使这条路径可见；v0.9 将使支持充分性可观察并处于可裁决状态。

### 11.2 v0.8 北极星

v0.8.x 不是智能扩展序列。它是一个测量、复用、拓扑和实验序列------先测量，再复用冻结证据，再改变角色拓扑，再在更快更硬化的路径上运行实验。速度只能来自去除重复推理和复用哈希验证的冻结工件，绝不能来自更少的记录、更少的门禁或更少的批准。

### 11.3 v0.9 方向：从可追溯到支持充分性

v0.9 从\"这个声明有可追溯的记录吗？\"推进到\"每个实质性原子子声明是否有证据跨度级的支持，未被支持、薄弱、矛盾或遗漏的支持是否被路由到阻止、降级、裁决或非参考状态？\"

目标不是证明真相或消除幻觉。目标是使质量随机性成为可观察、可归因、可阻断、可复现、可比较和可裁决的。

**语义控制原则**：语义模型可以评估、质疑、提出标签、起草支持记录、标记不确定性和解释分歧。它们不得直接决定发布资格、声明支持真相、修复归属、归档/参考等级或未来运行策略。Python 只接受 schema、哈希、必填字段、词汇表、覆盖率检查、同意/分歧记录、校准元数据、裁决状态、阻断策略和发布等级规则。语义评分是控制记录的输入，不是最终权威。

**v0.9 概念路径**：

> source pack → evidence spans → claim ledger → atomic claim graph\
> → claim-support matrix → semantic assessment report\
> → adjudication queue → coverage gate → release eligibility report → delivery

解读：来源提供 evidence spans。evidence spans 支持 atomic claims。atomic claims 组成 material claims。claim-support matrix 记录支持充分性。release eligibility 决定交付/参考状态。

**v0.9+ 改进闭环：Finding Candidate System**

v0.9 的支持充分性控制面解决了"这条声明有没有被充分支持"的问题。但还有一个后续问题：**当系统发现失败时，如何把失败变成可验证的改进？**

与 OpenAI Tax AI 生产闭环 [A+1] 独立收敛，v0.9+ 将 MABW 已有的 failure-to-finding-to-repair 方向形式化为 Finding Candidate System：当 Claim-Support Matrix、coverage gate、audit report 或 human feedback 中出现可复现的问题时，系统将其转为结构化的 Finding Candidate。每个 finding 有唯一 ID、受影响的 claim 和 evidence span、期望行为、回归测试用例、修复范围定义和人类决策记录。

改进闭环不是自主自进化，而是一个有边界的工程流程：

> report failure → claim-level trace → structured finding → eval target → scoped repair → same-evidence regression → human review → release eligibility update

关键约束：并非每个修正都会自动变成修复任务。修正可能代表提取遗漏、映射问题、产品边界、领域判断，或者工作流噪声；只有反复差异经过审查并归并成可执行发现后，才进入改进队列 [A+1]。

### 11.4 非目标（v0.9 及以后）

MABW 不旨在成为真理证明系统。它不引入一个全局语义分数作为发布权威。它不让 LLM judge 决定最终支持真相。它不让 Python 直接执行语义支持判断。它不削弱 v0.8 的可追溯性、归档、事件日志、人类交付或冻结工件规则。

公开措辞纪律：*MABW 操作化语义支持充分性。MABW 不证明真相或消除幻觉。*

## 附录 A：合约 Schema 定义

configs/orchestrator_contract.yaml 中定义的四类合约范畴：Behavior（角色边界）、Process/Artifact（阶段就绪、工件预期）、Fact-Grounding/Evidence（声明可追溯性）、Quality/Audience（对齐读者的交付）。

## 附录 B：决策词汇表

六术语词汇表（continue/retry_stage/delegate_repair/request_human_review/block_run/finalize）加上 configs/stage_specs.yaml 中的阶段范围合法性表。

## 附录 C：控制面 Schema 注册表

运行时状态文件、证据与门禁表面、记忆与改进表面、交付/归档表面的完整字段表。权威定义在 docs/control-surfaces.md 和 src/multi_agent_brief/orchestrator/runtime_state/。

## 附录 D：专家角色卡

来自 configs/stage_specs.yaml 的十阶段流水线：doctor（Python）→ source-discovery → input-governance（Python）→ scout → screener → claim-ledger → analyst → editor → auditor → finalize（Python）。每个角色在合约表面内运行；Agent 角色起草内容；Python 角色写控制状态。

## 附录 E：评测框架

评测用例验证确定性控制行为，而非模型输出质量。11+ 打包的公开安全 fixture 覆盖：质量门禁、反馈分类、运行时状态阻塞、溯源投影、改进账本物化控制，以及 MABW-080 实验用例验证。

## 附录 F：词汇表

双语（English--中文）。关键术语：contract-backed control surface（合约支撑的控制面）、file-state blackboard（文件状态黑板）、decision vocabulary（决策词汇表）、stage completion transaction（阶段完成事务）、claim freeze transaction（声明冻结事务）、run integrity（运行完整性）、immutable archive（不可变归档）、fast-rerun import（快速重跑导入）、improvement ledger（改进账本）、single-writer principle（单一写者原则）、coverage gate（覆盖率门禁）、support calibration（支持度校准）、guidance manifestation（指导物化）、three quality layers（三层质量体系）。

*MABW 架构参考 v0.3.0（legacy pre-BriefLoop naming）。代码快照：v0.8.3，分支 \`main\`。2026-06-19。*
