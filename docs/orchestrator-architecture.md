# Orchestrator Architecture

This page describes the public v0.6 Orchestrator architecture at a high level.

## Core Model

The Orchestrator is the runtime main agent. It may run as the Hermes parent agent, Claude Code command context, Codex main agent, OpenCode primary agent, or manual fallback operator.

Python remains the tool layer. It provides workspace setup, source handling, deterministic checks, validation helpers, audit support, and final rendering. It is not the standard full brief-generation runtime.

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

v0.6.0 introduces public-safe contract references:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

These files describe shared authority, decision vocabulary, stage order, artifact expectations, and the default policy shell. v0.6.1 added minimum runtime state control files and artifact status checks. v0.6.2 adds minimum feedback issue and repair-plan controls, but it does not automatically edit brief artifacts, execute repair, or build a provenance graph.

## Four Contract Categories

| Category | Purpose |
|---|---|
| Behavior | Defines Orchestrator and specialist role boundaries. |
| Process / Artifact | Defines stage readiness and expected artifact categories. |
| Fact-Grounding / Evidence | Keeps material statements traceable to supported claims. |
| Quality / Audience | Keeps delivery decisions aligned with reader context. |

## Decision Vocabulary

The Orchestrator uses a shared decision vocabulary:

- `continue`
- `retry_stage`
- `delegate_repair`
- `request_human_review`
- `block_run`
- `finalize`

In v0.6.1 these decisions can also be recorded through the runtime state event log. In v0.6.2 feedback issue and repair-plan events can also be recorded. The event log is a control trace, not a full provenance graph.

## Runtime Loop

Each runtime should communicate the same loop:

1. Read `config.yaml`, `sources.yaml`, `user.md`, inputs, handoff artifacts, and runtime state files.
2. Read contract references from the handoff.
3. Identify the current stage and expected artifact.
4. Delegate the stage to the appropriate specialist role or Python tool.
5. Check that the expected artifact is present and suitable for the next stage.
6. When audit findings or human feedback exist, structure issues and repair plans without executing repair.
7. Decide whether to continue, retry, delegate repair, request human review, block, or finalize.
8. Finalize only after audit readiness.

Runtime mechanics may differ, but artifact expectations should stay aligned.

## Provenance Compatibility

v0.6.0 does not build a provenance graph. It does keep the contract shape compatible with later provenance work by preserving:

- artifact identity
- producer stage or role
- consumer stage or role
- validation result summary
- blocking reason
- retry or human-review decision
- decision category attached to a stage

## Deferred Work

Later v0.6 milestones own:

- material-fact and freshness gates
- public-safe evaluation cases
- evidence and execution provenance

## Related

- [Orchestrator Contract Model](orchestrator-contracts.md)
- [Architecture Status](architecture-status.md)
- [Migration Notes](MIGRATION.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)
