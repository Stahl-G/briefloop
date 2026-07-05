# Owner-Stage Repair Protocol

Read this when a gate, audit report, state check, or runtime handoff says the
run needs repair.

## Legal Path

```bash
briefloop repair route --workspace <workspace> --json
briefloop repair start --workspace <workspace> --json
```

Delegate only the reported `repair_owner` role. The owner may edit only
`allowed_artifacts`. Then run:

```bash
briefloop repair complete --workspace <workspace> --reason "<reason>" --json
```

Rerun downstream stages from `must_rerun_from`.

## Boundaries

- `repair route` is read-only.
- `repair start` creates `workflow_state.active_repair`.
- While `active_repair` exists, stage completion, finalize completion, delivery,
  and gate-report writes must fail closed.
- Direct edits to frozen artifacts without active repair remain contamination.
- Repair does not make a contaminated run clean or reference-eligible.
- If no deterministic route exists, use `request_human_review`, `block_run`, or
  a fresh workspace rather than patching control files.

## Common Mistakes

- Do not use `state decide delegate_repair` as authorization to edit artifacts.
- Do not route input limitations such as insufficient claims into Claim Ledger
  repair unless a deterministic route explicitly says so.
- Do not clear stale downstream artifacts by editing the artifact registry.
