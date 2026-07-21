# Status And Gates

Use status and gate output as control-plane diagnostics. Do not convert them
into delivery or release authority.

## Inspect Status

```powershell
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop runtime next --workspace "<workspace>"
& $BriefLoop state check --workspace "<workspace>"
```

Follow only handoff, the Store-native status projection, and `runtime next`
for next action, gate, finalize, and delivery
routing. Raw workflow state, event log, Registry, timestamps, and file
existence are audit evidence only. If the status projection or handoff reports
blockers, contamination,
active repair, stale artifacts, or invalid artifacts, stop and follow its
indicated transaction path. The legacy completion projection /
`workbuddy diagnose` surface is retired; do not call it.

## Quality Panel

Generate the static Quality Panel with:

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
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

A formal finalize-complete claim requires every current-run observation:
successful finalize command, structurally valid Finalize Report, reader-clean
pass, promoted delivery, current render transaction, finalize gate pass,
successful finalize-complete, the Store-native status projection reporting
`package_ready=true`, and a literal `delivered` / `terminal_state`. Any
hand-written Markdown/DOCX is
`draft/manual/unverified`. If it contains `CL-*`, `SRC-*`, `Claim Ledger`, local
paths, or other forbidden residue, stop the delivery claim and follow formal
repair/finalize; never hand-edit a frozen artifact.
