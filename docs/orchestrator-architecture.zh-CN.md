# Orchestrator 架构

本页用公开安全方式说明 v0.6 Orchestrator 架构。

## 核心模型

Orchestrator 是 runtime main agent。它可以是 Hermes parent agent、Claude Code command context、Codex main agent、OpenCode primary agent，或 manual fallback 操作者。

Python 保持工具层定位，负责 workspace setup、source handling、deterministic checks、validation helpers、audit support 和 final rendering。Python 不是标准完整 brief-generation runtime。

```text
runtime main agent
  -> reads workspace context
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

这些文件描述共享 authority、decision vocabulary、stage order、artifact expectations 和 default policy shell。v0.6.1 增加最小 runtime state control files 和 artifact status checks。v0.6.2 增加最小 feedback issue 和 repair-plan 控制面。v0.6.3 增加 deterministic material-fact、freshness 和 target-relevance gate controls。v0.6.4 增加 packaged public-safe evaluation cases，用于开发和 CI 回归验证。Python 仍不自动改 brief artifacts、不执行 repair、不 live-fetch sources、不做 semantic truth judgment、不用 LLM judge 给文章打分，也不实现 provenance graph。

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

在 v0.6.1 中，这些 decision 也可以通过 runtime state event log 记录。v0.6.2 也会记录 feedback issue 和 repair-plan events。v0.6.3 也会记录 quality gate check/pass/block events。这个 event log 是 control trace，不是完整 provenance graph。

## Runtime Loop

每个 runtime 都应表达同一套 loop：

1. 读取 `config.yaml`、`sources.yaml`、`user.md`、inputs、handoff artifacts 和 runtime state files。
2. 从 handoff 读取 contract references。
3. 判断当前 stage 和 expected artifact。
4. 将 stage 委派给对应 specialist role 或 Python tool。
5. 检查 expected artifact 是否存在，并是否适合进入下一 stage。
6. 如果存在 audit findings 或 human feedback，先结构化 issue 和 repair plan，但不执行 repair。
7. 决定 continue、retry、delegate repair、request human review、block 或 finalize。
8. 仅在 audit readiness 后 finalize。

不同 runtime 的机制可以不同，但 artifact expectations 不应分叉。

## Provenance Compatibility

v0.6.0 不构建 provenance graph。但 contract 形状要兼容后续 provenance 工作，保留：

- artifact identity
- producer stage or role
- consumer stage or role
- validation result summary
- blocking reason
- retry or human-review decision
- decision category attached to a stage

## Deferred Work

后续 v0.6 milestone 负责：

- material-fact and freshness gates
- private/commercial benchmark suites
- LLM-as-judge prose scoring
- evidence and execution provenance

## Related

- [Orchestrator Contract 模型](orchestrator-contracts.zh-CN.md)
- [当前架构状态](architecture-status.zh-CN.md)
- [迁移说明](MIGRATION.zh-CN.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)
