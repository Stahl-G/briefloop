# 公开路线图

这是 BriefLoop（原 MABW — Multi-Agent Brief Workflow）的公开路线图。它只描述产品方向和版本目标。详细实现计划、schema 草案、prompt notes、私有评测样例和商业化场景设计，在代码稳定前不放进公开仓库。

## 方向

BriefLoop 的下一阶段是一个由司乐师（Orchestrator）管理、由契约（contracts）约束、可审计的简报工作流：

```text
subagent-first runtime
→ orchestrator contracts
→ feedback and repair loop
→ checkpointed quality gates and evaluation
→ workspace memory and control switchboard
→ provenance-aware artifacts
→ policy packs and runtime parity
→ stable v0.11.0 product baseline
```

v0.11.0 产品基线前不优先重建完整分布式 multi-agent runtime。Python 继续作为 setup、source handling、validation、audit、rendering 工具箱；workflow runtime 由外部 main agent 和 delegated subagents 执行。

后续阶段遵循两个设计原则：

- Stage boundary 就是契约 boundary。部分 gate 只需要机器检查，部分 gate 需要人类语义确认，部分 gate 由机器 findings 加司乐师判断共同决定。
- Memory 是 workspace-local 且 human-governed。项目可以加入 agent-proposed memory updates 和 frozen per-run snapshots，但 v0.11.0 产品基线前不做完整 long-term-memory 或 RAG platform。

## 已完成基线

### v0.5.7

- `multi-agent-brief run` 已改为 运行交接单 launcher，而不是 Python brief generator。
- 标准流程转为由外部 subagents 完成来源抽取、筛选、事实账本、起草、编辑、审计和格式化。
- Hermes 成为 primary runtime path，支持定时和 delegated brief workflow。
- Input governance 已区分 evidence、feedback、instructions 和 context。

### v0.5.8

- 标准路径中已清理旧 Python pipeline 叙事。
- 支持矩阵、release checks 和版本一致性 workflow 已清理。
- 安装方式和 runtime 支持边界已明确。

### v0.6.0

- 已建立共享 司乐师 authority、decision vocabulary、契约引用 和 runtime role parity。
- 运行交接单 artifacts 已将所有支持的 runtime 指向同一套 司乐师 control model。
- 契约引用 已随 Python distribution 打包，非 editable 安装后也能运行 `run`。
- 持久化 runtime state、artifact registry 执行、feedback repair 和 溯源图 当时仍属于后续 v0.6 milestone。

### v0.6.1

- 已为 司乐师 handoff run 增加最小 runtime state control files。
- Artifact registry 现在记录最小文件状态，但不执行 workflow stages。
- Stage-scoped blocking 区分尚未轮到的下游 artifacts 和真正阻断当前 stage 的 artifacts。
- `state init`、`state check`、`state show`、`state decide` 提供 runtime inspection 和 decision recording 入口。
- 自动 repair execution 和 溯源图 仍属于后续 v0.6 milestone。

### v0.6.2

- 已加入 feedback issue handling 和 bounded repair planning，但 Python 不变成 repair executor。
- `feedback ingest`、`feedback plan`、`feedback resolve`、`feedback show`、`feedback validate` 提供 human feedback 和 audit findings 的 CLI 入口。
- Feedback 和 repair control artifacts 已被追踪，但公开 roadmap 不展开内部 repair artifact 名称。
- Feedback blocking 只作用于当前 stage，repair decision 仍通过 司乐师 decision vocabulary。
- Python 不自动修改 brief artifacts、不执行 repair，也不判断语义修复是否已经完成。

### v0.6.3

- 已为 auditable artifacts 加入 deterministic material-fact、freshness 和 target-relevance gates。
- `gates check`、`gates show` 和 `gates validate` 提供 `output/intermediate/gates/` 下 stage-scoped quality gate reports 的 CLI 入口；`quality_gate_report.json` 保留为 latest/legacy projection。
- 质量门禁 blocking 只作用于当前 stage，并以明确 blocking 语义为准，不把所有 high severity finding 默认当成 runtime stop。
- Python 不会 live-fetch market data、自动 recrawl source、改稿、执行 repair 或做 semantic truth judgment。

### v0.6.4

- 已加入 packaged public-safe evaluation cases，用于开发和 CI 回归验证。
- `eval-cases list`、`eval-cases validate`、`eval-cases run` 提供 gates、feedback、runtime blocker 和 Hermes static invariant cases 的 CLI 入口。
- Evaluation cases 使用 structured allowlisted actions，不执行 shell 字符串。
- Evaluation outputs 只属于 developer/CI results，不加入 workflow 产物契约。
- Python 不会在 eval cases 中给文章打分、调用 LLM judge、执行 repair、运行 subagents 或抓取来源。

### v0.6.5

