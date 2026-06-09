# Orchestrator 架构

本页用公开安全方式说明 v0.6 Orchestrator 架构。

## 核心模型

Orchestrator 是 runtime main agent。它可以是 Hermes parent agent、Claude Code command context、Codex main agent、OpenCode primary agent，或 manual fallback 操作者。

Python 保持工具层定位，负责 workspace setup、source handling、deterministic checks、validation helpers、audit support 和 final rendering。Python 不是标准完整 brief-generation runtime。

```text
runtime main agent
  -> reads workspace context
  -> reads frozen audience profile snapshot
  -> reads contract references
  -> identifies the next stage
  -> delegates a specialist role
  -> checks the expected artifact
  -> decides continue / retry / repair / review / block / finalize
```

## Contract References

v0.6.0 引入公开安全的 contract references：

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

这些文件描述共享 authority、decision vocabulary、stage order、artifact expectations 和 default policy shell。v0.6.1 增加最小 runtime state control files 和 artifact status checks。v0.6.2 增加最小 feedback issue 和 repair-plan 控制面。v0.6.3 增加 deterministic material-fact、freshness 和 target-relevance gate controls。v0.6.4 增加 packaged public-safe evaluation cases，用于开发和 CI 回归验证。v0.6.5 增加可选 deterministic provenance projection，用于 workspace audit/debug review。v0.6.6 增加 workspace-local audience profile 和 frozen per-run audience snapshot，并通过 handoff 暴露。Python 仍不自动改 brief artifacts、不执行 repair、不 live-fetch sources、不做 semantic truth judgment、不用 LLM judge 给文章打分、不把 provenance 当成语义证明，也不自动学习 taste 或基于 taste 做 workflow routing。

## 四类 Contract

| 类别 | 目的 |
|---|---|
| Behavior | 定义 Orchestrator 和 specialist role 边界。 |
| Process / Artifact | 定义 stage readiness 和 expected artifact categories。 |
| Fact-Grounding / Evidence | 保持 material statements 可追溯到 supported claims。 |
| Quality / Audience | 让 delivery decisions 匹配 reader context。 |

## Decision Vocabulary

Orchestrator 使用统一 decision vocabulary：

- `continue`
- `retry_stage`
- `delegate_repair`
- `request_human_review`
- `block_run`
- `finalize`

在 v0.6.1 中，这些 decision 也可以通过 runtime state event log 记录。v0.6.2 也会记录 feedback issue 和 repair-plan events。v0.6.3 也会记录 quality gate check/pass/block events。v0.6.5 也会记录 provenance build/validate 结果。v0.6.6 也可以记录 audience snapshot creation。event log 是 control trace；`provenance_graph.json` 是独立的派生 projection。

## Runtime Loop

每个 runtime 都应表达同一套 loop：

1. 读取 `config.yaml`、`sources.yaml`、`user.md`、inputs、handoff artifacts、runtime state files 和 `output/intermediate/audience_profile_snapshot.md`。
2. 从 frozen audience snapshot 总结和本 run 相关的 taste guidance，供 delegated roles 使用。
3. 从 handoff 读取 contract references。
4. 判断当前 stage 和 expected artifact。
5. 将 stage 委派给对应 specialist role 或 Python tool，并在有用时传入 taste summary。
6. 检查 expected artifact 是否存在，并是否适合进入下一 stage。
7. 如果存在 audit findings 或 human feedback，先结构化 issue 和 repair plan，但不执行 repair。
8. 决定 continue、retry、delegate repair、request human review、block 或 finalize。
9. 仅在 audit readiness 后 finalize。

不同 runtime 的机制可以不同，但 artifact expectations 不应分叉。

## Audience Profile Runtime Surface

v0.6.6 会在 init 时创建 workspace-local `audience_profile.md`，并为当前 active run 冻结为 `output/intermediate/audience_profile_snapshot.md`。snapshot 会通过 `agent_handoff.json` 的 `audience_memory_files` 暴露。

- Orchestrator 在 run 中读取 snapshot，不读取 live profile 作为当前 run 行为依据。
- 中途修改 `audience_profile.md` 只影响下一次 run。
- Audience memory 是 runtime context，不是 source evidence、artifact contract、quality gate、provenance graph node 或 stage blocker。
- Python 负责创建、冻结、暴露和记录这层 context；不自动学习 taste、不自动更新 profile、不 enforce taste，也不基于 taste 做 workflow routing。

## Provenance Projection

v0.6.5 可以基于已有 runtime state、artifact registry、event log、Claim Ledger、feedback、repair 和 quality gate files 生成 `output/intermediate/provenance_graph.json`。这个 graph 是 audit/debug projection：

- 保留 artifact identity、producer stage or role、consumer stage or role 和 validation summaries 作为 graph metadata。
- 只由 `multi-agent-brief provenance build` 创建。
- 不初始化 runtime state，也不执行 workflow stages。
- 记录 citation 和 control relationships，不做语义证明。
- 默认不阻断 `state check`、`state decide` 或 `finalize`。

v0.6.5 不实现 semantic proof、source support graph、execution replay 或 full DAG runtime。

## Deferred Work

后续 v0.6 milestone 负责：

- private/commercial benchmark suites
- LLM-as-judge prose scoring
- semantic evidence support verification
- execution replay 或完整 DAG runtime

## Related

- [Orchestrator Contract 模型](orchestrator-contracts.zh-CN.md)
- [当前架构状态](architecture-status.zh-CN.md)
- [迁移说明](MIGRATION.zh-CN.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)
