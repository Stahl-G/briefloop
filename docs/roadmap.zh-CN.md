# 公开路线图

这是 Multi-Agent Brief Workflow 的公开路线图。它只描述产品方向和版本目标。详细实现计划、schema 草案、prompt notes、私有评测样例和商业化场景设计，在代码稳定前不放进公开仓库。

## 方向

Multi-Agent Brief Workflow 的下一阶段是一个由 Orchestrator 管理、由 contracts 约束、可审计的简报工作流：

```text
subagent-first runtime
→ orchestrator contracts
→ provenance-aware artifacts
→ quality evaluation
→ policy packs and runtime parity
→ stable v1.0 baseline
```

v1.0 前不优先重建完整分布式 multi-agent runtime。Python 继续作为 setup、source handling、validation、audit、rendering 工具箱；workflow runtime 由外部 main agent 和 delegated subagents 执行。

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

### v0.6 — Orchestrator Contracts

目标：明确 main agent。Orchestrator 应负责协调 specialist subagents、验证 handoff artifacts，并在不安全时阻断流程。

公开范围：

- 定义 Orchestrator 的高层职责。
- 定义四类公开 contract：
  - Behavior
  - Process / Artifact
  - Fact-Grounding / Evidence
  - Quality / Audience
- 保持 Python 作为 tools、validators、renderers，而不是 workflow runtime。

Non-goals:

- 不实现完整 DAG runtime。
- 不一次性重写所有 agents。
- 不重做 final report rendering。
- 不扩张新的 search providers。

### v0.7 — Evaluation And Feedback Loop

目标：让质量回归可见，但不要求 LLM 输出逐字一致。

公开范围：

- 引入 public-safe evaluation cases。
- 跟踪 contract compliance、citation retention、artifact presence 和 delivery readiness。
- 将重复失败转成结构化 improvement signals。

Non-goals:

- 不公开私有 golden examples。
- 不允许自动修改 main branch。
- 不把 raw prompt、raw log 或 private feedback 注入公开 prompts。

### v0.8 — Policy Packs And Runtime Parity

目标：用 configurable policy packs 支持不同简报场景，同时保持不同 runtime 的行为一致。

公开范围：

- 引入 audience、industry、cadence、delivery expectations 相关的 policy-pack 概念。
- 让 Hermes、Claude Code、Codex、OpenCode 和 manual fallback 对齐到同一组 artifact expectations。
- 保持单一公开 support matrix。

Non-goals:

- 商业 policy-pack 内部规则稳定前不公开。
- 不让 runtime-specific 细节分叉业务 artifact schema。

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
