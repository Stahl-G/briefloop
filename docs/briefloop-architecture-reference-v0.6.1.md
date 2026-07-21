# BriefLoop：面向可审计商业简报的开源闭环工程

## 架构参考 v0.6.1：动态知识冲突、证据治理与发布权威

| 字段 | 值 |
|---|---|
| 报告版本 | v0.6.1 |
| 产品基线 | v0.12.1；包含 `v0.12.1` tag（`c2a09157`）之后已合并的 #513—#515 |
| 代码快照 | `main@47ae439d0206a852a2a223db4051d28f39b54c38` |
| 分支 | `main` |
| 报告日期 | 2026-07-19 |
| 评测状态 | 计划中；尚未执行或冻结 |
| v1.0 首用户证据 | `not_satisfied` |
| 正式引用集 | 42 条；本版新增 2 篇 2026 年预印本 |

> **版本边界。** 本报告描述 `main@47ae439d0206a852a2a223db4051d28f39b54c38` 的实现状态。版本文件为 v0.12.1，但该 exact head 位于 `v0.12.1` tag 之后：它额外包含统一内部引用解析、引用片段边界修复和 WorkBuddy pilot contract 收口。因此，本报告把“发布标签能力”和“当前 main 能力”分开陈述，不把 post-tag 修复倒灌为 tag 证据。`run` 仍是外部运行时的 handoff launcher，而不是 Python 简报生成器。测试数量只描述回归表面积，不证明散文质量、语义真伪或首用户成功。稳定性仍以 `docs/architecture-status.md` 和 `docs/support-matrix.md` 为准。`docs/v1-pilot-evidence.md` 当前仍为 `not_satisfied`；接近 1.0 不等于已经证明输出质量、首用户可用性或管理就绪。

## 摘要

周期性企业简报不是静态知识检索任务，而是在过时参数记忆、最新检索证据和相互冲突来源之间作出有时间边界的发布判断。BriefLoop 是面向这一任务的开源控制系统。它不把 AI 辅助报告视为一次性文档生成，而把声明、证据、失败、修复和交付决定组织成可以检查、追溯和复核的受治理发布过程。

系统把智能解释与权威生效分开：智能体可以发现来源并提出声明；确定性事务负责校验 schema、保护冻结工件、记录状态迁移、执行门禁并暴露未解决缺口；人类负责语义裁决和交付批准。当前快照已经包含运行状态、声明与证据表面、质量门禁、恢复机制、可审计交付工件和面向产品的报告包。实验性支持充分性表面可以记录声明—证据提案和人工决定，但不能证明语义真伪或获得发布权威。

本版把动态知识冲突确立为周期性简报的结构性风险。既有研究表明，最新上下文未必覆盖参数记忆，检索来源本身也可能彼此冲突。BriefLoop 不声称自动发现全部冲突、裁决时间性真相或已经提高内容质量；它提供可追溯性、有限时间检查、对机械可判条件的确定性控制，以及对未解决问题的显式升级。计划评测尚未执行或冻结，v1.0 首用户证据仍为 `not_satisfied`。

## 本版修订

v0.6.1 是对冻结 v0.6.0 的研究质量修订，保留同一代码快照，不回写历史版本。本版：

