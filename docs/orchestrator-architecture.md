# Orchestrator Architecture

This page describes the public v0.6 Orchestrator architecture at a high level.

## Core Model

The Orchestrator is the runtime main agent. It may run as the Hermes parent agent, Claude Code command context, Codex main agent, OpenCode primary agent, or manual fallback operator.

Python remains the tool layer. It provides workspace setup, source handling, deterministic checks, validation helpers, audit support, and final rendering. It is not the standard full brief-generation runtime.

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

v0.6.0 introduces public-safe contract references:

- `configs/orchestrator_contract.yaml`
- `configs/stage_specs.yaml`
- `configs/artifact_contracts.yaml`
- `configs/policy_packs/default.yaml`

These files describe shared authority, decision vocabulary, stage order, artifact expectations, and the default policy shell. v0.6.1 added minimum runtime state control files and artifact status checks. v0.6.2 added minimum feedback issue and repair-plan controls. v0.6.3 added deterministic material-fact, freshness, and target-relevance gate controls. v0.6.4 added packaged public-safe evaluation cases for developer/CI regression checks. v0.6.5 added optional deterministic provenance projection for workspace audit/debug review. v0.6.6 adds a workspace-local audience profile and frozen per-run audience snapshot exposed through handoff. v0.6.7 adds an Orchestrator control switchboard for deterministic control recommendations and recorded enable/defer/reject selections. v0.6.8 adds a reader-facing source appendix generated during finalize from cited Claim Ledger sources. v0.6.9 stabilizes install/runtime asset parity and adds workspace-local OpenCode/Claude Code runtime kits. Python still does not automatically edit brief artifacts, execute repair, live-fetch sources, make semantic truth judgments, score prose with an LLM judge, treat provenance or a source appendix as semantic proof, learn taste automatically, or execute selected controls automatically.

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

In v0.6.1 these decisions can also be recorded through the runtime state event log. In v0.6.2 feedback issue and repair-plan events can also be recorded. In v0.6.3 quality gate check/pass/block events can also be recorded. In v0.6.5 provenance build/validate outcomes can also be recorded. In v0.6.6 audience snapshot creation can also be recorded. In v0.6.7 switchboard build and control selection events can also be recorded. The event log is a control trace; `provenance_graph.json` is a separate derived projection.

## Runtime Loop

Each runtime should communicate the same loop:

1. Read `config.yaml`, `sources.yaml`, `user.md`, inputs, handoff artifacts, runtime state files, `output/intermediate/audience_profile_snapshot.md`, and `output/intermediate/orchestrator_control_switchboard.json`.
2. Summarize relevant taste guidance from the frozen audience snapshot for delegated roles.
3. Record enable/defer/reject control selections when the switchboard recommends controls; selection is not execution.
4. Read contract references from the handoff.
5. Identify the current stage and expected artifact.
6. Delegate the stage to the appropriate specialist role or Python tool, passing the taste summary as context when useful.
7. Check that the expected artifact is present and suitable for the next stage.
8. When audit findings or human feedback exist, structure issues and repair plans without executing repair.
9. Decide whether to continue, retry, delegate repair, request human review, block, or finalize.
10. Finalize only after audit readiness, generating configured reader-facing outputs and `output/source_appendix.md` when requested.

Runtime mechanics may differ, but artifact expectations should stay aligned.

## Runtime Asset Availability

Package installs include Python CLI code, packaged contract configs, policy
packs, and packaged public-safe eval fixtures. Source runtime directories such
as `.agents/`, `.claude/`, `.codex/`, `.opencode/`, and
`integrations/hermes-plugin/` are source-clone-only.

Use `multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|all`
from a source clone to copy workspace-local OpenCode/Claude Code commands,
agents, and skills into the business workspace. The installed workspace kit
lets a runtime operate from the workspace without reading the MABW source repo.

## Reader-Facing Source Appendix

v0.6.8 lets `multi-agent-brief finalize` write a reader-facing source appendix when `source_appendix` is configured, or when older configs request the legacy `source_map` output format. Current `source_appendix` requests append the source list to final reader Markdown/DOCX by default while also writing `output/source_appendix.md`.

- The appendix is generated only from claims actually cited in `output/intermediate/audited_brief.md`.
- Reader-facing output must not expose raw `claim_id`, `source_id`, evidence text, local paths, or `file://` URLs.
- The appendix is a reader-facing source list, not a runtime state file, artifact contract, quality gate, provenance graph, or semantic proof that claims are true.
- Missing or malformed Claim Ledger data fails explicit `source_appendix` requests; legacy `source_map` requests are treated as compatibility aliases and may skip with warnings.

## Audience Profile Runtime Surface

v0.6.6 creates workspace-local `audience_profile.md` during init and freezes it into `output/intermediate/audience_profile_snapshot.md` for each active run. The snapshot is exposed through `agent_handoff.json` as `audience_memory_files`.

- The Orchestrator reads the snapshot, not the live profile, during the run.
- Mid-run edits to `audience_profile.md` apply to the next run.
- Audience memory is runtime context, not source evidence, an artifact contract, a quality gate, a provenance graph node, or a stage blocker.
- Python creates, freezes, exposes, and records the context; it does not learn taste automatically, update profiles, enforce taste, or route workflow controls based on taste.

## Orchestrator Control Switchboard

v0.6.7 creates `output/intermediate/orchestrator_control_switchboard.json` during runtime handoff and records explicit Orchestrator selections in `output/intermediate/control_selections.json` only when `multi-agent-brief controls select` is called.

- The switchboard separates available controls, deterministic recommendations, Orchestrator selections, and execution.
- Selection is not execution: choosing `enable` records intent but does not run quality gates, feedback planning, provenance projection, source discovery, repair, or subagents.
- Privacy-sensitive controls require explicit human approval before they are execution-ready.
- The switchboard is runtime control context, not a Claim Ledger input, final-reader artifact, or finalize gate.

## Provenance Projection

v0.6.5 can build `output/intermediate/provenance_graph.json` from existing runtime state, artifact registry, event log, Claim Ledger, feedback, repair, and quality gate files. This graph is an audit/debug projection:

- It preserves artifact identity, producer stage or role, consumer stage or role, and validation summaries as graph metadata.
- It is created only by `multi-agent-brief provenance build`.
- It does not initialize runtime state or execute workflow stages.
- It records citation and control relationships, not semantic proof.
- It does not block `state check`, `state decide`, or `finalize` by default.

## Deferred Work

Later v0.6 milestones own:

- private/commercial benchmark suites
- LLM-as-judge prose scoring
- semantic evidence support verification
- execution replay or a full DAG runtime

## Related

- [Orchestrator Contract Model](orchestrator-contracts.md)
- [Architecture Status](architecture-status.md)
- [Migration Notes](MIGRATION.md)
- [v0.6.0 Explicit Orchestrator Contract](implementation/v0.6.0-explicit-orchestrator-contract.md)
