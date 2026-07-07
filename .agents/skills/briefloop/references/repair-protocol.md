# Owner-Stage Repair Protocol

Read this when a gate, audit report, state check, or runtime handoff says the
run needs repair.

## Legal Path

For current quality-gate owner-stage repair, inspect the current gate guidance:

```bash
briefloop gates show --workspace <workspace> --json
```

Follow the emitted `required_commands`. Current-gate repair start must be
scoped with `--gate-stage` and `--gate-artifact`; do not use unscoped repair
start for current-gate blockers. Delegate only the reported `repair_owner` role.
The owner may edit only
`allowed_artifacts`. Then run:

```bash
briefloop repair complete --workspace <workspace> --reason "<reason>" --json
```

Rerun downstream stages from `must_rerun_from`.

For non-gate owner-stage repair routes from `audit_report`, `finalize_report`,
`artifact_registry`, or `transaction_integrity`, inspect the workspace route:

```bash
briefloop repair route --workspace <workspace> --json
```

Start the selected non-gate route with `--finding-id <finding_id>` or
`--route-index <route_index>`. Do not use bare
`repair start --workspace <workspace>`.

Legacy `quality_gate_report.json` blockers must be materialized into a
stage-scoped report first: run `briefloop gates check --workspace <workspace>
--stage <current_stage>`, then rerun `briefloop gates show --workspace
<workspace> --json`.

## Boundaries

- `repair route` is read-only.
- `repair start` creates `workflow_state.active_repair`.
- Workspace-wide `repair route` is for non-gate route inspection; bare
  `repair start --workspace <workspace>` is not a legal current-gate or
  non-gate repair command.
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