- 保留 v0.6.0 新增的 12 篇同行评审知识冲突论文，并加入两篇高相关 2026 年预印本：确定性新鲜度聚合 [P34](#ref-p34) 与 ConflictRAG [P35](#ref-p35)；正式引用集由 40 条扩展到 42 条；
- 把 P12、P14、P16、P17 从旧预印本元数据升级到 EMNLP 2023 或 NeurIPS 2023 正式记录；
- 收窄未来研究摘要、冲突优先级和评测时态，不把未执行协议写成已完成实验；
- 把 vanilla RAG、确定性新鲜度和冲突感知 RAG 加入计划基线，同时保留人工裁决与发布权威边界。

逐条纳入理由、允许支撑范围和禁止外推边界见 [唯一引用索引](tech-report-v0.6.1/reference-index.md)；候选筛选和排除理由见 [引用筛选记录](tech-report-v0.6.1/reference-screening.md)。

> **名称约定。** BriefLoop 是本项目唯一的现行名称。旧命令、schema、归档文件名和实验 ID 中可能仍含历史字面量，这些字面量只为兼容和复现而保留，不构成项目别名。详细规则见 `docs/briefloop-naming.md`。

## 未来研究摘要（计划中，尚未执行）

> 当参数记忆、检索证据，以及发布、事件、可用和裁决时间彼此冲突时，大语言模型可能无法正确重建最近发生的变化。对于企业情报工作流而言，这是反复出现的运行风险，而不是普遍必然失败。模型可能检索到一份近期文档却仍沿用旧世界状态，把后续公告误写成首次发布，合并本应区分的产品阶段，或以超过证据支持程度的确定性重复已被取代的声明。许多 vanilla 纯提示词与检索增强管线仍把这些冲突留给模型隐式判断。
> 
> BriefLoop 把时间知识冲突视为系统治理问题。它运行在冻结来源证据之上，将智能体解释与权威状态变更分离，把重要声明绑定到可识别证据，记录冲突、失败与修订，并通过确定性控制和人工裁决管理交付。本报告定义一套拟预注册的企业周报评测协议，对比直接提示、Skill/智能体工作流、vanilla RAG、确定性新鲜度基线、冲突感知 RAG 与 BriefLoop；任务包计划覆盖发布日期—事件日期错位、过期状态、preview 到 GA 转换、厂商自报指标和相似实体混淆。
> 
> BriefLoop 不声称架构能让生成声明天然为真。它提供的是一个企业控制层，使证据、冲突、决定、未解决的不确定性和后续修复可以被检查、质疑和治理。只有当协议、任务包、判定规则和哈希实际冻结后，报告才可称其为预注册；只有实验完成后，才能陈述比较结果。

## 1. 核心洞察

### 1.1 架构宪章

以下原则来自真实运行中的失败，不是宣传口号。

1. **聪明的无权，有权的确定，生效的过人，过人的留痕。** 大语言模型和智能体可以理解、建议、拆解和起草，但不能直接写入权威状态、推进阶段、冻结证据、通过门禁或批准交付。真正生效的变更必须由确定性控制面执行，并经过人工确认、留下记录。
2. **机器能管的，不交给记忆。** 能通过 schema、验证器、门禁、事务、事件或测试检查的规则，不能只停留在提示词或交接说明中。
3. **同一个字段只许有一个写者。** Python 写状态、账本、事件、哈希、门禁和归档；大语言模型写内容草稿；人类批准偏好和交付决定。派生投影不能反向覆盖权威记录。
4. **有来源，不等于被支持；能追溯，不等于被证明。** 检索计划、候选来源、搜索摘要和模型摘要只能用于发现。来源是否真正支持声明，必须按支持强度、来源层级、适用范围和时效性分别记录。
5. **冻结工件不能静默改写，缺口不能被隐藏。** 合法变化必须产生新修订、新工件、新事件或明确的取代、撤销、污染记录。失败门禁、缺失证据、被拒声明和人工决策缺口都必须留下可查询记录。
6. **机械可解析的冲突按预先声明的层级处理，而不是交给模型临场说服；语义上未解决的冲突必须保持显式并进入人工裁决。** 事实契约和确定性门禁高于风格偏好；当前运行的修复高于跨运行的品味记忆；目标、读者、时间窗、来源政策和交付标准发生变化时，必须形成明确配置或新运行。声明的优先级不能自动决定哪个竞争来源在世界中为真。
7. **跨模块不变量必须在结构上闭合。** 一个控制规则若跨越事务、状态重算、注册表、门禁、投影和运行时适配器，就必须有唯一权威记录，并覆盖所有写者、重算者和读取者；不能依赖每条路径分别“记得”保留同一事实。

### 1.2 运营纪律

- **产品骨架：加速不能偷走问责。** 可以复用冻结证据、减少重复推理、并行无依赖任务，但不能删减账本、门禁、批准、事件、快照和归档。
- **公开声明：不说工件证明不了的话。** 未测量的能力应明确写成“尚未测量”；只有可追溯性时，就不能宣称语义证明或质量提升。
- **数据边界：私有事实不能替公共机制背书。** 真实业务流程可以提供失败类型和测试形态，但私有业务事实、客户材料、雇主数据和未公开信息不能进入公开仓库、测试样例或演示。

### 1.3 为什么编程智能体进步得更快

编程智能体的进步不仅来自模型能力，也来自软件工程已经建立的闭合反馈回路。Anthropic 对 workflow 与 agent 的区分提供了一个工程坐标：固定、可分解的任务更适合清晰输入输出和程序化检查；开放式路径才需要更高自治度。[E21](#ref-e21)

| 软件工程机制 | 提供的改进信号 |
|---|---|
| 测试套件 | 明确的通过或失败结果 |
| Git 历史 | 每次变更的作者、原因和差异 |
| 缺陷到提交的追溯 | 可以定位是哪次变更引入问题 |
| 持续集成 | 合入前自动验证 |
| 代码评审 | 重要变更经过人工批准 |

模型提供能力，基础设施提供可重复的反馈信号。企业简报通常缺少这些条件：质量问题很难转化为明确测试，过时数据难以定位到具体检索环节，口头反馈不会积累，“这一段感觉不对”也很少成为可复用的工程任务。因此，业务工作流往往只能依靠个人经验缓慢改进，而且经验难以传递。

### 1.4 BriefLoop 的核心论点

BriefLoop 的目标不是让某个模型本身更聪明，而是把软件工程中的问责基础设施移植到企业简报：

| 软件工程机制 | BriefLoop 中的对应机制 |
|---|---|
| 测试套件 | 工件验证和阶段质量门禁 |
| Git 历史 | `event_log.jsonl` 中的决定、时间、操作者和原因 |
| 缺陷追溯 | `artifact_registry.json` 中的生产阶段、角色和工件关系 |
| 持续集成 | Orchestrator 控制循环和阶段完成事务 |
| 代码评审 | `request_human_review`、修复计划、人工裁决和改进账本 |

v0.7.1 的参考运行暴露了一个决定性问题：智能体完成了内容流水线，却几乎完全跳过控制流水线。它生成了八个内容工件，但没有调用决定事务，没有运行门禁，`workflow_state.json` 仍停留在初始阶段。智能体事后承认，它把 Orchestrator 合约当成背景说明，而不是必须执行的 API。

这次失败推动 BriefLoop 将关键记账从提示词迁移到事务。智能体仍负责提出“做什么、为什么”；Python 控制面负责确认“该决定是否已经合法生效、依赖哪些工件、满足哪些条件”。重要规则不能只写在说明中，必须落到 schema、验证器、门禁、事务、事件或测试里。

### 1.5 与 Harness Engineering 的独立收敛

2026 年的三项 Harness 优化研究和一篇研究综述从不同方向给出了与 BriefLoop 可比较、但不可相互替代的外部坐标。

- **LIFE-HARNESS** 把冻结模型之外的运行时接口作为优化对象，但其结论建立在可计算奖励的确定性环境中，不能外推为开放域简报质量提升。[P01](#ref-p01)
- **Self-Harness** 使用失败挖掘、有界修改和域内/留出回归，支持“Harness 可以被工程化改进”，不支持“候选修改可以自批生效”。[P02](#ref-p02)
- **Meta-Harness** 进一步把模型、工具和环境放进端到端优化视野；它强化了评估整个运行系统的必要性，也强化了权限和接受协议必须留在可编辑循环之外的要求。[P05](#ref-p05)
- **Weng（2026）**综合了工作流、上下文生命周期、持久状态、工具、子智能体、权限与评估等 Harness 组成，并系统讨论评估器、奖励投机和人类监督风险。它是研究综述/技术文章，不是 BriefLoop 的实验依据。[E19](#ref-e19)

时间顺序也应准确记录：BriefLoop 的人工门禁改进账本和运行快照已进入 v0.7.0（2026-06-10），Python 持有的声明冻结事务已进入 v0.8.3（2026-06-16），均早于 Weng 的综述。因而，更准确的表述是**后验独立收敛**：Weng 提供了统一的研究语言和风险边界；BriefLoop 的仓库历史展示了这些原则在开放域企业简报中的一个早期工程实例。时间先后不能替代效果评测，也不构成优先权或性能优势证明。

### 1.6 从智能体工程到闭环工程

Loop Engineering 关注的不是如何写一次更好的提示，而是如何设计一个持续发现任务、分派任务、检查结果、记录状态并决定下一步的系统；本报告只借用该术语和范式归因，不把技术文章当作实验依据。[E20](#ref-e20) BriefLoop 将这一方法用于周期性企业简报：控制单元不再是代码差异，而是重要声明、证据片段、支持记录、`FeedbackIssue`、修复任务和交付决定。

| 闭环工程要素 | 编程场景 | BriefLoop 场景 |
|---|---|---|
| 定时发现 | 定期扫描问题 | 周报、月报和周期性研究任务 |
| 隔离工作区 | Git worktree | 独立运行工作空间 |
| 技能 | 项目级 `SKILL.md` | 受众画像、政策配置和角色契约 |
| 连接器 | issue tracker、数据库、API | 来源提供器和交付连接器 |
| 子智能体 | 制作者与检查者分离 | 起草者与审计者分离 |
| 持久记忆 | 磁盘文件和提交历史 | 改进账本、声明账本和事件日志 |
| 验证 | 单元测试和回归测试 | 质量门禁和同证据回归 |
| 人工复核 | Pull Request 评审 | 人工裁决和交付批准 |

## 2. 设计哲学

### 2.1 三层质量体系

BriefLoop 将质量区分为三个层次，避免把“流程合规”“交付干净”和“分析优秀”混为一谈。

1. **规则层（Law）**：机器可以检查的要求，例如引用是否存在、来源是否过期、数字是否与账本一致、读者版是否泄露内部标识。该层可以由哈希、事件和门禁报告验证，但只能捕获可形式化的错误。
2. **诚实交付层（Honesty）**：读者交付物是否干净、可读、没有内部流程残留或空白引用。它衡量交付纪律，不衡量分析深度。
3. **分析判断层（Wisdom）**：简报是否抓住真正重要的问题，分析是否有洞察、是否优于单模型基线。当前状态仍是**尚未测量**。不同运行之间的声明层工件不完全相同，因果归因尚不成立。

正确顺序是先稳定规则层，再稳定诚实交付层，最后在受控基线上测量分析判断。交付本身尚未稳定时，内容质量比较会受到过多混杂因素影响。

### 2.2 正确性、品味与证据

| 维度 | 关注内容 | 治理机制 | 权威写者 |
|---|---|---|---|
| 正确性 | 事实错误、过时数据、归因错配、结构违规 | schema、阶段验证和确定性门禁 | Python 控制面 |
| 品味 | 部门偏好、文化规范、未明说的读者期望 | 受众画像和人工批准的改进账本 | 人类；模型负责解释和应用 |
| 证据 | 来源与声明的绑定、支持强度、时效性和权威层级 | 声明草稿、冻结账本、证据片段和来源附录 | 模型起草；Python 冻结和验证 |

正确性可以部分机械化；品味必须保持可由人类编辑；证据位于两者之间。模型可以发现和起草声明，但声明 ID、冻结记录、哈希和支持元数据必须由确定性控制面持有。

### 2.3 治理域与控制面

四类合约回答“治理什么”：

| 合约类别 | 治理范围 |
|---|---|
| 行为合约（Behavior） | Orchestrator 与专家角色的权限边界 |
| 过程与工件合约（Process / Artifact） | 阶段就绪条件和预期工件 |
| 事实与证据合约（Fact-Grounding / Evidence） | 重要声明能否追溯到被登记的证据 |
| 质量与受众合约（Quality / Audience） | 交付物是否符合读者和质量要求 |

控制面回答“谁写、何时冻结、如何验证、失败时发生什么”。其理论谱系可以从工作流模式、黑板架构和契约式设计理解：前者描述控制流表达能力，共享黑板描述专门角色围绕共同状态协作，契约式设计强调前置条件、后置条件和不变量。[T01](#ref-t01) [T02](#ref-t02) [T03](#ref-t03) BriefLoop 的溯源关系也借鉴 W3C PROV 的实体、活动、责任主体与派生词汇，但不声称完整兼容或通过一致性测试。[T06](#ref-t06) 合约类别与控制面不是两套竞争架构：前者描述治理内容，后者把治理内容落实为文件、唯一写者、事务和失败状态。完整现行清单见 `docs/control-surfaces.md`。

北极星（Northstar）是产品治理与取舍建议表面，不是运行时角色、架构权威、Merge Governor 或工作空间控制面。它可以根据用户证据建议构建、延期或拒绝范围，但不能写运行状态、执行门禁、最终化、批准交付、合并代码或批准公开发布。主观取舍、商业承诺、试点参与、结果接受和真正生效的产品决定仍由人类持有；若决定改变当前运行的目标、读者、时间窗、来源政策或交付标准，必须形成显式配置变化或新运行。

### 2.4 单一写者原则

- Python 写控制状态、账本、事件、哈希、门禁、事务和归档；
- 运行时智能体写候选声明、筛选结果、声明草稿、简报正文和语义审计意见；
- 人类写批准、受众指导、交付决定和明确的运行方向。

`claim_drafts.json` 与 `claim_ledger.json` 被设计为两个工件，正是为了落实这一原则：模型只能写不带权威 ID 的草稿；Python 负责分配稳定 ID 并冻结账本。任何一方都不能顺手改写对方持有的工件。

### 2.5 速度原则

速度只能来自复用冻结工件、减少重复推理和并行无依赖任务，不能来自少记录、少门禁、少批准或弱化归档。快速重跑会导入并校验既有事实层，从分析阶段重新开始；它仍保留写作、审计、门禁、最终化和人工交付路径。加速来自复用，而不是省略。

## 3. 架构：五条控制骨干

![BriefLoop 从来源发现到人工交付的可审计闭环架构](assets/briefloop-architecture-v0.5.0.svg)

*图 1：智能体负责内容工作，确定性控制面负责状态、冻结、门禁和归档；人工负责关键授权与最终交付。*

### 3.1 运行时状态骨干

```text
runtime_manifest.json
→ workflow_state.json
→ artifact_registry.json
→ event_log.jsonl
```

Python 控制面是唯一权威写者。v0.12 将 Manifest、Workflow、Registry、Event Log 和 Finalize Report 的恢复读取绑定到同一个已打开工作空间 session；POSIX 使用 descriptor-bound/no-follow 读取，Windows 使用 handle-bound 读取并处理 reparse point。合法的可选缺失保持 typed absence，unsafe、stale 或非 canonical Registry 不投影值。该设计把第七条宪章落到共享 fail-closed 读取边界，避免不同消费者各自重开路径、各自解释同一控制文件。

### 3.2 证据与声明骨干

```text
来源证据
→ 持久来源证据
→ 输入分类
→ candidate_claims.json
→ screened_candidates.json
→ claim_drafts.json
→ 冻结事务
→ claim_ledger.json
→ audited_brief.md
→ audit_report.json
→ source_appendix.md
```

运行时智能体负责候选、筛选、声明草稿和简报内容；Python 在 `claim_drafts.json` 到 `claim_ledger.json` 的边界进行验证和冻结。溯源投影可以把声明、来源、工件、决定和门禁 finding 连接起来，但关系可追踪不等于来源语义支持，也不等于 W3C PROV 完整兼容。[T06](#ref-t06)

声明冻结事务的步骤如下：

1. 声明角色写入不含 `claim_id` 的 `claim_drafts.json`；任何层级预先携带 `claim_id` 都会被拒绝。
2. `briefloop state freeze-claim-ledger` 读取已验证草稿，按确定性顺序分配 `CL-####`，写入权威 `claim_ledger.json`，记录哈希和冻结元数据，并追加 `claim_ledger_frozen` 事件。
3. `briefloop state stage-complete --stage claim-ledger` 要求存在相匹配的冻结记录；哈希漂移、冻结元数据缺失或账本字节过期都会失败关闭。
4. 分析和审计角色只能读取冻结后的账本，不得读取草稿作为权威输入，也不得修改账本。

### 3.3 门禁骨干

```text
CompositeAuditAgent
├── DeterministicAuditAgent
├── QualityHarnessAuditAgent
└── NoOpSemanticAuditAgent
    → gates/auditor_quality_gate_report.json
    → gates/finalize_quality_gate_report.json
```

前两类审计均由 Python 执行，不调用大语言模型。语义审计槽当前仍是占位；运行时审计角色需要检查支持强度是否与措辞匹配，但项目尚未交付具有发布权威的模型语义审计器。审计阶段和最终化阶段各自读取阶段范围的门禁报告；旧版 `quality_gate_report.json` 只是兼容投影，不是冻结权威。

### 3.4 记忆与改进骨干

```text
audience_profile.md
→ audience_profile_snapshot.md

improvement/ledger.jsonl
→ improvement/memory.md
→ improvement_memory_snapshot.md
```

人类维护受众画像并批准改进指导；Python 负责从账本生成记忆、冻结每次运行的快照，并在运行清单中记录生效条目及 SHA-256。运行中发生的批准或撤销只影响后续运行，不能改变当前运行已经冻结的输入。每个账本修订通过前序哈希形成链式记录。

### 3.5 交付与归档骨干

```text
output/intermediate/finalize_report.json   # 单一交付真相
output/delivery/brief.md
output/delivery/<name>.docx
output/source_appendix.md
output/runs/<run_id>/
```

最终化先在候选位置渲染读者版并执行 reader-clean 检查，只有候选通过后才提升到 `output/brief.md` 和 `output/delivery/`。失败会写入失败的 `finalize_report.json`，但保留此前交付包不变；成功报告记录本次运行绑定的交付工件和 SHA-256。交付资格不等于交付成功，后者还需要当前运行绑定的 delivery outcome event；文件存在本身不是交付真相。归档继续保存交付物、中间工件、控制记录和哈希清单，历史运行不得原地覆盖。

### 3.6 产品层与支持充分性实验栈

```text
report_spec.yaml
→ ReportPack / ReportTemplate / PolicyProfile
→ atomic_claim_graph.json
→ evidence_span_registry.json
→ claim_support_matrix.json
→ semantic_assessment_report.json
→ semantic_support_acceptance_ledger.json
→ quality_panel.json / quality_summary.md / quality_panel.html
→ delivery_bundle.zip / audit_bundle.zip
```

该栈的权威边界如下：

- 专家角色可以起草原子声明图、证据片段、支持矩阵行和语义评估提案；
- Python 只验证 schema、引用关系、哈希绑定、必要行覆盖和裁决记录格式；
- `semantic-support adjudicate` 记录人工接受或拒绝，但裁决记录不会自动改写支持矩阵；
- `briefloop new`、`packs bundle`、`quality summarize`、`extract` 和 `sources materialize-pack` 只写工作空间结构或投影，不运行专家角色、不批准交付，也不证明语义正确性；
- 实验性 WorkBuddy/CodeBuddy 路径采用两阶段权限：checked-in role agent 只起草 handoff 指定工件；具备命令能力的 main session 必须重读 handoff 后执行允许的 CLI 事务。看见某个产物或 generic helper 叙述，不能代替 host-visible 的精确角色调用与返回记录。

稳定支持的产品入口为：

| 用户命令 | 内部报告包 | 用途 |
|---|---|---|
| `briefloop new industry-weekly` | `market_weekly` | 行业周报 |
| `briefloop new management-monthly` | `management_monthly` | 管理层月报 |
| `briefloop new document-review` | `evidence_extract` | 文档证据提取工作空间 |

## 4. 控制事务

### 4.1 阶段完成事务

`stage-complete`、`finalize` 和 `finalize-complete` 把阶段记账、候选提升和完成判定从提示词义务迁移到确定性执行。事务会：

- 检查预期工件是否已在 trusted Registry 解释结果中登记且有效；
- 更新 `workflow_state.json` 的阶段状态并向 `event_log.jsonl` 追加事件；
- 执行阶段特定前置条件，例如声明账本阶段必须存在匹配冻结记录；
- 在最终化时先检查候选读者版，再原子提升并把交付工件哈希写入 `finalize_report.json`；
- 在完成与交付投影中区分 eligibility、finalization success 和 delivery outcome，不从文件存在推断成功。

Orchestrator 决定行动及其理由；Python 记录该决定是否已经在满足条件的情况下合法生效。

### 4.2 声明账本冻结事务

| 操作 | 权威写者 | 工件或结果 |
|---|---|---|
| 起草声明 | 声明角色 | `claim_drafts.json`，不含 `claim_id` |
| 验证草稿 | Python | 拒绝预先写入的 ID 和无效结构 |
| 分配 ID | Python | 稳定的 `CL-####` |
| 冻结账本 | Python | `claim_ledger.json`、冻结元数据和事件 |
| 完成阶段 | Python | 无匹配冻结记录则拒绝完成 |

冻结完成后，分析和审计角色只能读取账本。任何修改都必须形成新运行或明确的污染、取代和修复记录。

### 4.3 运行完整性与污染

`workflow_state.json.run_integrity` 记录运行是否仍可作为干净参考证据。重置已执行运行、在过时状态上重放阶段或修改冻结工件时，系统会写入污染事件及原因。v0.12 的 recovery context 必须从同一可信工作空间 session 读取五类控制输入；supersede 会把下游工件标为 stale，直到它们被重新生成。受污染运行可以继续形成受约束的交付，但不能冒充 A 级受控实验；缺失、stale 和 unsafe 输入必须保持可见。

### 4.4 不可变归档

归档目录 `output/runs/<run_id>/` 保存：

- Markdown、DOCX 等交付工件；
- 声明账本、门禁报告和审计报告等中间工件；
- 运行状态、事件日志和运行清单等控制记录；
- 所有纳入清单的 SHA-256。

归档只能追加，不能原地改写。

### 4.5 快速重跑导入

`briefloop state import-fact-layer` 可以把已归档的来源证据、输入分类、候选声明、筛选结果和声明账本复制到新工作空间。事务复制原始字节、验证哈希、记录导入关系，并将已满足的上游阶段标记为“由导入完成”。`briefloop run --recipe fast-rerun` 从分析阶段开始；最终化时仍会按照新工作空间的时间重新检查来源时效性。快速重跑复用的是事实层，不复用旧简报、审计结果、最终化记录或交付批准。

## 5. 证据与声明治理

### 5.1 从来源到声明

```text
来源发现
→ 持久来源证据
→ 输入分类
→ 候选声明
→ 筛选结果
→ 声明草稿
→ 确定性冻结
→ 声明账本
```

只有已经物化的来源文件和受支持的来源配置条目才能成为证据。`source_candidates.yaml` 只用于规划和评审，不能代替 `sources.yaml`，也不能据此宣称来源发现已经完成。检索计划、搜索摘要和模型摘要都是发现材料，不是证据本身。

当前来源记录要求至少包含来源 ID、名称、类型、标题和内容。证据片段、抓取时间、来源层级和摘录哈希属于支持充分性方向；它们可以增强追溯，但仍不能自动证明来源在语义上支持声明。

### 5.2 声明草稿合约

`claim_drafts.json` 是声明冻结事务的输入。任何草稿条目或其元数据都不得预先包含 `claim_id`。冻结算法 `sorted_sequential_v1` 会按照稳定键排序并分配 `CL-####`；相同冻结输入产生相同 ID，但如果草稿集合本身发生增删或重排，则不承诺跨冻结保持原 ID。

这一设计避免模型伪造权威身份：模型负责声明内容，系统负责声明身份和冻结状态。

### 5.3 支持强度校准

v0.7.4 的失败研究暴露出五类常见问题：

1. **支持强度膨胀**：来源只表明存在某种监管讨论，正文却写成正式认可；
2. **来源权威膨胀**：会议消息或媒体报道被写成政府计划或官方事实；
3. **声明混同**：得到支持的主事实与未经验证的子结论写在同一句话中；
4. **归因错配**：一个来源被用来承载多个并未逐一得到支持的结论；
5. **预测被误当成证据**：二级市场预测或评论被作为核心事实依据。

这些问题不是“没有来源”，而是来源与措辞之间的校准失败。审计角色需要检查夸大表述、支持强度、置信度、证据关系和限制条件。实验性支持记录可以使用 `explicitly_supported`、`partially_supported`、`supportive_but_overextended`、`attribution_mismatch`、`needs_primary_source`、`unsupported` 等标签，但标签本身仍需要人工裁决，不能直接成为发布权威。

动态知识还增加了四类不同但相邻的校准风险：

6. **时间有效域膨胀**：历史上成立的事实被改写成在当前冻结时点仍然成立；
7. **版本混同**：旧稿、更正稿、更新稿和转载稿被当成互相独立且同等有效的证据；
8. **重复被误当成佐证**：多个转载副本形成数量优势，掩盖一个较新的原始来源；
9. **来源忠实度丢失**：模型依赖自身知识“纠正”输入材料，使输出可能更符合外部事实，却不再忠实于它声称总结的来源。[P29](#ref-p29)

时间知识研究表明，事实本身可能具有有效期，而来源可信度也可以作为显式生成信号；这两者都不能被简化成“有日期”或“来源等级高”便自动为真。[P30](#ref-p30) [P33](#ref-p33) 当前 exact head 可以记录来源的 `published_at`、`retrieved_at` 并执行确定性新鲜度检查，政策监管模块也有局部 `effective_date`；但它没有面向一般声明的 `valid_time` / `as_of` 模型，没有来源更正、撤回或取代关系，也没有通用冲突 finding 与 resolution 状态。因此，上述四项在本版中是架构要求和计划评测，不是已经交付的冲突治理能力。

### 5.4 来源附录的边界

`output/source_appendix.md` 在最终化过程中根据读者正文实际引用的声明生成，并被嵌入 Markdown 和 DOCX 交付物，同时保留一份审计副本。来源附录为读者提供追问入口，但不是事实正确证书。它证明的是“可以追到哪里”，不是“已经证明为真”。

## 6. 门禁与修复

### 6.1 阶段范围门禁

| 门禁报告 | 约束阶段 | 主要检查内容 |
|---|---|---|
| `gates/auditor_quality_gate_report.json` | 审计阶段完成 | 重要事实、时效性、目标相关性、覆盖遗漏 |
| `gates/finalize_quality_gate_report.json` | 最终化完成 | 读者版残留、内部 ID、流程用语和交付卫生 |

阶段范围报告是权威记录。旧的 `quality_gate_report.json` 只保留为兼容投影。完成事务没有绕过门禁的 `--force` 路径。

### 6.2 确定性审计栈

```text
运行时审计角色
→ CompositeAuditAgent
→ DeterministicAuditAgent
→ QualityHarnessAuditAgent
→ NoOpSemanticAuditAgent
→ audit_report.json
```

确定性审计负责来源、时效性、数字、日期、安全措辞、流程残留和脱敏检查；质量 Harness 审计负责重要事实、目标相关性和读者残留等规则。语义审计槽仍是占位符。未来即使加入模型语义评估，也不能覆盖确定性发现，不能单独决定支持真相或交付资格。

### 6.3 修复路由

`briefloop repair route` 是只读诊断命令。它把门禁、审计、注册表和工作流发现映射到应负责的阶段及允许修改的工件类别。它告诉 Orchestrator“修复应当由谁处理、可以改哪里”，但不创建正文、不执行修复，也不替代修复计划。

### 6.4 反古德哈特原则

《Precision Is Not Faithfulness》说明，只优化精度可能鼓励系统删掉难以验证但重要的内容。[P08](#ref-p08) 对 BriefLoop 而言，每个阻塞型精度门禁在上线前都必须回答一个问题：**系统最便宜的过关方式是什么？** 如果最便宜的策略是删内容，就必须同时设置覆盖或遗漏检查，防止通过沉默获得高分。

### 6.5 覆盖与遗漏连续性

当前稳定门禁会检查 `screened_candidates.json` 中的高优先级候选，是否在声明账本或被引用的简报中无声消失。它捕获的是“已经筛选通过，却在分析或编辑阶段被遗漏”的路径，不等于对所有相关事实具有完整召回，也不证明全文覆盖充分。

## 7. 受控记忆与改进

### 7.1 受众画像

`audience_profile.md` 是工作空间中的人工可编辑文件，用于记录结构偏好、部门词汇、语气和长期反馈。每次运行只读取冻结的 `audience_profile_snapshot.md`。运行过程中修改实时画像，只能影响后续运行。画像属于语义指导，不是证据，也不具有门禁权威。

### 7.2 改进账本

`improvement/ledger.jsonl` 是追加写入、带修订链、需要人工批准的工作空间账本。其生命周期为：

```text
提出 propose
→ 人工批准 approve
→ Python 重建 improvement/memory.md
→ 下一次运行冻结 improvement_memory_snapshot.md
→ 必要时撤销 revert
```

关键不变量包括：

- 提出条目不会影响任何运行；
- 批准只追加状态，不改变当前运行；
- 物化发生在下一次运行开始时；
- 被撤销条目从下一次记忆和快照中移除；
- `runtime_manifest.json` 中的 `materialized_entry_ids` 和哈希，记录本次运行实际读取了哪些指导。

### 7.3 指导是否被体现

实验性 `guidance_manifestation_report.json` 可以记录已批准指导在输出中的可观察状态：明确体现、部分体现、相互矛盾或无法观察。Python 只验证标签并统计数量，不判断标签是否语义正确，不修改改进记忆，也不阻止最终化。

BriefLoop-090 的归档实验可以导入外部评估者给出的体现评分，但该测量不属于普通产品路径，也不能据此宣称输出质量已经提升。

### 7.4 尚未交付的记忆表面

| 计划工件 | 状态 | 作用 |
|---|---|---|
| `improvement/intake.jsonl` | 延期 | 接收带来源关系的原始反馈 |
| `improvement/candidates.jsonl` | 延期 | 暂存尚未批准的规则或偏好候选 |
| `reference_samples/manifest.jsonl` | 计划中 | 保存经人工接受的品味样例 |

这些能力在核心账本的“提出—批准—物化—冻结—撤销”生命周期稳定之后再引入。

### 7.5 受控 Harness 改进协议（提案）

当前 exact head 已具备事件轨迹、门禁发现、`FeedbackIssue`、`RepairPlan`、评测 fixture、改进账本、运行快照、可信控制读取和事务式交付记录，但尚未形成端到端的 Harness 自我改进系统。未来协议必须保持以下权威边界：

| 阶段 | 允许行为 | 权威限制 |
|---|---|---|
| 观察薄弱点 | 从事件、门禁、审计和人工反馈形成结构化候选 | 观察不会自动变成修改 |
| 提出有界修改 | 智能体针对反复且可定位的问题提出窄修改，并声明可编辑范围和应保留行为 | 智能体不能写入活动 Harness |
| 回归验证 | 用域内案例确认目标问题已修复，用留出案例和同证据重跑检查副作用 | 评估器和权限控制位于可编辑循环之外 |
| 授权 | 人工接受或拒绝，确定性事务记录输入、版本、结果和决定 | 只有批准事务能生成候选新版本 |
| 生效 | 新版本仅影响未来运行；被拒方案和负面结果继续留痕 | 不回写当前运行或历史冻结运行 |

因此，BriefLoop 所谓“改进”不是让智能体直接改写自身控制面，而是把生产失败转化为可定位、可提案、可回归、可批准和可撤销的工程变更。企业简报中的重要性、分析品味和管理价值仍需要人类判断；确定性门禁只能提供局部、可审计的弱奖励面。

## 8. v0.12.1 / post-tag main 实现基线

### 8.1 版本演进

| 版本 | 主题 | 核心能力边界 |
|---|---|---|
| v0.8.3 | 声明冻结事务 | 草稿声明由 Python 分配稳定 ID 并冻结 |
| v0.9.x | 支持充分性实验核心 | 原子声明图、证据片段注册表、支持矩阵、语义提案与人工裁决记录 |
| v0.10.x | 产品层与交付硬化 | 报告配置、交付包投影、最终化事务和五步写作路径 |
| v0.11.x | 产品基线 | 三类产品入口、政策/模板诊断、运行时与操作面 |
| v0.12.0 | 可信读取与交付真相 | descriptor/handle-bound 控制读取、同 session recovery、candidate-before-promotion、`finalize_report.json` 单一交付真相 |
| v0.12.1 tag | 委派与产品治理边界 | WorkBuddy/CodeBuddy 两阶段权限、精确 delegation evidence、Northstar 建议权边界、v0.4 历史报告 |
| post-tag main | 引用解析与 pilot contract 收口 | #513—#515；不倒灌为 v0.12.1 tag 能力 |
| v1.0.0（目标） | 产品冻结 | 冻结承诺并满足首用户证据门禁，不以扩张能力为目标 |

### 8.2 稳定支持

- 默认、严格和 `human_assisted` 角色拓扑，以及 Delivery Editor；
- Hermes、Claude Code 和 OpenCode 运行时；`run` 生成 handoff，不替代外部角色执行；
- 运行状态、trusted Registry、事件日志、声明冻结、阶段完成、事务式最终化、污染/取代记录和不可变归档；
- 审计与最终化阶段门禁、覆盖和遗漏连续性检查、反馈与修复计划、Orchestrator 控制总开关；
- 改进账本、受众快照、每运行改进快照和溯源投影；
- `output/delivery/` 读者交付包、来源附录审计副本、`finalize_report.json` 交付真相和 current-run delivery outcome；
- 输入治理四分类，以及市场竞争、政策和监管分析模块；
- 25 个公开安全评测用例；该快照本地可收集 3710 个 pytest 测试项；
- `industry-weekly`、`management-monthly`、`document-review` 三类产品入口。

### 8.3 实验性能力

- 原子声明图、证据片段注册表、声明—证据支持矩阵、语义评估与人工裁决；
- 更广的 Product OS 扩展：模板渲染、政策配置门禁适配、质量面板、重要性和支持措辞诊断；
- UTF-8 文本证据片段种子、持久来源证据包、SourceHub Lite 和快速重跑事实层导入；
- Codex custom-agent、source-clone WorkBuddy/CodeBuddy role assets、飞书、Gmail、PDF 与 MinerU 等路径；
- BriefLoop-090 及其冻结历史 ID `MABW-080` 的归档实验工具。

### 8.4 尚未交付

- 端到端问题候选系统，以及把候选安全提升为新 Harness 版本的批准事务；
- 具有发布阻断权的完整语义覆盖门禁、回归 Harness 和发布资格总表；
- 已验证的跨模型输出质量提升、完整事实核查、私有商业 benchmark 和自主学习；
- 能证明真实 WorkBuddy/CodeBuddy 角色委派与结果质量的外部运行证据；
- 已由真实 package-index 制品 smoke 验证的 `pipx install briefloop` 主安装路径；
- 满足 v1.0 要求的首用户 pilot 证据。

### 8.5 v1.0 证据门禁

`docs/control-surfaces.md` 中的冻结清单标识可获得向后兼容承诺的表面。v1.0 还要求至少一条可公开复现的首用户证据，例如 external fresh-clone、WorkBuddy 首用、pilot checklist 或周期性周报 dogfood。当前 exact head 的 `docs/v1-pilot-evidence.md` 仍明确记录 `Status: not_satisfied`；它是 release-readiness 证据账本，不是语义证明、输出质量证明、交付批准或发布权威。

BriefLoop-090 已完成一个合成 `auditable_brief` 试点，可以支持“在单一案例中观察到指导模式差异”这一窄口径陈述，不能支持泛化的输出质量、管理就绪或交付质量声明。

## 9. 参考证据与失败研究

### 9.1 v0.7.2 公开太阳能集成运行

两轮公开材料运行证明了以下机制能够闭合：

- 已批准指导可以物化为每次运行冻结的改进记忆；
- 质量门禁曾三次主动阻止阶段推进，并在修复后通过；
- Orchestrator 能读取冻结快照，而不是实时可变的工作空间记忆；
- `runtime_manifest.json` 记录了生效条目 ID 和相应哈希。

这次运行不能证明输出质量提高，也不能证明指导产生了因果效果。两轮运行的候选声明、筛选结果和声明账本哈希并不相同，因此指导不是唯一变量。该运行适合作为 B+ 级集成证据，不是 A 级受控实验。

### 9.2 v0.7.4 失败研究

一次产业研究运行生成了完整、可读的简报，也保留了全套流程工件；外部评审仍发现支持强度膨胀、权威层级错配、声明混同、归因错配和预测被误当作事实等问题。

它没有证明 BriefLoop 输出更好，却证明了一个更窄也更重要的能力：在直接模型起草中，错误往往只留在最终文本里；在 BriefLoop 中，错误可以沿“来源摘要—候选声明—筛选结果—声明账本—已审计正文—读者交付物”回溯。失败没有被自动修好，但传播路径被保存下来。

### 9.3 内容与控制解耦

v0.7.1 的运行说明，模型能够完成所有内容工作，同时跳过几乎全部控制义务。该失败直接促成阶段完成、最终化完成和声明冻结事务。它支持的结论是：**提示词义务不等于执行保证。** 如果规则重要，就必须存在可由机器验证的执行路径。

### 9.4 证据边界

| 证据 | 可以支持的结论 | 不能支持的结论 |
|---|---|---|
| 太阳能 B+ 运行 | 门禁真实执行；改进记忆链路闭合 | 输出质量提高；指导具有因果效果 |
| 失败研究 | 错误传播路径被保留；失败可分类 | BriefLoop 优于单模型基线 |
| 内容与控制解耦 | 模型不适合承担低层权威记账 | 所有模型都会以相同方式失败 |
| BriefLoop-090 | 同一冻结事实层下可观察到指导模式差异 | 泛化质量、管理就绪或 DOCX 质量 |

### 9.5 合成试点与产品层参考包

BriefLoop-090 使用单一公开安全的合成案例，对基线组（`baseline`）、记忆组（`memory`）和仅提示组（`prompt-only`）三种条件进行盲评和哈希绑定导入。观察结果符合预期：基线组未体现目标指导，记忆组较稳定，仅提示组出现过度应用。该结果仅适用于一个案例。

v0.11.3 产品层参考包展示了确定性 `same_evidence_reader_quality_regression`：读者版不含内部声明标记，审计包保留追踪记录，质量面板汇总重要性、模板、措辞和轨迹诊断。该参考包不调用模型，因此也不能证明模型输出质量或交付批准。

## 10. 相关研究与产业实践

### 10.1 Harness 适配与优化

LIFE-HARNESS、Self-Harness 与 Meta-Harness 共同表明，模型与环境之间的 Harness 可以成为独立优化对象。[P01](#ref-p01) [P02](#ref-p02) [P05](#ref-p05) 但三者的任务、奖励和接受机制都不能直接外推到开放域企业简报。BriefLoop 因此只吸收“失败可结构化、修改应有界、回归应覆盖留出行为”这一方法论；活动控制面、评估器、权限和批准事务必须位于候选修改之外。

Weng 的综述把 Harness 扩展为工作流、上下文生命周期、持久状态、工具、子智能体、权限和评估组成的运行系统，并强调递归结构、模糊评估、奖励投机和长期维护目标仍是风险。[E19](#ref-e19) 本报告把它用于领域定义和风险综合；任何机制或性能结论仍回到原始论文，且不把综述写成同行评审实验。

BriefLoop 已稳定实现轨迹调控的一部分：当重试、修复循环或重复阻塞超过预算时，Python 将当前阶段的合法决定收窄为 `request_human_review` 和 `block_run`，并写入事件日志。这是控制状态收窄，不是自动修复。

### 10.2 多轮反馈与回归

DRA Multi-Turn 发现，流程级反馈可以带来明显的单轮改善，但后续改写会使先前已经满足的条件回归。[P06](#ref-p06) 这支持 BriefLoop 的定向修复、冻结快照和外部门禁，也解释了同证据回归不能只问“本轮问题是否消失”，还要问“此前通过的行为是否仍被保留”。该研究不提供 BriefLoop 自身的质量提升幅度。

### 10.3 可审计的人机协作

CHAP 使用工作空间、任务、工件和追加式证据日志描述多人—多智能体协作。[P07](#ref-p07) 它关注通信协议；BriefLoop 关注企业简报内部的治理和发布责任。两者都要求协作结果落在可检查工件上，而不是只存在于短暂对话中；本报告不声称协议或 schema 兼容。

### 10.4 评估方法

FActScore 把长文本拆成原子事实，ALCE 把引用质量从一般流畅度中分离出来；二者说明“有引用”与“被完整支持”必须分开评估。[P12](#ref-p12) [P13](#ref-p13) 《Precision Is Not Faithfulness》进一步提醒，单一精度指标可能奖励“少说少错”，因此精度门禁需要覆盖连续性约束。[P08](#ref-p08) ResearchLoop 则提供外置证据门禁和持久声明绑定的相邻系统对照。[P20](#ref-p20) BriefLoop 把冻结、哈希、覆盖检查、阶段状态和交付卫生留在非模型控制面，同时把语义判断保留为提案或人工裁决输入；它不声称原子化或 provenance 本身证明真理。

### 10.5 多智能体框架

AutoGen、CAMEL 和 MetaGPT 分别代表可对话智能体、角色扮演协作与 SOP 编码流水线。[P09](#ref-p09) [P10](#ref-p10) [P11](#ref-p11) EvoMAS 则把多智能体系统生成表述为结构化配置空间中的执行反馈驱动进化，并在其基准中报告性能与可执行性结果。[P21](#ref-p21) 这些工作研究“如何组织智能体”；BriefLoop 的差异在于“谁有权让结果生效”。EvoMAS 在本报告中只支持候选拓扑/配置可被搜索，不支持 BriefLoop 已实现自动架构进化。工程上，多智能体只在上下文隔离、并行探索或专业化确有收益时值得使用。[E10](#ref-e10)

### 10.6 记忆与偏好

Self-Refine 和 Reflexion 展示了模型自反馈、语言反馈与情节记忆如何影响后续输出或试次。[P16](#ref-p16) [P17](#ref-p17) Hermes Agent 的持久记忆文档则提供 `USER.md`/`MEMORY.md` 式人类可读文件表面和可选写入批准。[E22](#ref-e22) BriefLoop 借鉴的是可读文件表面，不继承其权威模型：受众指导还要经过工作空间账本、人工批准、每次运行冻结、链式哈希和生效条目清单。实时记忆不能静默改变当前运行，偏好不能覆盖事实门禁，投影不能回写源账本。

### 10.7 企业知识工作智能体

本节只保留能承担明确架构论证的一手工程资料：workflow/agent 区分、多智能体适用边界、Loop Engineering 术语、持久记忆表面和 Tax AI 生产闭环。其余产品发布、聚合页和重复案例不进入正式 bibliography。工程文章可以说明实践形态，不能证明 BriefLoop 实现正确、质量更高或达到生产就绪。

#### 10.7.1 从对话到可交付成果

周期性简报同时包含两种工作：目标、阶段和交付格式明确的部分适合可预测 workflow；来源探索、重要性判断和解释冲突仍需要具备工具与上下文的 agent。Anthropic 的工程文章明确区分预定义代码路径中的 workflow 与模型动态决定过程的 agent。[E21](#ref-e21) BriefLoop 因此让外部运行时角色承担内容工作，让 Python 控制面承担状态转移、冻结和交付事务。

#### 10.7.2 企业分析首先是动态知识、冲突与验证问题

企业分析的首要难题不是让段落更像报告，而是在特定冻结时点上保持实体、时间、范围、来源层级和支持关系清楚。FActScore 与 ALCE 分别从原子事实和引用质量说明了中间对象的重要性。[P12](#ref-p12) [P13](#ref-p13) 对周期性简报而言，这一问题还包含动态知识裁决：同一个模型可能同时面对训练时留下的旧知识、本周检索到的新材料，以及彼此不一致的公告、转载和更正。

知识冲突综述通常区分三类情形：当前上下文与参数知识之间的 `context-memory conflict`、不同外部上下文之间的 `inter-context conflict`，以及参数记忆内部同时编码多个不一致答案的 `intra-memory conflict`。[P22](#ref-p22) 对简报和摘要任务还必须单独区分事实正确性与来源忠实度：模型可能输出现实上正确的内容，却通过擅自“纠正”来源而破坏声明—证据关系。[P29](#ref-p29)

新闻流研究说明，更新检索空间确实能帮助模型快速适应，但底层语言模型仍然过时时，系统会弱于同步更新参数模型的方案。[P23](#ref-p23) DYNAMICQA 进一步发现，动态事实更容易形成参数记忆内部冲突，而存在内部冲突的事实也更难被新上下文更新。[P24](#ref-p24) 这与时间知识研究的基础观察一致：许多事实具有有效期，而大多数语言模型训练在特定时间的数据快照上。[P30](#ref-p30)

检索本身也可能制造冲突。受控实验显示，一些检索增强模型即使获得正确证据仍会坚持错误的内部记忆，并可能受证据数量和确认偏差影响。[P25](#ref-p25) QACC 则在其开放域 Google Search 设置下发现，多达约四分之一的无歧义问题会检索到相互冲突的上下文；这个比例不能外推成新闻周报或企业数据的总体冲突率。[P26](#ref-p26)

提示模型“考虑最新事实”也不是可靠控制。时间冲突研究发现，明确提示事实可变性会增加模型对时间变化的表述，却没有提高该研究设置下的事实准确率；模型口头识别时间冲突，不等于最终预测正确。[P27](#ref-p27) 当冲突缺少充分裁决依据时，透明暴露分歧而不是让模型静默选择，是更适合可审计系统的行为。[P28](#ref-p28)

一篇 2026 年预印本进一步把“当前值”冲突拆成语义候选提取与确定性聚合。在带有明确版本序号的 MemoryAgentBench FactConsolidation 单跳任务上，候选提取加 Python `max(serial)` 的整条管线比自由文本 LLM 判断高 10.8 个百分点，262K 上下文下差距扩大到 21 个百分点。作者同时明确限定：这是 prompt、输出格式、温度和 resolver 共同变化的管线级效应；在 45 条 LongMemEval knowledge-update 样本上，`max(timestamp)` 管线没有优于 LLM 判断。该结果支持把可安全全序的 current-value 冲突设为确定性微基线，不支持把“最新来源”写成一般真相裁决规则。[P34](#ref-p34)

ConflictRAG 预印本则提出 `detect → classify → resolve → generate` 管线，在生成前区分文档间冲突和参数—上下文冲突，并保留来源归属与冲突说明。它为冲突感知 RAG 提供了有用的邻近方法和计划基线，但不是企业发布权威模型：该方法在参数—上下文冲突时偏向检索证据，在时间冲突时按近期性排序，来源评分仍由 LLM 提取，且论文承认 CARS 在结构上偏向具有显式冲突模块的系统。[P35](#ref-p35)

这些结果不意味着检索增强没有价值。FreshLLMs 表明，经过组织的搜索上下文可以改善快速变化知识上的问答表现；Astute RAG 和可信度感知生成也说明冲突感知、来源感知的后检索方法是活跃且有效的研究方向。[P31](#ref-p31) [P32](#ref-p32) [P33](#ref-p33) 但 benchmark 改善不等于自动确定最新来源、完成版本取代，或获得企业发布权威。

BriefLoop 的来源包、声明账本、证据片段和支持记录是对这些中间对象的工程化表达，但当前只能保存来源时间、声明关系、支持提案和人工裁决记录；它不声称自动发现全部知识冲突、确定哪个来源为真、识别所有更正或取代关系，或独立完成动态事实核查。

因此，模型负责发现、起草、标注候选冲突和质疑；Python 负责冻结、验证、记录与执行确定性门禁；人类负责无法确定性裁决的语义分歧、运行方向和最终交付。三者之间不是能力高低关系，而是权威分工。

#### 10.7.3 从提示词到运行支撑系统

Loop Engineering 把关注点从一次提示上移到持续发现、分派、检查和记忆的系统；Building Effective Agents 则强调简单、可组合的模式和清晰阶段边界。[E20](#ref-e20) [E21](#ref-e21) BriefLoop 的合约、控制面、门禁、冻结工件、事件日志和人工批准是在简报场景中的具体实现选择；两篇技术文章不构成其测试证据。

#### 10.7.4 多智能体的适用边界

多智能体系统在三种情况下最有价值：隔离会污染主上下文的子任务、并行探索较大搜索空间、为不同职责配置专门工具和上下文；除此之外，协调成本常常超过收益。[E10](#ref-e10) BriefLoop 因此不以“更多智能体”为卖点；角色拓扑可以变化，但工件契约、唯一写者和控制责任保持不变。

#### 10.7.5 从质量治理到安全治理

v0.5 不把质量控制泛化为完整安全证明。当前可以陈述的是：控制读取采用 fail-closed 的 descriptor/handle 边界；实验性角色没有 CLI 事务权；人工批准、事件日志、冻结快照和交付记录保持可审计。项目尚未发布完整威胁模型，也不声称满足零信任架构、组织级安全标准或监管要求。

#### 10.7.6 生产追踪与受控改进

OpenAI 与 Thrive Holdings 的 Tax AI 案例把生产改进闭环组织为专家从业者修正、从源材料到最终提交的产品追踪，以及把反复问题转成定制评测和有范围工程任务。[E01](#ref-e01) 文章同时说明，模糊或不可安全自动化的案例回到产品团队，工程师仍负责架构、产品决定和上线；因此它支持受控闭环，不支持无边界自主改进。

BriefLoop 的对应取向是：人工修改、审计 finding、引用错配和支持不足先形成结构化问题；只有反复、可定位、可测试的问题才进入候选修复。自我改进不是模型独自反思，而是生产系统把失败转成可验证、可批准、可回退且只影响未来运行的工程变更。当前项目尚未完成这条端到端提升路径。

| Tax AI 实践 | BriefLoop 中的对应机制 |
|---|---|
| 源文件 | 来源包 |
| 字段提取 | 原子声明提取 |
| 字段引用 | 证据片段 |
| 专家修正 | 人工裁决和反馈问题 |
| 字段级复核行 | 支持记录和 `FeedbackIssue` |
| 重复修正模式 | 评测目标 |
| 有边界的代码修复 | 有范围限制的工作流修复 |
| 回归评测 | 语义回归和同证据重跑 |
| 已提交结果 | 经人工批准的简报交付物 |

## 11. 局限性与未来工作

### 11.1 已知边界

当前 exact head 不声称具备以下能力：

- 自动证明语义真伪或完成事实核查；
- 自动识别或裁决全部 `context-memory`、`inter-context` 和 `intra-memory` 知识冲突，包括动态事实、来源更正、版本取代、撤稿、争议事实和来源忠实度冲突；
- 已经提高简报内容质量、管理价值或跨模型稳定性；
- 自主执行修复、自主修改活动 Harness 或自行批准未来运行政策；
- 默认生成可直接发送给管理层的交付物；
- 仅凭文件存在、generic helper 叙述或 role asset 存在就证明真实委派；
- WorkBuddy/CodeBuddy、Codex、Gmail、飞书、PDF 或 PyPI 路径已经稳定生产就绪；
- 已满足 v1.0 首用户证据门禁；
- 已交付端到端自我改进 Harness。

当前更准确的能力边界是：重要声明可以连接到注册来源、工件和控制记录；可选支持记录和语义评估提案可以被观察和人工裁决；来源可以记录发布日期、抓取时间并接受新鲜度检查。这些能力提供可追溯性、有限时间信号和支持充分性记录，但不提供通用有效时间、来源取代关系、冲突发现完备性或真理证明。

当前版本尚未自动聚类由验证器确认的薄弱点，尚未交付 Harness 候选的批准/提升事务，也没有证明修改后的 Harness 在留出简报案例上无回归。改进账本管理的是人工批准的读者指导，不授权智能体修改代码、合约、门禁、政策或未来运行读取的控制状态。v0.12 的进展是把读取、恢复、最终化和委派权限闭合得更可靠，而不是把更多权威交给模型。

### 11.2 v1.0 的重点

v1.0 的工作重点应当是冻结承诺并取得首用户证据，而不是继续扩展实验面。产品层已经具备三类工作空间入口、报告配置、五步写作路径、状态投影、读者/审计交付包和质量面板；但 `docs/v1-pilot-evidence.md` 仍为 `not_satisfied`。下一步需要让一个非维护者在全新环境中完成可复现运行，并留下成功、困惑、失败、修复和剩余限制的证据，而不是由维护者替用户推断可用性。

### 11.3 支持充分性方向

已经存在的实验面包括原子声明图、证据片段注册表、声明—证据支持矩阵、语义评估提案、人工裁决、持久来源证据包和质量面板。仍待完成的路径为：

```text
阻塞式覆盖门禁
→ 域内/留出回归接受协议
→ 具有发布权威的回归 Harness
→ 发布资格总表
→ 问题候选系统
```

语义模型可以提出支持标签、冲突类型、候选取代关系、来源差异和不确定性说明，但不能直接决定哪个动态事实为真，不能静默丢弃竞争来源，也不能仅凭来源数量、模型置信度或与参数记忆的一致性授权发布。它同样不能直接决定修复归属、归档等级、未来运行政策或发布资格。发布权威仍在 schema、哈希、政策、人工裁决和确定性阻断规则中。

### 11.4 从失败到改进

未来问题候选系统应遵循以下路径：

```text
报告失败
→ 声明级追踪
→ 结构化问题
→ 评测目标
→ 有范围限制的修复
→ 同证据回归
→ 人工复核
→ 更新发布资格
```

不是每次修正都应自动进入工程队列。进一步修改 Harness 时，必须预先声明可修改范围，把评估器和权限控制放在可编辑循环之外，用域内案例确认目标问题、用留出案例检查副作用，并让批准版本只影响未来运行。EvoMAS 表明多智能体拓扑可以在结构化配置空间中由执行轨迹驱动搜索。[P21](#ref-p21) 对 BriefLoop 而言，可接受的未来路径只能是“生成候选配置 → 确定性 schema/不变量验证 → 域内与留出回归 → 人工批准 → 新版本留痕生效”，而不是运行中的智能体直接改写自身权威。

### 11.5 非目标

BriefLoop 不以一个全局语义分数作为发布权威，不让模型评审者决定最终支持真相，不让 Python 假装具备语义判断，也不会为了速度削弱账本、事件日志、归档、人工交付和冻结工件规则。

对外最准确的一句话是：

> BriefLoop 让企业简报中的声明、证据和交付决定进入可审计的工程闭环；它不证明真相，也不消除幻觉。

## 附录 A：合约类别

`configs/orchestrator_contract.yaml` 定义四类合约：

| 类别 | 中文说明 |
|---|---|
| `behavior` | 角色权限和行为边界 |
| `process_artifact` | 阶段顺序、就绪条件和工件预期 |
| `fact_grounding_evidence` | 声明与来源、证据和支持记录的关系 |
| `quality_audience` | 质量要求、读者要求和交付边界 |

## 附录 B：决定词汇表

Orchestrator 的合法决定包括：

| 决定 | 含义 |
|---|---|
| `continue` | 当前阶段满足条件，继续下一阶段 |
| `retry_stage` | 在同一阶段重新执行 |
| `delegate_repair` | 将有界修复交给相应角色 |
| `request_human_review` | 请求人工判断或批准 |
| `block_run` | 阻止运行继续 |
| `finalize` | 在审计和门禁满足后进入最终化 |

各阶段允许使用哪些决定，以 `configs/stage_specs.yaml` 为准。

## 附录 C：控制面索引

权威控制面分为四组：

- 运行状态：运行清单、工作流状态、工件注册表和事件日志；
- 证据与正确性：来源证据、声明账本、门禁报告、审计报告和支持记录；
- 品味与改进：受众画像、改进账本、冻结快照和体现诊断；
- 交付与归档：最终化报告、读者交付包、审计包和不可变运行归档。

字段级权威定义见 `docs/control-surfaces.md` 和 `src/multi_agent_brief/orchestrator/runtime_state/`。

## 附录 D：角色与阶段

```text
doctor（Python）
→ source-discovery
→ input-governance（Python）
→ scout
→ screener
→ claim-ledger
→ analyst
→ delivery-editor
→ auditor
→ finalize（Python）
```

默认拓扑允许 Scout 同时完成发现和筛选；严格拓扑使用独立 Screener；`human_assisted` 拓扑可以在指定节点引入人工参与。拓扑只改变角色分配，不改变工件契约和控制责任。

## 附录 E：评测框架

当前仓库打包 25 个公开安全评测用例；在 exact head 上，`pytest --collect-only` 可收集 3710 个测试项。前者是控制面行为 fixture，后者是测试发现数量，两者都不是模型文章质量分数。评测设计遵循《AI Agents That Matter》的取向：测量真实系统行为、成本、复现性和失败模式，而不是只比较一个准确率。[P19](#ref-p19) G-Eval 和 MT-Bench 说明 LLM 评审可以作为可扩展的辅助测量，但其位置、冗长、自偏好和模型偏差使它不能成为发布权威。[P14](#ref-p14) [P15](#ref-p15)

**当前已覆盖的确定性层**：质量门禁、反馈分类、运行时阻塞、轨迹预算、溯源投影、来源证据包、改进记忆、读者残留、交付真相、伪造事件与 Hermes 静态不变量。

**v0.5 之后需要执行、但本报告尚不宣称完成的测量层**：

- 声明级支持与引用完整性：按原子事实和引用质量分别核对，并保留人工裁决。[P12](#ref-p12) [P13](#ref-p13)
- 同证据比较：固定来源包、声明候选、截止时间和模型条件，对比直接提示、Skill/智能体 workflow、vanilla RAG、确定性新鲜度、冲突感知 RAG 与 BriefLoop。
- 盲评读者质量：至少两名独立人类评审，预先声明 rubric，分开测重要性、清晰度、校准与可行动性。
- LLM 评审敏感性：只作次级分析，交换答案位置、控制长度、使用多 judge 并报告分歧。
- 成本与时间：记录 token、模型调用、人工分钟、返工轮次和失败恢复时间。
- 首用户路径：fresh clone、安装/doctor、真实 role delegation、阻塞理解、交付判断和困惑点。
- 动态知识与冲突：固定问题和来源包，分别构造旧参数知识与新官方来源冲突、多个旧转载对抗一个新原始来源、更正/撤稿取代旧版本、两个可信来源无法裁决、模型擅自纠正来源，以及同名实体混淆。计划指标包括 `conflict_detection_recall`、`stale_fact_adoption_rate`、`unsupported_resolution_rate`、`transparent_conflict_disclosure_rate`、`source_fidelity_error_rate`、`supersession_resolution_accuracy`、`correct_human_escalation_rate`、`current_value_resolution_accuracy`、`as_of_state_reconstruction_accuracy`、`temporal_operator_selection_accuracy`、`deterministic_resolvability_coverage` 和 `unsafe_automatic_resolution_rate`，并同时记录人工裁决时间、模型调用成本和返工轮次。[P22](#ref-p22) [P23](#ref-p23) [P26](#ref-p26) [P28](#ref-p28) [P29](#ref-p29) [P34](#ref-p34) [P35](#ref-p35)

**计划比较条件**：

| 条件 | 回答的问题 |
|---|---|
| 直接提示 | 单次模型调用能完成什么 |
| Skill / 智能体 workflow | 更长指令和角色拆分是否足够 |
| Vanilla RAG | 仅加入外部检索是否足够 |
| 确定性新鲜度基线 | 对可安全全序的 current-value 案例，简单版本聚合能完成什么 |
| 冲突感知 RAG | 显式冲突发现与解析能改善什么 |
| BriefLoop | 冻结、透明披露、人工升级和发布控制是否提供额外价值 |

**确定性解析资格（计划协议，不是现行能力）**：

```text
同一规范化声明范围
AND 存在显式版本或取代标记
AND 版本形成有效全序
AND 问题要求 current value
AND 不存在来源权威冲突
AND 不存在披露政策冲突
→ 可以进入确定性解析候选

否则
→ 保持 unresolved / 转人工裁决
```

上述动态知识指标全部是计划评测，尚未执行或冻结。所有测量必须区分机制回归、输出质量、运行时委派和用户可用性，任何一层通过都不能替另一层背书。

历史实验命令使用冻结标识 `MABW-080`，状态为“归档实验性”，不属于产品使用路径。

## 附录 F：中英文术语

| 英文术语 | 本报告采用的中文 |
|---|---|
| Harness | 运行支撑系统；首次出现时保留 Harness |
| control surface | 控制面 |
| artifact | 工件 |
| claim | 声明 |
| Claim Ledger | 声明账本 |
| evidence span | 证据片段 |
| Claim-Support Matrix | 声明—证据支持矩阵 |
| stage completion transaction | 阶段完成事务 |
| claim freeze transaction | 声明冻结事务 |
| run integrity | 运行完整性 |
| immutable archive | 不可变归档 |
| support calibration | 支持强度校准 |
| guidance manifestation | 指导体现情况 |
| bounded Harness proposal | 有界 Harness 修改提案 |
| held-in / held-out regression | 域内/留出回归 |
| same-evidence rerun | 同证据重跑 |
| release eligibility | 发布资格 |
| parametric knowledge | 参数知识 |
| contextual knowledge | 上下文知识 |
| context-memory conflict | 上下文—参数记忆冲突 |
| inter-context conflict | 上下文间冲突 |
| intra-memory conflict | 参数记忆内部冲突 |
| temporal knowledge conflict | 时间性知识冲突 |
| source fidelity | 来源忠实度 |
| supersession | 取代关系 |

## 附录 G：研究与产业实践引用矩阵

本版沿用 v0.6.0 的 61 条候选资料和 40 条正式引用，并新增筛选两篇 2026 年预印本，共形成 63 条候选、42 条正式引用。逐条作者、版本、类型、`supports`、`does_not_support` 与 `used_in` 由 [唯一引用索引](tech-report-v0.6.1/reference-index.md) 统一管理；筛选与排除理由见 [引用筛选记录](tech-report-v0.6.1/reference-screening.md)。P34 与 P35 均为预印本，不计入同行评审证据。

| ID | 完整引用 | 类型 |
|---|---|---|
| <a id="ref-p01"></a>P01 | Xu, T., Wen, H., & Li, M. (2026). [*Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents*](https://arxiv.org/abs/2605.22166). arXiv:2605.22166v2. | 预印本 |
| <a id="ref-p02"></a>P02 | Zhang, H., Zhang, S., Li, K., Zhang, C., Chen, Y., Zhang, Y., Bai, L., & Hu, S. (2026). [*Self-Harness: Harnesses That Improve Themselves*](https://arxiv.org/abs/2606.09498). arXiv:2606.09498v1. | 预印本 |
| <a id="ref-p05"></a>P05 | Lee, Y., Nair, R., Zhang, Q., Lee, K., Khattab, O., & Finn, C. (2026). [*Meta-Harness: End-to-End Optimization of Model Harnesses*](https://arxiv.org/abs/2603.28052). arXiv:2603.28052v1. | 预印本 |
| <a id="ref-p06"></a>P06 | Sabharwal, R., Wang, H., Storkey, A., & Pan, J. Z. (2026). [*Multi-Turn Evaluation of Deep Research Agents Under Process-Level Feedback*](https://arxiv.org/abs/2606.09748). SCALE-ICML 2026 workshop paper; arXiv:2606.09748v1. | Workshop paper / 预印本 |
| <a id="ref-p07"></a>P07 | Shahid, A., Suttie, G., & Black, P. (2026). [*Collaborative Human-Agent Protocol (CHAP)*](https://arxiv.org/abs/2606.09751). arXiv:2606.09751v2. | 预印本 |
| <a id="ref-p08"></a>P08 | Santillana, J. S. (2026). [*Precision Is Not Faithfulness: Coverage-Aware Evaluation of Grounded Generation with a Complete Oracle*](https://arxiv.org/abs/2606.09376). arXiv:2606.09376v2. | 预印本 |
| <a id="ref-p09"></a>P09 | Wu, Q., et al. (2023). [*AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation*](https://arxiv.org/abs/2308.08155). arXiv:2308.08155v2. | 预印本 |
| <a id="ref-p10"></a>P10 | Li, G., Hammoud, H. A. A. K., Itani, H., Khizbullin, D., & Ghanem, B. (2023). [*CAMEL: Communicative Agents for “Mind” Exploration of Large Language Model Society*](https://arxiv.org/abs/2303.17760). NeurIPS 2023; arXiv:2303.17760v2. | 同行评审论文 |
| <a id="ref-p11"></a>P11 | Hong, S., et al. (2024). [*MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework*](https://arxiv.org/abs/2308.00352). arXiv:2308.00352v7. | 学术论文 / 预印本 |
| <a id="ref-p12"></a>P12 | Min, S., Krishna, K., Lyu, X., et al. (2023). [*FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation*](https://aclanthology.org/2023.emnlp-main.741/). EMNLP 2023, 12076–12100. DOI: 10.18653/v1/2023.emnlp-main.741. | 同行评审论文 |
| <a id="ref-p13"></a>P13 | Gao, T., Yen, H., Yu, J., & Chen, D. (2023). [*Enabling Large Language Models to Generate Text with Citations*](https://arxiv.org/abs/2305.14627). EMNLP 2023; arXiv:2305.14627v2. | 同行评审论文 |
| <a id="ref-p14"></a>P14 | Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., & Zhu, C. (2023). [*G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment*](https://aclanthology.org/2023.emnlp-main.153/). EMNLP 2023, 2511–2522. DOI: 10.18653/v1/2023.emnlp-main.153. | 同行评审论文 |
| <a id="ref-p15"></a>P15 | Zheng, L., et al. (2023). [*Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*](https://arxiv.org/abs/2306.05685). NeurIPS 2023 Datasets and Benchmarks; arXiv:2306.05685v4. | 同行评审论文 |
| <a id="ref-p16"></a>P16 | Madaan, A., Tandon, N., Gupta, P., et al. (2023). [*Self-Refine: Iterative Refinement with Self-Feedback*](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html). NeurIPS 2023, Main Conference Track. | 同行评审论文 |
| <a id="ref-p17"></a>P17 | Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). [*Reflexion: Language Agents with Verbal Reinforcement Learning*](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html). NeurIPS 2023, Main Conference Track. | 同行评审论文 |
| <a id="ref-p19"></a>P19 | Kapoor, S., Stroebl, B., Siegel, Z. S., Nadgir, N., & Narayanan, A. (2024). [*AI Agents That Matter*](https://arxiv.org/abs/2407.01502). arXiv:2407.01502v1. | 预印本 |
| <a id="ref-p20"></a>P20 | Xia, Y., & Wang, T. (2026). [*ResearchLoop: An Evidence-Gated Control Plane for AI-Assisted Research*](https://arxiv.org/abs/2605.28282). arXiv:2605.28282v1. | 技术报告 / 预印本 |
| <a id="ref-p21"></a>P21 | Hu, Y., Zhang, Y., Trager, M., Zhang, Y., Yang, S., Xia, W., & Soatto, S. (2026). [*EvoMAS: Evolutionary Generation of Multi-Agent Systems*](https://arxiv.org/abs/2602.06511). ICML 2026; arXiv:2602.06511v4. | 同行评审论文 |
| <a id="ref-p22"></a>P22 | Xu, R., Qi, Z., Guo, Z., Wang, C., Wang, H., Zhang, Y., & Xu, W. (2024). [*Knowledge Conflicts for LLMs: A Survey*](https://aclanthology.org/2024.emnlp-main.486/). EMNLP 2024, 8541–8565. DOI: 10.18653/v1/2024.emnlp-main.486. | 同行评审论文 |
| <a id="ref-p23"></a>P23 | Liska, A., Kocisky, T., Gribovskaya, E., et al. (2022). [*StreamingQA: A Benchmark for Adaptation to New Knowledge over Time in Question Answering Models*](https://proceedings.mlr.press/v162/liska22a.html). ICML 2022, PMLR 162, 13604–13622. | 同行评审论文 |
| <a id="ref-p24"></a>P24 | Marjanovic, S. V., Yu, H., Atanasova, P., Maistro, M., Lioma, C., & Augenstein, I. (2024). [*DYNAMICQA: Tracing Internal Knowledge Conflicts in Language Models*](https://aclanthology.org/2024.findings-emnlp.838/). Findings of EMNLP 2024, 14346–14360. DOI: 10.18653/v1/2024.findings-emnlp.838. | 同行评审论文 |
| <a id="ref-p25"></a>P25 | Jin, Z., Cao, P., Chen, Y., et al. (2024). [*Tug-of-War between Knowledge: Exploring and Resolving Knowledge Conflicts in Retrieval-Augmented Language Models*](https://aclanthology.org/2024.lrec-main.1466/). LREC-COLING 2024, 16867–16878. | 同行评审论文 |
| <a id="ref-p26"></a>P26 | Liu, S., Ning, Q., Halder, K., et al. (2025). [*Open Domain Question Answering with Conflicting Contexts*](https://aclanthology.org/2025.findings-naacl.99/). Findings of NAACL 2025, 1838–1854. DOI: 10.18653/v1/2025.findings-naacl.99. | 同行评审论文 |
| <a id="ref-p27"></a>P27 | Wallat, J., Nejdl, W., & Sikdar, S. (2026). [*When Facts Change: Temporal Knowledge Conflict Resolution in LLMs*](https://aclanthology.org/2026.findings-acl.103/). Findings of ACL 2026, 2154–2184. DOI: 10.18653/v1/2026.findings-acl.103. | 同行评审论文 |
| <a id="ref-p28"></a>P28 | Pham, Q. H., Ngo, H., Luu, A. T., & Nguyen, D. Q. (2024). [*Who’s Who: Large Language Models Meet Knowledge Conflicts in Practice*](https://aclanthology.org/2024.findings-emnlp.593/). Findings of EMNLP 2024, 10142–10151. DOI: 10.18653/v1/2024.findings-emnlp.593. | 同行评审论文 |
| <a id="ref-p29"></a>P29 | Li, M., Zhang, H., Fan, H., Ding, J., & Feng, Y. (2026). [*Harmful Factuality: LLMs Correcting What They Shouldn’t*](https://aclanthology.org/2026.findings-eacl.46/). Findings of EACL 2026, 896–912. DOI: 10.18653/v1/2026.findings-eacl.46. | 同行评审论文 |
| <a id="ref-p30"></a>P30 | Dhingra, B., Cole, J. R., Eisenschlos, J. M., Gillick, D., Eisenstein, J., & Cohen, W. W. (2022). [*Time-Aware Language Models as Temporal Knowledge Bases*](https://aclanthology.org/2022.tacl-1.15/). TACL 10, 257–273. DOI: 10.1162/tacl_a_00459. | 同行评审期刊论文 |
| <a id="ref-p31"></a>P31 | Vu, T., Iyyer, M., Wang, X., et al. (2024). [*FreshLLMs: Refreshing Large Language Models with Search Engine Augmentation*](https://aclanthology.org/2024.findings-acl.813/). Findings of ACL 2024, 13697–13720. DOI: 10.18653/v1/2024.findings-acl.813. | 同行评审论文 |
| <a id="ref-p32"></a>P32 | Wang, F., Wan, X., Sun, R., Chen, J., & Arik, S. O. (2025). [*Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts for Large Language Models*](https://aclanthology.org/2025.acl-long.1476/). ACL 2025, 30553–30571. DOI: 10.18653/v1/2025.acl-long.1476. | 同行评审论文 |
| <a id="ref-p33"></a>P33 | Pan, R., Cao, B., Lin, H., et al. (2024). [*Not All Contexts Are Equal: Teaching LLMs Credibility-aware Generation*](https://aclanthology.org/2024.emnlp-main.1109/). EMNLP 2024, 19844–19863. DOI: 10.18653/v1/2024.emnlp-main.1109. | 同行评审论文 |
| <a id="ref-p34"></a>P34 | Reddy, V., & Challaram, S. (2026). [*Don’t Ask the LLM to Track Freshness: A Deterministic Recipe for Memory Conflict Resolution*](https://arxiv.org/abs/2606.01435). arXiv:2606.01435v1. | 预印本；未标注同行评审 |
| <a id="ref-p35"></a>P35 | Wang, C., Li, Y., Liu, Y., & Shu, Y. (2026). [*ConflictRAG: Detecting and Resolving Knowledge Conflicts in Retrieval-Augmented Generation*](https://arxiv.org/abs/2605.17301). arXiv:2605.17301v2；投稿 IEEE SMC 2026。 | 预印本；未标注录用 |
| <a id="ref-t01"></a>T01 | van der Aalst, W. M. P., ter Hofstede, A. H. M., Kiepuszewski, B., & Barros, A. P. (2003). [*Workflow Patterns*](https://doi.org/10.1023/A:1022883727209). *Distributed and Parallel Databases*, 14, 5–51. | 同行评审期刊论文 |
| <a id="ref-t02"></a>T02 | Nii, H. P. (1986). [*The Blackboard Model of Problem Solving and the Evolution of Blackboard Architectures*](https://doi.org/10.1609/aimag.v7i2.537). *AI Magazine*, 7(2). | 同行评审期刊论文 |
| <a id="ref-t03"></a>T03 | Meyer, B. (1992). [*Applying “Design by Contract”*](https://ieeexplore.ieee.org/document/161279/). *Computer*, 25(10). | 同行评审期刊论文 |
| <a id="ref-t06"></a>T06 | Moreau, L., & Missier, P. (Eds.). (2013). [*PROV-DM: The PROV Data Model*](https://www.w3.org/TR/prov-dm/). W3C Recommendation, 30 April 2013. | 技术标准 |
| <a id="ref-e01"></a>E01 | Srinivasan, A., Shamdasani, S., Araujo, A. F., & de Wasseige, J.; OpenAI & Thrive Holdings. (2026, May 27). [*Building Self-Improving Tax Agents with Codex*](https://openai.com/index/building-self-improving-tax-agents-with-codex/). | 一手工程案例 |
| <a id="ref-e10"></a>E10 | Anthropic. (2026, January 23). [*Building Multi-Agent Systems: When and How to Use Them*](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them). | 一手工程文章 |
| <a id="ref-e19"></a>E19 | Weng, L. (2026, July 4). [*Harness Engineering for Self-Improvement*](https://lilianweng.github.io/posts/2026-07-04-harness/). | 研究综述 / 技术文章 |
| <a id="ref-e20"></a>E20 | Osmani, A. (2026, June 8). [*Loop Engineering*](https://addyo.substack.com/p/loop-engineering). | 工程文章 |
| <a id="ref-e21"></a>E21 | Anthropic. (2024, December 19). [*Building Effective Agents*](https://www.anthropic.com/engineering/building-effective-agents). | 一手工程文章 |
| <a id="ref-e22"></a>E22 | Nous Research. (n.d.). [*Persistent Memory — Hermes Agent*](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory). Accessed 2026-07-14. | 项目文档 |

## 附录 H：问题候选边界（未交付）

问题候选系统尚未交付。本报告只保留产品边界：未来若实现，该机制必须遵守现有的确定性控制面、冻结工件、单写者、结构闭合和人工裁决原则，且不得赋予智能体自我批准或发布权威。

本报告不定义其字段、schema、类别、状态机、迁移或失败分类。任何具体合同都应在实际实现时由对应的权威所有者、验证器、测试和现行文档共同建立。

## 附录 I：历史标识隔离

BriefLoop 是唯一现行项目名。旧命令、模块路径、工作空间 schema、归档实验 ID 和历史文件名只因兼容或复现需要而存在；具体字面量及允许出现的位置，以 `docs/briefloop-naming.md` 为准。

这些字面量不得用于当前标题、项目介绍、架构名称、推荐命令或对外品牌表达，也不得被用来暗示存在两个并列项目名。冻结归档和历史 ID 不应被原地重写；未来若迁移技术标识，必须提供兼容层和迁移测试。

---

*BriefLoop 架构参考 v0.6.1。代码快照 `main@47ae439d0206a852a2a223db4051d28f39b54c38`（版本文件 v0.12.1；含 tag 后 #513—#515），2026-07-19。*
