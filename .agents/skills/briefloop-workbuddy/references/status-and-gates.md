# Status And Gates

Use status and gate output as control-plane diagnostics. Do not convert them
into delivery or release authority.

## Inspect Status

```bash
multi-agent-brief status --workspace <workspace>
multi-agent-brief status --workspace <workspace> --json
multi-agent-brief state check --workspace <workspace>
```

If `status` reports blockers, contamination, active repair, stale artifacts, or
invalid artifacts, stop and follow the indicated transaction path.

## Quality Panel

Generate the static Quality Panel with:

```bash
multi-agent-brief quality summarize --workspace <workspace>
```

The output files are:

- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`

These are audit projections. They are not gates, release approval, or
delivery approval.

## Delivery

Only run delivery when the user explicitly asks and the current gate/status path
allows it. If a reader-clean or gate blocker exists, do not package or deliver
around it.

Reader-clean requests are finalize requests. Do not edit
`output/intermediate/audited_brief.md` to remove reader residue. If
`reader_clean` fails, stop and report finalize failure. Do not call a manual
cleaned copy final, delivery, complete, `终稿`, or `已交付`.
