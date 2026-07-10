# Repair Protocol

Repairs are deterministic transactions around owner-stage artifact edits.
WorkBuddy must not invent a repair by editing control files.

## Inspect Current-Gate Guidance

```bash
multi-agent-brief gates show --workspace <workspace> --json
```

Follow the emitted `required_commands`. If no route exists, report that result.
Do not start an unowned repair.

## Start Current-Gate Repair

Use the scoped repair start command from `gates show`. Current-gate repair
start must include `--gate-stage` and `--gate-artifact`; do not use unscoped
repair start for current-gate blockers.

## Start Non-Gate Repair

For non-gate owner-stage repair routes from audit_report, finalize_report,
artifact_registry, or transaction_integrity, inspect:

```bash
multi-agent-brief repair route --workspace <workspace> --json
```

Start the selected non-gate route with `--finding-id <finding_id>` or
`--route-index <route_index>`. Do not use bare
`repair start --workspace <workspace>`.

After this transaction, edit only the artifacts allowed by the active repair
record and only for the repair owner/stage shown by BriefLoop.

## Complete Repair

```bash
multi-agent-brief repair complete --workspace <workspace> --reason "<reason>"
```

Then rerun the downstream status/gate path that BriefLoop reports.

## Contaminated Recovery

If a frozen owner-stage artifact was edited outside an active repair and the
run is already contaminated, do not clear the contamination or edit
`artifact_registry.json`. When the operator/human decision is to accept the
current bytes as a new owner-stage revision, record that recovery transaction:

```bash
multi-agent-brief repair supersede-stage --workspace <workspace> --stage <owner_stage> --artifact <artifact_path> --reason "<reason>" --json
```

This records the old registered hash, current bytes hash, and reason, preserves
the original contamination event, keeps `reference_eligible=false`, and requires
downstream stages to rerun.

When `briefloop workbuddy diagnose --workspace <workspace> --json` reports
`next_allowed_action=stop_human_review_or_supersede`, use this lane instead of
hand-editing control files. Only WorkBuddy diagnose surfaces
`next_allowed_action` (it formats the completion projection); `status --json`
does not output that field.

## Boundaries

- `repair route` is read-only.
- `repair start` creates `workflow_state.active_repair`.
- `repair supersede-stage` records a contaminated owner-stage revision; it does
  not make the run clean or reference-eligible.
- Workspace-wide `repair route` is for non-gate route inspection; bare
  `repair start --workspace <workspace>` is not a legal current-gate or
  non-gate repair command.
- While `active_repair` exists, stage completion, finalize completion, delivery,
  and gate-report writes must fail closed.
- Direct edits to frozen artifacts without active repair remain contamination.
- Repair does not make a contaminated run clean or reference-eligible.

## Hard Stops

Stop and ask for human review when:

- trajectory regulation narrows decisions to `request_human_review` or
  `block_run`;
- repair would require changing frozen artifacts directly without a recorded
  repair or supersede transaction;
- the route asks for a stage or artifact WorkBuddy cannot safely perform;
- the user asks to bypass gates, delivery checks, or approval records.
