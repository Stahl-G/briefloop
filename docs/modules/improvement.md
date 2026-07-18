# Improvement Ledger And Memory

v0.7.0 adds a workspace-local control surface for approved reader-preference guidance.

It is a human-governed memory mechanism, not autonomous learning, retrieval memory, or a quality guarantee.

## Files

| File | Role |
|---|---|
| `improvement/ledger.jsonl` | Append-only Improvement Ledger. Stores proposed, approved, rejected, and reverted guidance revisions. |
| `improvement/memory.md` | Deterministic projection/debug surface rebuilt from approved materializable ledger entries. |
| `output/intermediate/improvement_memory_snapshot.md` | Frozen per-run runtime input exposed through handoff when eligible guidance exists. |
| `output/intermediate/runtime_manifest.json.improvement` | Per-run audit record for ledger hash, projection hash, snapshot hash, and materialized entry ids. |

## Command Lifecycle

```bash
briefloop improve propose --workspace <ws> \
  --guidance "Lead with the decision-relevant number when evidence supports it." \
  --category audience_mismatch \
  --scope brief \
  --source-summary "Operator-created audience guidance proposal."

briefloop improve list --workspace <ws>
briefloop improve show --workspace <ws> --entry-id AG-0001
briefloop improve approve --workspace <ws> --entry-id AG-0001 --by <operator>
briefloop improve reject --workspace <ws> --entry-id AG-0001 --by <operator> --reason "..."
briefloop improve revert --workspace <ws> --entry-id AG-0001 --by <operator> --reason "..."
briefloop improve stats --workspace <ws>
briefloop improve validate --workspace <ws>
briefloop improve rebuild --workspace <ws>
```

`improve rebuild` only writes `improvement/memory.md`. It does not create runtime state, write `output/intermediate/`, update handoff, or append events.

## Runtime Semantics

Approval is not immediate current-run effect. An approved ledger entry becomes runtime input only when the next `run`, `start`, or `handoff` recomputes memory and freezes a per-run snapshot.

`runtime_manifest.json.improvement.materialized_entry_ids` means entries included in this run's frozen `improvement_memory_snapshot.md`. It is not a ledger state and is not proof that the model followed the guidance.

`memory_sha256` records the deterministic projection content computed during `run`, `start`, or `handoff`. It is not a runtime-readable input and must not cause handoff to expose `improvement/memory.md`.

`memory_sha256` is the hash of the deterministic memory projection at the time
the run snapshot was frozen. If live `improvement/memory.md` changes later after
ledger updates, that does not invalidate the existing run snapshot.

`ledger_sha256: null` means no Improvement Ledger file existed when the run
snapshot was prepared. A non-null `memory_sha256` with no snapshot is valid: it
records the deterministic zero-entry projection used for audit/debug, not active
runtime guidance.

Handoff exposes only `output/intermediate/improvement_memory_snapshot.md`. Runtime agents should not read live `improvement/memory.md` as guidance.

## Capacity And Auditability

Active approved materialized entries should stay around 20. Full injection is an
auditability requirement: every materialized entry remains visible in the frozen
snapshot and `runtime_manifest.json.improvement.materialized_entry_ids`.
Retrieval-based memory, compaction, and selective injection are deferred until
they can be audited without hiding which guidance the run consumed.

## Feedback Boundary

FeedbackIssue is evidence, not guidance. Guidance must be human-authored and human-approved. There is no automatic path from issue or gate finding to materialized audience memory.

Machine-checkable issues are not taste. Correctness, delivery, gate, and repair findings should stay in the feedback, repair, contract, or gate surfaces unless a human rewrites them as persistent reader-preference guidance.

`origin_runtime` is metadata for audit/rendering context only. It is not used for filtering, routing, materialization, or runtime-specific behavior.

## Non-Goals

- no autonomous learning
- no automatic repair
- no semantic proof
- no output quality guarantee
- no RAG or retrieval memory
- no runtime-specific guidance filtering
- no ledger compaction in v0.7
