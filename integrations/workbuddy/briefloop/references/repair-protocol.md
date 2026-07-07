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

## Hard Stops

Stop and ask for human review when:

- trajectory regulation narrows decisions to `request_human_review` or
  `block_run`;
- repair would require changing frozen artifacts directly;
- the route asks for a stage or artifact WorkBuddy cannot safely perform;
- the user asks to bypass gates, delivery checks, or approval records.
