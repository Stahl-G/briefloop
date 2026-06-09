# Orchestrator Contract 模型

v0.6 围绕四类公开 contract 组织。本页只做抽象定义。详细 schema 草案、private prompt notes、评测样例和商业 policy rules 在稳定前不放进公开仓库。

## Behavior Contract

定义角色边界：main Orchestrator 能协调什么，specialist subagents 各自负责什么，哪些动作应被阻断或升级。

v0.6.7 control switchboard 会暴露 deterministic control recommendations，并记录 Orchestrator 显式做出的 selections。Selection 只是 runtime intent，不会自动执行 CLI command、subagent、repair action、source collection 或 human approval step。

## Process / Artifact Contract

定义 workflow 是否已经经过必要 stage，以及下游继续前预期 artifacts 是否存在。

`orchestrator_control_switchboard.json` 和 `control_selections.json` 是 runtime control context files，不是 expected workflow artifacts、Claim Ledger evidence、reader-facing output 或默认 finalize gates。

## Fact-Grounding / Evidence Contract

定义重要陈述必须保持 source-grounded evidence 可追溯，unsupported 或 uncertain claims 不应被过度表述。

v0.6.5 provenance projection 可以为 audit/debug review 暴露 citation 和 control relationships，但不声称完成语义真伪验证，也不替代人工 evidence review。

`provenance_graph.json` 由 Python control tool 生成，不是正式 workflow stage 的产物。Artifact contracts 用 `producer_kind: control_tool` 标识这一点；`producer_stage: provenance` 是 control-tool pseudo-producer label，不是 `stage_specs.yaml` stage。

Provenance graph 中 `artifact_derived_from` 有固定方向：`from` 是 derived/output artifact，`to` 是 source/input artifact。

## Quality / Audience Contract

定义最终 brief 是否对目标读者有用，是否匹配任务场景，并达到交付准备状态。

v0.6.8 source appendix 是 reader-facing delivery artifact，只来自 audited brief 实际引用、并可通过 Claim Ledger 解析的 claims。它不能暴露 raw claim IDs、source IDs、evidence text、本地路径或 `file://` URL，也不证明 claim 已经语义为真。

## 公开边界

本公开模型不会发布完整 schema 草案、精确 validation rules、私有 golden cases、行业 policy packs 或 agent prompt details。
