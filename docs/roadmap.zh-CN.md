# 公开路线图

这是 Multi-Agent Brief Workflow 的公开路线图。它只描述产品方向和版本目标。详细实现计划、schema 草案、prompt notes、私有评测样例和商业化场景设计，在代码稳定前不放进公开仓库。

## 方向

Multi-Agent Brief Workflow 的下一阶段是一个由 Orchestrator 管理、由 contracts 约束、可审计的简报工作流：

```text
subagent-first runtime
→ orchestrator contracts
→ feedback and repair loop
→ checkpointed quality gates and evaluation
→ workspace memory and mode registry
→ provenance-aware artifacts
→ policy packs and runtime parity
→ stable v1.0 baseline
```

v1.0 前不优先重建完整分布式 multi-agent runtime。Python 继续作为 setup、source handling、validation、audit、rendering 工具箱；workflow runtime 由外部 main agent 和 delegated subagents 执行。

后续阶段遵循两个设计原则：

- Stage boundary 就是 contract boundary。部分 gate 只需要机器检查，部分 gate 需要人类语义确认，部分 gate 由机器 findings 加 Orchestrator 判断共同决定。
- Memory 是 workspace-local 且 human-governed。项目可以加入 agent-proposed memory updates 和 frozen per-run snapshots，但 v1.0 前不做完整 long-term-memory 或 RAG platform。

## 已完成基线

### v0.5.7

- `multi-agent-brief run` 已改为 runtime handoff launcher，而不是 Python brief generator。
- 标准流程转为由外部 subagents 完成来源抽取、筛选、Claim Ledger、起草、编辑、审计和格式化。
- Hermes 成为 primary runtime path，支持定时和 delegated brief workflow。
- Input governance 已区分 evidence、feedback、instructions 和 context。

### v0.5.8

- 标准路径中已清理旧 Python pipeline 叙事。
- 支持矩阵、release checks 和版本一致性 workflow 已清理。
- 安装方式和 runtime 支持边界已明确。

### v0.6.0

- 已建立共享 Orchestrator authority、decision vocabulary、contract references 和 runtime role parity。
- Runtime handoff artifacts 已将所有支持的 runtime 指向同一套 Orchestrator control model。
- Contract references 已随 Python distribution 打包，非 editable 安装后也能运行 `run`。
- 持久化 runtime state、artifact registry 执行、feedback repair 和 provenance graph 当时仍属于后续 v0.6 milestone。

### v0.6.1

- 已为 Orchestrator handoff run 增加最小 runtime state control files。
- Artifact registry 现在记录最小文件状态，但不执行 workflow stages。
- Stage-scoped blocking 区分尚未轮到的下游 artifacts 和真正阻断当前 stage 的 artifacts。
- `state init`、`state check`、`state show`、`state decide` 提供 runtime inspection 和 decision recording 入口。
- 自动 repair execution 和 provenance graph 仍属于后续 v0.6 milestone。

### v0.6.2

- 已加入 feedback issue handling 和 bounded repair planning，但 Python 不变成 repair executor。
- `feedback ingest`、`feedback plan`、`feedback resolve`、`feedback show`、`feedback validate` 提供 human feedback 和 audit findings 的 CLI 入口。
- Feedback 和 repair control artifacts 已被追踪，但公开 roadmap 不展开内部 repair artifact 名称。
- Feedback blocking 只作用于当前 stage，repair decision 仍通过 Orchestrator decision vocabulary。
- Python 不自动修改 brief artifacts、不执行 repair，也不判断语义修复是否已经完成。

### v0.6.3

- 已为 auditable artifacts 加入 deterministic material-fact、freshness 和 target-relevance gates。
- `gates check`、`gates show` 和 `gates validate` 提供 `quality_gate_report.json` 的 CLI 入口。
- Quality gate blocking 只作用于当前 stage，并以明确 blocking 语义为准，不把所有 high severity finding 默认当成 runtime stop。
- Python 不会 live-fetch market data、自动 recrawl source、改稿、执行 repair 或做 semantic truth judgment。