- 已加入可选 deterministic 溯源投影，用于 workspace audit/debug review。
- `provenance build`、`provenance show` 和 `provenance validate` 提供 `provenance_graph.json` 的 CLI 入口。
- 该 graph 投影已有 runtime state、artifact registry、event log、事实账本、feedback、repair 和 质量门禁 control files。
- Provenance edges 使用 citation/control wording，不声称 source 已经语义证明 claim。
- Python 不会在 溯源投影 中执行 workflow stages、抓取来源、replay DAG、执行 repair、验证语义真伪，默认也不会阻断 `finalize`。

### v0.6.6

- 已加入 workspace-local audience taste profile，作为 runtime context surface。
- `audience_profile.md` 位于 workspace root，可由人工编辑。
- `run`、`start` 和 `handoff` 会创建或复用 frozen per-run `output/intermediate/audience_profile_snapshot.md`。
- 交接单 JSON/Markdown 用独立 `audience_memory_files` 暴露该 context，不混入 expected artifacts 或 control files。
- Python 不会把 读者画像 内容当作 source evidence、产物契约、质量门禁、溯源图 expansion、自动学习能力或长期记忆系统。

### v0.6.7

- 已加入 司乐师 控制台，作为 runtime control surface。
- `run`、`start` 和 `handoff` 会创建 `output/intermediate/orchestrator_control_switchboard.json`。
- `controls build-switchboard`、`controls show`、`controls select` 和 `controls validate` 提供 recommendations 和 司乐师 selections 的 CLI 入口。
- `control_selections.json` 只在 司乐师 显式选择后记录 enable/defer/reject。
- Selection 不是 execution：Python 不会自动运行 gates、feedback planning、溯源投影、source discovery、repair 或 subagents。

### v0.6.8

- 已在 finalize 阶段加入 reader-facing source appendix 生成。
- `source_appendix` 是当前 output format 名称；旧 `source_map` 作为兼容 alias 保留。
- Reader-facing 来源列表会追加到 `output/delivery/` 下的交付 Markdown/DOCX 末尾，同时 `output/source_appendix.md` 作为 audit/control copy 保留。它只来自 `output/intermediate/audited_brief.md` 实际引用、并可通过 `output/intermediate/claim_ledger.json` 解析的 claims。
- Reader-facing output 不应暴露 raw claim IDs、source IDs、evidence text、本地路径或 `file://` URL。
- Appendix 不是 source evidence、semantic proof、runtime state file、溯源图 或 workflow gate。

### v0.6.9

- 在进入 v0.7 improvement-proposal 工作前，先稳定 install/runtime asset parity。
- Package install 包含 Python CLI、packaged 契约、policy packs 和 packaged public-safe eval fixtures。
- `.agents/`、`.claude/`、`.codex/`、`.opencode/` 和 `integrations/hermes-plugin/` 等 runtime source directories 明确为 source-clone-only，除非复制到 workspace。
- `multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|codex|all` 可以从 source clone 安装 workspace-local OpenCode/Claude Code runtime kits 和 Experimental Codex custom-agent assets。
- v0.6.9 不新增 FrictionStore、improvement proposal commands、policy-pack authoring 或自动 workflow execution。

### v0.7.0

- 已实现 Improvement Ledger lifecycle commands：`improve propose/list/show/approve/reject/revert/stats/validate/rebuild`。
- 人工撰写、人工批准的读者偏好可以保存在 `improvement/ledger.jsonl`。
- Approved 且可物化的 guidance 会投影到 `improvement/memory.md`，并在每次 run 冻结为 `output/intermediate/improvement_memory_snapshot.md`。
- Runtime handoff 只暴露 frozen snapshot，不暴露 live `improvement/memory.md`。
- `runtime_manifest.json.improvement` 记录当前 run 的 `ledger_sha256`、`memory_sha256`、`snapshot_path`、`snapshot_sha256` 和 `materialized_entry_ids`。
- Public-safe eval cases 已验证 unapproved、approved 和 reverted improvement entries 的控制行为。
- v0.7.0 不新增 FrictionStore、autonomous learning、retrieval memory、runtime-specific guidance filtering、output-quality validation、ledger compaction、policy-pack authoring 或 automatic workflow execution。

## 下一阶段

### v0.5.9 — Roadmap Privacy And Architecture Status

目标：保留有用的公开路线图，同时把详细实现计划移出公开仓库。

公开范围：

- 将 roadmap 简化到版本目标和模块边界。
- 增加当前架构状态说明，帮助 contributor 区分已实现能力和未来目标。
- 增加迁移说明，解释从旧 Python-pipeline 叙事到 司乐师-first 架构的变化。
- 增加内部规划文件的 ignore 规则。

Non-goals:

