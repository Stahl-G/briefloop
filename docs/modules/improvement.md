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
multi-agent-brief improve propose --workspace <ws> \
  --guidance "Lead with the decision-relevant number when evidence supports it." \
  --category audience_mismatch \
  --scope brief \
  --source-summary "Operator-created audience guidance proposal."

multi-agent-brief improve list --workspace <ws>
multi-agent-brief improve show --workspace <ws> --entry-id AG-0001
multi-agent-brief improve approve --workspace <ws> --entry-id AG-0001 --by <operator>
multi-agent-brief improve reject --workspace <ws> --entry-id AG-0001 --by <operator> --reason "..."
multi-agent-brief improve revert --workspace <ws> --entry-id AG-0001 --by <operator> --reason "..."
multi-agent-brief improve stats --workspace <ws>
multi-agent-brief improve validate --workspace <ws>
multi-agent-brief improve rebuild --workspace <ws>
```

`improve rebuild` only writes `improvement/memory.md`. It does not create runtime state, write `output/intermediate/`, update handoff, or append events.

## Runtime Semantics

Approval is not immediate current-run effect. An approved ledger entry becomes runtime input only when the next `run`, `start`, or `handoff` recomputes memory and freezes a per-run snapshot.

`runtime_manifest.json.improvement.materialized_entry_ids` means entries included in this run's frozen `improvement_memory_snapshot.md`. It is not a ledger state and is not proof that the model followed the guidance.

`memory_sha256` records the deterministic projection content computed during `run`, `start`, or `handoff`. It is not a runtime-readable input and must not cause handoff to expose `improvement/memory.md`.

Handoff exposes only `output/intermediate/improvement_memory_snapshot.md`. Runtime agents should not read live `improvement/memory.md` as guidance.

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
