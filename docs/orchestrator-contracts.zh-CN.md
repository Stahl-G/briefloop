# Orchestrator Contract 模型

v0.6 围绕四类公开 contract 组织。本页只做抽象定义。详细 schema 草案、private prompt notes、评测样例和商业 policy rules 在稳定前不放进公开仓库。

## Behavior Contract

定义角色边界：main Orchestrator 能协调什么，specialist subagents 各自负责什么，哪些动作应被阻断或升级。

## Process / Artifact Contract

定义 workflow 是否已经经过必要 stage，以及下游继续前预期 artifacts 是否存在。

## Fact-Grounding / Evidence Contract

定义重要陈述必须保持 source-grounded evidence 可追溯，unsupported 或 uncertain claims 不应被过度表述。

## Quality / Audience Contract

定义最终 brief 是否对目标读者有用，是否匹配任务场景，并达到交付准备状态。

## 公开边界

本公开模型不会发布完整 schema 草案、精确 validation rules、私有 golden cases、行业 policy packs 或 agent prompt details。