- 不改变 runtime 行为。
- 不新增 schema。
- 不新增 source providers。
- 不重写 prompt 或 agent role。

### v0.6 — 司乐师 契约 And Feedback Loop

目标：先明确 main agent，再尽早展示“产出 -> 反馈 -> 有界修复”的闭环价值。司乐师 应负责协调 specialist subagents、验证 交接产物、接收反馈、路由修复，并在不安全时阻断流程。

公开范围：

- 定义 司乐师 的高层职责。
- 定义四类公开 契约：
  - Behavior
  - Process / Artifact
  - Fact-Grounding / Evidence
  - Quality / Audience
- 建立最小 runtime state 和 artifact status 层。
- 在扩展更深 溯源 前，先引入 feedback and repair loop。
- 增加 material facts、source freshness 和 target relevance 相关质量门。
- 在影响 司乐师 决策的地方，标明 stage gate 是 machine-only、human-in-the-loop 还是 mixed。
- 从真实失败模式抽象 public-safe evaluation cases。
- 将 溯源投影 保持为 audit/debug tooling，semantic proof、replay 和 graph-database style query systems 后移。
- 保持 Python 作为 tools、validators、renderers，而不是 workflow runtime。

v0.7.0 之后的公开顺序转向 FrictionStore、policy packs、reference workflows 和 runtime parity，同时继续保持 subagent-first runtime boundary。

公开实施概览：

- [Implementation overview index](implementation/README.md)
- [v0.5.9 司乐师 Contract Preparation](implementation/v0.5.9-orchestrator-prep.md)
- [v0.6.0 Explicit 司乐师 Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)

Non-goals:

- 不实现完整 DAG runtime。
- 不一次性重写所有 agents。
- 不重做 final report rendering。
- 不扩张新的 search providers。

### v0.7 — Improvement Ledger And Controlled Memory

目标：把有边界、有证据、人类把关的读者偏好保存为可审计的 workspace memory，但不把它做成自动学习系统。

v0.7.0 已实现：

- Improvement Ledger lifecycle。
- Approved guidance materialization 到 deterministic memory projection。
- 通过 handoff 暴露 frozen per-run Improvement Memory snapshot。
- 用 public-safe eval cases 验证 Improvement Memory 控制行为。

Deferred：

- FrictionStore 和自动 recurring-failure detection。
- Autonomous learning。
- Retrieval memory 或 RAG platform behavior。
- Runtime-specific guidance filtering。
- Improvement guidance 的 output-quality validation。
- Ledger compaction。
- Policy-pack-driven memory routing。

Non-goals:

- 不公开私有 golden examples。
- 不允许自动修改 main branch。
- 不把 raw prompt、raw log 或 private feedback 注入公开 prompts。
- 不做完整 RAG platform 或 autonomous long-term-memory system。

### v0.8 — Measurement, Fast Rerun, Role Topology, And Evaluation

目标：先让 runtime trace 可测，再让同一事实层的重跑更便宜，随后在不削弱 accountable artifacts 的前提下收敛默认角色拓扑，并定义 approved guidance 是否真正体现、是否造成回归的第一版评估协议。

公开范围：

- 新增 run-integrity 和 timing surfaces，让 runtime trace 能区分 clean、incomplete 和 contaminated run，避免在污染轨迹上做性能主张。
- 规划 fast-rerun 基础设施：基于 hash-verified same-evidence rewrite，但不跳过 writer、auditor、gates、finalize-complete、human delivery 或 archive。
- 收敛默认 role topology，同时保留同一套 accountable artifacts，包括 candidate claims、screened candidates、Claim Ledger、audit report、gate reports 和 delivery bundle。
- policy-pack / recipe 工作先保持最小化：只支撑 fast-rerun 与 default/strict topology 选择，不先展开完整 mode registry。
- 保持单一公开 support matrix。
- 定义 guidance manifestation 和 guidance regression 评估口径，用真实 runtime traces 观察 approved guidance 是否被体现、是否造成事实或表达回归。`origin_runtime` 只用于分析，不用于 runtime filtering 或 routing。

Non-goals:

- 商业 policy-pack 内部规则稳定前不公开。
- 最小 recipe / pack surface 被证明前，不展开完整 mode registry。
- 不让 runtime-specific 细节分叉业务 artifact schema。
- 不为 GUI 或 messaging 入口另写一套简化 pipeline。
- 不做 `lite mode`，不做跳过 gates 的 fast path，不做部分 fact-layer import。
- v0.8 协议实际执行前，不声称 v0.7 Improvement Memory 已经改善输出质量。

### v0.9 — Support Sufficiency Core

目标：从 source-level traceability 推进到最低可用的 support-sufficiency core，同时保留现有 MABW 兼容面。

公开范围：