### v0.6.4

- 已加入 packaged public-safe evaluation cases，用于开发和 CI 回归验证。
- `eval-cases list`、`eval-cases validate`、`eval-cases run` 提供 gates、feedback、runtime blocker 和 Hermes static invariant cases 的 CLI 入口。
- Evaluation cases 使用 structured allowlisted actions，不执行 shell 字符串。
- Evaluation outputs 只属于 developer/CI results，不加入 workflow artifact contracts。
- Python 不会在 eval cases 中给文章打分、调用 LLM judge、执行 repair、运行 subagents 或抓取来源。

### v0.6.5

- 已加入可选 deterministic provenance projection，用于 workspace audit/debug review。
- `provenance build`、`provenance show` 和 `provenance validate` 提供 `provenance_graph.json` 的 CLI 入口。
- 该 graph 投影已有 runtime state、artifact registry、event log、Claim Ledger、feedback、repair 和 quality gate control files。
- Provenance edges 使用 citation/control wording，不声称 source 已经语义证明 claim。
- Python 不会在 provenance projection 中执行 workflow stages、抓取来源、replay DAG、执行 repair、验证语义真伪，默认也不会阻断 `finalize`。

### v0.6.6

- 已加入 workspace-local audience taste profile，作为 runtime context surface。
- `audience_profile.md` 位于 workspace root，可由人工编辑。
- `run`、`start` 和 `handoff` 会创建或复用 frozen per-run `output/intermediate/audience_profile_snapshot.md`。
- Handoff JSON/Markdown 用独立 `audience_memory_files` 暴露该 context，不混入 expected artifacts 或 control files。
- Python 不会把 audience profile 内容当作 source evidence、artifact contract、quality gate、provenance graph expansion、自动学习能力或长期记忆系统。

## 下一阶段

### v0.5.9 — Roadmap Privacy And Architecture Status

目标：保留有用的公开路线图，同时把详细实现计划移出公开仓库。

公开范围：

- 将 roadmap 简化到版本目标和模块边界。
- 增加当前架构状态说明，帮助 contributor 区分已实现能力和未来目标。
- 增加迁移说明，解释从旧 Python-pipeline 叙事到 Orchestrator-first 架构的变化。
- 增加内部规划文件的 ignore 规则。

Non-goals:

- 不改变 runtime 行为。
- 不新增 schema。
- 不新增 source providers。
- 不重写 prompt 或 agent role。

### v0.6 — Orchestrator Contracts And Feedback Loop

目标：先明确 main agent，再尽早展示“产出 -> 反馈 -> 有界修复”的闭环价值。Orchestrator 应负责协调 specialist subagents、验证 handoff artifacts、接收反馈、路由修复，并在不安全时阻断流程。

公开范围：

- 定义 Orchestrator 的高层职责。
- 定义四类公开 contract：
  - Behavior
  - Process / Artifact
  - Fact-Grounding / Evidence
  - Quality / Audience
- 建立最小 runtime state 和 artifact status 层。
- 在扩展更深 provenance 前，先引入 feedback and repair loop。
- 增加 material facts、source freshness 和 target relevance 相关质量门。
- 在影响 Orchestrator 决策的地方，标明 stage gate 是 machine-only、human-in-the-loop 还是 mixed。
- 从真实失败模式抽象 public-safe evaluation cases。
- 将 provenance projection 保持为 audit/debug tooling，semantic proof、replay 和 graph-database style query systems 后移。
- 保持 Python 作为 tools、validators、renderers，而不是 workflow runtime。

v0.6.6 之后的公开顺序转向 FrictionStore、improvement proposals、policy packs 和 runtime parity，同时继续保持 subagent-first runtime boundary。

公开实施概览：

- [Implementation overview index](implementation/README.md)
- [v0.5.9 Orchestrator Contract Preparation](implementation/v0.5.9-orchestrator-prep.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)

