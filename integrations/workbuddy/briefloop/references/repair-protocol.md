# Repair Protocol

Repairs are deterministic transactions around owner-stage artifact edits.
WorkBuddy must not invent a repair by editing control files.

## Inspect Current-Gate Guidance

```powershell
& $BriefLoop gates show --workspace "<workspace>" --json
```

Follow the emitted `required_commands`. When a command starts with `briefloop`
or `multi-agent-brief`, the only permitted adapter transformation is replacing
that leading token with the already-bound absolute `$BriefLoop`. Preserve every
subcommand, option, and argument value. Do not use `Invoke-Expression`, `cmd /c`,
Bash/Git-Bash fallback, PATH re-resolution, argument changes, or an unknown
leading command. If safe rebinding cannot be established, stop and display the
command instead of guessing. If no route exists, report that result. Do not
start an unowned repair.

## Start Current-Gate Repair

Use the scoped repair start command from `gates show`. Current-gate repair
start must include `--gate-stage` and `--gate-artifact`; do not use unscoped
repair start for current-gate blockers.

## Start Non-Gate Repair

For non-gate owner-stage repair routes from audit_report, finalize_report,
artifact_registry, or transaction_integrity, inspect:

```powershell
& $BriefLoop repair route --workspace "<workspace>" --json
```

Start the selected non-gate route with `--finding-id <finding_id>` or
`--route-index <route_index>`. Do not use bare
`repair start --workspace <workspace>`.

After this transaction, edit only the artifacts allowed by the active repair
record and only for the repair owner/stage shown by BriefLoop.

## Complete Repair

```powershell
& $BriefLoop repair complete --workspace "<workspace>" --reason "<reason>"
```

Then rerun the downstream status/gate path that BriefLoop reports.

## Contaminated Recovery

If a frozen owner-stage artifact was edited outside an active repair and the
run is already contaminated, do not clear the contamination or edit
`artifact_registry.json`. When the operator/human decision is to accept the
current bytes as a new owner-stage revision, record that recovery transaction:

```powershell
& $BriefLoop repair supersede-stage --workspace "<workspace>" --stage "<owner_stage>" --artifact "<artifact_path>" --reason "<reason>" --json
```

This records the old registered hash, current bytes hash, and reason, preserves
the original contamination event, keeps `reference_eligible=false`, and requires
downstream stages to rerun.

When `& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json` reports
`recovery_state.status=awaiting_recovery` and
`recovery_state.recommended_recovery_action=request_recovery_decision`, inspect
the bound contamination and owner revision before the operator chooses a
controlled repair, supersede, or new run. Do not infer recovery progress from
`run_integrity`, and do not hand-edit control files. Only WorkBuddy diagnose
surfaces `next_allowed_action` (it formats the completion projection);
`status --json` does not output that field.

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
