# Repair Protocol

Repairs are deterministic transactions around owner-stage artifact edits.
WorkBuddy must not invent a repair by editing control files.

## Inspect The Route

```bash
multi-agent-brief repair route --workspace <workspace>
```

If no route exists, report that result. Do not start an unowned repair.

## Start Repair

```bash
multi-agent-brief repair start --workspace <workspace>
```

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
