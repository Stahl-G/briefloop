# Draft-Promote Ownership Matrix

This page defines a public-safe ownership matrix for future BriefLoop artifacts
that follow the same pattern as Claim Ledger freeze:

```text
agent-owned draft -> deterministic Python validation/promotion -> authoritative artifact
```

It is an implementation boundary note, not a shipped capability claim. It does not add a runtime, stage, artifact schema, validator, gate, delivery approval, release authority, semantic proof, or output-quality proof.

## Purpose

BriefLoop already uses a clean draft-promote pattern for Claim Ledger:

```text
Claim Ledger role writes output/intermediate/claim_drafts.json
Python runs state freeze-claim-ledger
Python writes output/intermediate/claim_ledger.json
```

The goal of this matrix is to prevent future high-risk JSON artifacts from
being introduced with unclear writers. Before implementing a new artifact, pick
one ownership class and one promotion path.

## Ownership Classes

| Class | Writer | Authority | Example |
|---|---|---|---|
| `agent_owned_draft` | Runtime role or compact operator | Draft content only | `claim_drafts.json` |
| `python_promoted_authoritative` | Deterministic Python transaction | Authoritative workflow artifact | `claim_ledger.json` |
| `python_only_control` | Deterministic Python transaction/checker | Runtime control state | `workflow_state.json` |
| `human_approval_record` | Human-triggered CLI command | Recorded approval/decision | `human_approval_ledger.json` |
| `projection_only` | Deterministic Python projection | Diagnostic/read-only surface | `quality_panel.json` |
| `reader_delivery` | Deterministic finalize/delivery command | Reader-facing output | `output/delivery/brief.md` |

Core Python-only control files include `workflow_state.json`,
`artifact_registry.json`, `runtime_manifest.json`, and `event_log.jsonl`.

## Initial Artifact Classification

| Artifact or surface | Class | Current writer | Notes |
|---|---|---|---|
| `output/intermediate/claim_drafts.json` | `agent_owned_draft` | Claim Ledger role or operator | Freeze input only; draft entries must not carry claim IDs. |
| `output/intermediate/claim_ledger.json` | `python_promoted_authoritative` | `multi-agent-brief state freeze-claim-ledger` | Python assigns deterministic claim IDs and records freeze metadata. |
| `output/intermediate/workflow_state.json` | `python_only_control` | Runtime-state transactions | Never hand-edit; use `state check`, `state decide`, `stage-complete`, `finalize-complete`, or repair transactions. |
| `output/intermediate/artifact_registry.json` | `python_only_control` | `state check` / runtime transactions | Hash/status registry; direct edits break auditability. |
| `output/intermediate/runtime_manifest.json` | `python_only_control` | Runtime initialization/control commands | Run identity and runtime metadata, not a role draft. |
| `output/intermediate/event_log.jsonl` | `python_only_control` | Event append helpers | Append-only transaction trace. |
| `output/intermediate/audited_brief.md` | `agent_owned_draft` | Analyst then Editor roles | Role-owned working artifact; Python stage completion records hash/snapshot boundaries. |
| `output/intermediate/analyst_draft_snapshot.md` | `python_promoted_authoritative` | `state stage-complete --stage analyst` | Frozen snapshot of Analyst output. |
| `output/intermediate/audit_report.json` | `agent_owned_draft` | Auditor role | Semantic audit report, then Python-owned binding/freshness checks constrain downstream use. |
| `output/intermediate/gates/*_quality_gate_report.json` | `python_only_control` | `gates check` | Deterministic gate report, not role-authored prose. |
| `output/intermediate/quality_panel.json` | `projection_only` | `quality summarize` | Diagnostic projection only; no gate, delivery, or release authority. |
| `output/intermediate/quality_summary.md` | `projection_only` | `quality summarize` | Human-readable projection derived from `quality_panel.json`. |
| `output/intermediate/quality_panel.html` | `projection_only` | `quality summarize` | Static projection derived from `quality_panel.json`. |
| `output/delivery/brief.md` | `reader_delivery` | `finalize` | Reader-facing artifact generated from audited inputs. |
| `output/delivery_bundle.zip` | `reader_delivery` | `packs bundle` | Bundle projection/export only; no delivery approval. |
| `output/audit_bundle.zip` | `projection_only` | `packs bundle` | Audit/control bundle projection only. |
| `human_approval_ledger.json` | `human_approval_record` | `approval init` / `approval record` | Human-triggered record; not automatic approval. |
| `release_readiness_report.json` | `projection_only` | `release check` | Internal release-mode readiness report, not public release authority. |

## Promotion Rules

Use these rules before adding a new artifact:

1. If an agent writes it and downstream stages treat it as authoritative, add a
   Python promotion transaction first.
2. If Python writes it, handoff text must present the owning command, not a file
   editing instruction.
3. If humans approve it, require a human-triggered CLI record and event linkage.
4. If it is only diagnostic, label it `projection_only` and keep it out of gate,
   delivery, and release authority unless a later PR explicitly changes that
   contract.
5. If it is reader-facing delivery, require finalize/delivery hygiene checks and
   current run-integrity state.

## Non-Goals

This matrix does not:

- rewrite current artifact schemas;
- migrate historical workspaces;
- add new stages or decisions;
- make WorkBuddy, operator runtime, or any unadapted host a delegated runtime;
- let Python draft report prose or judge semantic truth;
- turn Quality Panel, release readiness, or bundle projection into approval;
- claim output-quality improvement or semantic correctness.

## Checklist For New Artifacts

Before implementing a new artifact, answer:

- Which ownership class owns the artifact?
- Who is the sole writer?
- Is it draft input, promoted authoritative output, control state, projection,
  approval record, or reader delivery?
- Which command writes it?
- Which command validates it?
- Which downstream consumer reads it?
- If invalid or stale, does it block, warn, or stay unavailable?
- What test proves an agent cannot hand-author the Python-owned version?
