# Status And Gates

Use status and gate output as control-plane diagnostics. Do not convert them
into delivery or release authority.

## Inspect Status

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop state check --workspace "<workspace>"
```

Follow only handoff/diagnose for next action, gate, finalize, and delivery
routing. Raw status, workflow state, event log, Registry, timestamps, and file
existence are audit evidence only. If diagnose reports blockers, contamination,
active repair, stale artifacts, or invalid artifacts, stop and follow its
indicated transaction path.

## Quality Panel

Successful CLI `finalize-complete` first completes the authoritative transaction
and immutable run archive, then automatically materializes the static Quality
Panel artifacts and binds them through the Artifact Registry.

If the automatic projection is missing, stale, or invalid, repair or reproject
it explicitly with:

```powershell
& $BriefLoop quality summarize --workspace "<workspace>"
```

The output files are:

- `output/intermediate/quality_panel.json`
- `output/intermediate/quality_summary.md`
- `output/intermediate/quality_panel.html`

`quality summarize` is not the unique normal writer. These are audit
projections. They are not gates, release approval, or delivery approval.

## Delivery

Only run delivery when the user explicitly asks and the current gate/status path
allows it. If a reader-clean or gate blocker exists, do not package or deliver
around it.

A formal finalize-complete claim requires every current-run observation:
successful finalize command, structurally valid Finalize Report, reader-clean
pass, promoted delivery, current render transaction, finalize gate pass,
successful finalize-complete, current finalize event in diagnose, valid
delivery truth, and literal delivery outcome. Any hand-written Markdown/DOCX is
`draft/manual/unverified`. If it contains `CL-*`, `SRC-*`, `Claim Ledger`, local
paths, or other forbidden residue, stop the delivery claim and follow formal
repair/finalize; never hand-edit a frozen artifact.
