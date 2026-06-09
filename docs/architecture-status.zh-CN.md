# 当前架构状态

本页用于区分当前实现状态和 roadmap 目标。按 roadmap 做开发前，先读这里。

## 已实现的公开基线

- 标准用户路径是 subagent-first。
- `multi-agent-brief run` 生成 runtime handoff artifacts，而不是自己生成完整 brief。
- Runtime handoff 会初始化最小 runtime state 和 artifact registry control files。
- Feedback issues 和 bounded repair plans 可以被结构化、校验和记录，但不会自动执行 repair。
- Deterministic material-fact、freshness 和 target-relevance gates 可以写入 quality gate report，但不会自动找源、改稿或 repair。
- Packaged public-safe evaluation cases 可以验证 gates、feedback、runtime blocker 和 Hermes path 相关回归，用于开发和 CI。
- 可选 deterministic provenance projection 可以基于已有 control files 写入 workspace-local audit/debug graph。
- Workspace-local `audience_profile.md` 可以记录 reader taste；`run`、`start` 和 `handoff` 会创建或复用 frozen per-run `output/intermediate/audience_profile_snapshot.md`，并通过 handoff 暴露为 runtime context。
- Orchestrator control switchboard 可以给出 deterministic control recommendations，并记录 enable/defer/reject selections；selection 不会自动执行对应 control。
- Finalize 可以从 audited brief 中实际引用的 Claim Ledger 来源生成 reader-facing `output/source_appendix.md`，不会在最终读者面暴露内部 claim IDs、source IDs、evidence text 或本地路径。
- Python 命令负责 setup、source tooling、validation、audit support 和 rendering。
- Hermes、Claude Code、Codex、OpenCode 和 manual fallback 都是 agent runtime surfaces。
- Input governance 已区分 evidence、feedback、instructions 和 background context。
- 旧 Python-pipeline 叙事不再是标准 workflow。

## Roadmap 目标

roadmap 中提到的概念不一定已经实现。除非代码、测试和 support matrix 已确认，否则都按目标处理：

- Orchestrator contracts
- semantic evidence support verification
- quality evaluation and feedback loops
- policy packs
- public-safe reference workflows
- smart routing or automatic taste learning

## Experimental 或有限能力

标记为 experimental、interface-only 或 CLI-only 的能力，不应被当成稳定用户承诺。使用前先查 support matrix 和 CLI 输出。

## Contributor 规则

roadmap 方向不等于已实现代码。实现 roadmap item 前，先确认当前代码路径、对应 validator 或 test，以及该能力属于 public、experimental 还是 internal planning。
