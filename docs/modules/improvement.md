# Approved Guidance And Successor Snapshots

The active development-main guidance path is Store-native and Human-governed.
It carries presentation guidance into a normal later brief without turning
feedback into automatic learning or giving guidance evidence/control authority.
Utility is **NOT MEASURED**.

The v0.7 file-based Improvement system is retired. These files are inert and
have no current reader or writer:

- `improvement/ledger.jsonl`
- `improvement/memory.md`
- `output/intermediate/improvement_memory_snapshot.md`
- `runtime_manifest.json.improvement`

The public `briefloop improve ...` mutators, fast-rerun path, and MABW-080
tooling are also retired. They are not migration, fallback, or compatibility
paths for a fresh SQLite workspace.

## Human Review Lifecycle

After one run reaches verified `finalized_local`, Store-qualified post-final
review can append:

1. an explicit Human accept/reject/defer disposition for one selected LAJ
   result;
2. a Human-edited guidance draft; and
3. a separate approval/status revision.

Each step is a strict Human request recorded through append-only SQLite
Receipts. Approval does not itself affect the current run or implicitly start a
later run. Generation 2 and later remain explicit; policy drift never auto-runs
or redials an evaluator.

After a successor becomes current, the explicit status/deactivate/revert/
supersede CLI path can still resolve the exact historical assessment result and
append a lifecycle change to its source run. Actionable browser `review-open`
remains current-head-only and never mixes a historical result into the
successor's Brief/Quality page.

## Explicit Normal Successor

A Human starts a normal successor in the same workspace with a new run identity
and strict `RunDirection`:

```bash
briefloop runtime successor-start \
  --workspace <workspace> \
  --direction-json '<strict RunDirection JSON>' \
  --run-id <new-run-id> \
  --include-approved-guidance
```

`--include-approved-guidance` is the separate Boolean opt-in. Without it, the
successor still starts normally and freezes an empty guidance snapshot. The
command does not call a provider or role, edit the predecessor, or reuse the
recovery-reset meaning.

One deterministic Core transaction atomically creates the successor and its
guidance snapshot. Exact replay returns the original Receipt and snapshot;
changed input or a competing successor conflicts before write.

## Compatibility And Selection

Python selects guidance deterministically. A guidance draft is eligible only
when its source finding was accepted, its exact latest draft is actively
approved, all Store bindings verify, and these presentation dimensions match
the successor direction exactly:

- audience and audience profile;
- output language;
- output style, including exact null;
- ordered output formats; and
- cadence/report type.

Subject, objective, focus areas, sources, search policy, report date/window, and
target terms are not supplied or overridden by guidance. Draft-only, rejected,
deferred, inactive, reverted, superseded, malformed, cross-workspace, and
scope-mismatched guidance has zero consumption.

The complete compatible active-approved set is bounded at 16 items and 65,536
combined UTF-8 bytes. Exceeding either bound returns a typed zero-write failure;
the system never truncates, summarizes, ranks semantically, or silently omits
items to fit.

## Runtime Consumption And Precedence

The successor snapshot copies exact Human-authored text into immutable Store
records. Analyst and Editor receive the same ordered
`RoleTaskEnvelope.frozen_guidance_context`; source-planner, source-provider,
Scout, Screener, Claim Ledger, Auditor, and Formatter receive none. Runtime
replay reconstructs the context from the frozen snapshot rather than a live
guidance head or retired file. Later deactivation or supersession cannot change
the already-created successor.

Roles must apply this precedence:

```text
Core integrity and legality
> current RunDirection, source policy, output contract, Gate and delivery
> frozen approved guidance
> role presentation preferences
```

Frozen guidance may shape only audience fit, structure, style, and expression.
It is not a fact, source, Claim Ledger input, Gate rule, repair command,
finalize/delivery authority, or Core next-action authority.

## Non-Goals

- no automatic finding acceptance or guidance approval
- no implicit reuse, automatic learning, or automatic repair
- no semantic proof or output-quality guarantee
- no RAG, retrieval memory, semantic ranking, or runtime-specific filtering
- no provider expansion or repeated evaluator call
- no cross-workspace import, old-schema migration, rollback, or dual read/write
- no guidance for source/evidence roles, Auditor, Formatter, Gate, finalize, or
  delivery authority