Non-goals:

- 不实现完整 DAG runtime。
- 不一次性重写所有 agents。
- 不重做 final report rendering。
- 不扩张新的 search providers。

### v0.7 — FrictionStore And Improvement Proposals

目标：把 recurring failures、audit findings、human feedback 和 workspace memory signals 转成受控 improvement proposals。

公开范围：

- 跨 run 跟踪 recurring failure patterns。
- 生成 improvement signals、patch plans 和 regression-plan suggestions。
- 加入轻量 workspace memory model，用于 audience taste 和 recurring feedback patterns。
- memory updates 只能是 agent-proposed、human-approved。
- 使用 frozen per-run memory snapshots，避免同一次 run 因中途写入的新 memory 改变行为。
- self-improvement 在人类或 maintainer 批准前只生成 proposal，不自动改代码。

Non-goals:

- 不公开私有 golden examples。
- 不允许自动修改 main branch。
- 不把 raw prompt、raw log 或 private feedback 注入公开 prompts。
- 不做完整 RAG platform 或 autonomous long-term-memory system。

### v0.8 — Mode Registry, Policy Packs, And Runtime Parity

目标：用 configurable policy packs 和 mode registry 支持不同简报场景与入口，同时保持不同 runtime 的行为一致。

公开范围：

- 引入 mode registry，让同一套 Orchestrator 和 specialist roles 支持 full run、source-readiness check、audit-only、repair-planning-only、audience-profile update 和 final-render-only 等入口。
- 引入 audience、industry、cadence、delivery expectations 相关的 policy-pack 概念。
- 让 Hermes、Claude Code、Codex、OpenCode 和 manual fallback 对齐到同一组 artifact expectations。
- 保证 CLI、Hermes GUI/plugin 和其他 runtime 入口都基于同一套 Orchestrator contracts 和 state files。
- 保持单一公开 support matrix。

Non-goals:

- 商业 policy-pack 内部规则稳定前不公开。
- 不让 runtime-specific 细节分叉业务 artifact schema。
- 不为 GUI 或 messaging 入口另写一套简化 pipeline。

### v0.9 — Distribution And Reference Workflows

目标：降低新用户安装和试跑 demo workflow 的门槛。

公开范围：

- 改进 package assets、install checks 和 runtime setup diagnostics。
- 提供 public-safe reference workflows。
- 对不稳定能力继续标注 experimental、interface-only 或 CLI-only。

### v1.0 — Stable Orchestrated Brief Workflow

目标：冻结一个 local-first、file-state-driven、contract-governed 的稳定简报工作流基线。

v1.0 应包含：

- 清晰的 Orchestrator-first workflow。
- 可审计 artifacts。
- evidence-aware drafting 和 audit gates。
- 带明确 machine、human 或 mixed gate 语义的 checkpointed stage transitions。
- 区分 correctness contracts 和 taste preferences 的 workspace-local memory。
- 面向常见 brief workflow 入口的 public-safe mode registry。
- supported agent surfaces 的 runtime parity。
- public-safe evaluation coverage。
- 可靠的 rendered outputs。
- 清晰的 support 和 security boundaries。

## 研究轨道

v2.0 是未来研究轨道，不是短期产品承诺。v1.0 后，项目可以探索更正式的 multi-agent runtime，包括 shared state、task boards、replay 和更丰富的 coordination protocols。

v1.0 前不优先做：

- distributed multi-server orchestration。
- enterprise multi-tenancy。
- 完整 long-term memory 或 RAG platform。
- 自动 main-branch self-modification。
- 为扩张而扩张的 connector 增加。

## 规划保密边界

公开 roadmap 不应包含详细 schema 草案、完整 contract 示例、私有 golden cases、商业场景设计、private prompt notes 或 failure taxonomies。这些内容应放在被 ignore 的内部规划文件中，等实现稳定且适合公开后再逐步发布。