- 使用 BriefLoop 作为公开项目名，同时保留 MABW 作为历史实现名和兼容面。
- 实现最低 support-sufficiency 路径：
  - Atomic Claim Graph
  - Evidence Span Registry
  - Claim-Support Matrix
- 保持 Semantic Assessment Report 为 proposal-only 实验面：semantic assessment 可以提出 support labels、uncertainty、disagreement 和 adjudication needs，但不能修改 Claim-Support Matrix、创建 adjudication queue items、阻断 delivery、决定 release eligibility 或证明真理。
- v0.9 兼容期内保留 `multi-agent-brief`、`briefloop` shell alias、`/briefloop`、`/mabw`、Python package/module paths、artifact names、workspace formats 和 MABW experiment IDs。
- 对不稳定能力继续标注 experimental、interface-only 或 CLI-only。

延后处理的 semantic-governance surfaces：

- human adjudication
- coverage and omission gates
- semantic regression
- release eligibility
- quality packs
- finding-to-repair workflows

这些不再是默认下一阶段实现主线。等 product layer 拥有稳定 report contracts 和真实用户路径之后，再决定是否重开。

### v0.10 — Product OS And Report Packs

目标：在不削弱问责主链的前提下，把 support-sufficiency core 包装成可用的 recurring-report product layer。

公开范围：

- 新增 ReportSpec 和 ReportPack 契约，让 BriefLoop 知道正在生产哪类报告。
- 引入初始 report packs，例如 `market_weekly`、`management_monthly`，以及后续 `evidence_extract`。
- 改善 zero-config workspace 创建，同时保留 `multi-agent-brief` 作为稳定 engine CLI，保留 `briefloop` 作为 shell alias，保留 `/briefloop` 作为 writer command，保留 `/mabw` 作为兼容 writer command。
- 把 reader-facing delivery bundle 和 audit/control bundle 分成 export/projection 层，但不静默移动或删除现有控制 artifacts。
- 本地文件和简单来源配置继续是一等路径；大范围 connector 和 UI 工作放到后面。
- 为内部 review workflow 增加 release modes 和 human approval records，但不声称外部发布授权。

Non-goals：

- 不做 SaaS-first product。
- CLI product path 跑通前，不做 heavy UI。
- 不声称 IR/disclosure readiness。
- 不允许 report pack 绕过 Claim Ledger、gates、event log、archive、reader-final gate、source appendix 或 human delivery。
- 不混淆 `/briefloop` writer command 和 BriefLoop skill operator protocol surface。
- 不做自动 external publication 或 public release command。

### v0.11.0 — Stable Weekly/Monthly Brief Product

目标：冻结一个 modest、local-first、file-state-driven、契约治理 的 recurring business reports CLI product。

v0.11.0 应包含：

- 面向产品语言的 zero-config entries，例如 `briefloop new industry-weekly`
  和 `briefloop new management-monthly`，同时内部继续使用
  `market_weekly`、`management_monthly` 等 canonical ReportPack ids。
- 一个映射到内部 `evidence_extract` report pack 的 `document-review`
  入口，用于 page/span-cited document work。
- 本地文件优先的 report loop。
- 稳定 Markdown 和 DOCX 输出。
- 保留 Claim Ledger、source appendix、gates、event log、support records 和 archive surfaces。
- 稳定 ReportSpec 和 ReportPack 契约。
- 至少三个 report packs。
- 明确 delivery/audit bundle separation。
- 显式 human delivery。
- 无 force-deliver path。
- 清晰 runtime dependence、support status 和 non-goals。

## 研究轨道

v0.12.0 可以在 CLI product path 跑通后增加 local Studio preview。Studio 必须调用现有 CLI/service transactions，不得直接修改 frozen artifacts，也不得提供 force-deliver path。

v0.13.0+ 可以增加 IR/disclosure support packs，但它们是 review-support surfaces，不是 publication automation。这些 pack 可以标记 forward-looking statements、materiality review items、KPI consistency issues 和 evidence-annex gaps，但不能声称自动判断 materiality、自动生成 SEC-ready filing，或替代律师、审计师、IR 负责人和 disclosure committee。

v2.0 是未来研究轨道，不是短期产品承诺。product baseline 稳定后，项目可以探索更正式的 multi-agent runtime，包括 shared state、task boards、replay 和更丰富的 coordination protocols。

v0.11.0 产品基线前不优先做：

- distributed multi-server orchestration。
- enterprise multi-tenancy。
- 完整 long-term memory 或 RAG platform。
- 自动 main-branch self-modification。
- 为扩张而扩张的 connector 增加。

## 规划保密边界

公开 roadmap 不应包含详细 schema 草案、完整 契约 示例、私有 golden cases、商业场景设计、private prompt notes 或 failure taxonomies。这些内容应放在被 ignore 的内部规划文件中，等实现稳定且适合公开后再逐步发布。
