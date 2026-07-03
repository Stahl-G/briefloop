# Runtime Workspace Protocol

Read this when operating a real BriefLoop/MABW workspace.

## Authority

- The generated workspace handoff is the per-run contract.
- `docs/agent-contract.md` is the public cross-runtime contract.
- Python commands own persistent state, frozen artifacts, gates, and events.

## Allowed Actions

- Inspect state with `multi-agent-brief status --workspace <workspace>`,
  `state show`, or `state check`.
- Launch handoff with `multi-agent-brief run --workspace <workspace>`.
- Advance stages only with deterministic completion transactions.
- Use owner-stage repair transactions for frozen artifact repair.
- Trigger delivery only when the operator explicitly asks and gates allow it.
- Write product-quality projections with
  `briefloop quality summarize --workspace <workspace>` when the operator asks
  for the Quality Panel / Summary / static HTML audit surfaces. This command is
  a deterministic projection writer, not a gate runner, repair action, delivery
  approval, or quality score.
- Use `briefloop extract --workspace <workspace> --scope <text> --source <file>`
  in `document-review` / `evidence_extract` workspaces to register explicit
  extraction scope, durable local source bytes, source-lock hashes,
  deterministic logical-page seeds, and deterministic text-span registry seeds
  for UTF-8 text sources. This is not binary/PDF parsing, rendered-page visual
  inspection, table/figure extraction, semantic support assessment,
  Claim-Support Matrix generation, citation gating, or legal / disclosure
  review.
- Use `multi-agent-brief approval init`, `multi-agent-brief approval record`,
  and `multi-agent-brief release check` only for internal release-mode approval
  records. These commands write event-linked control records; they do not
  authorize public release or bypass gates.

## Forbidden Actions

- Do not edit control files directly.
- Do not patch frozen artifacts after stage completion.
- Do not use `state decide` to bypass `stage-complete`, `repair complete`, gate
  checks, or `finalize-complete`.
- If `workflow_state.json.trajectory_regulation.status` is
  `decision_narrowed`, only record decisions listed in
  `workflow_state.json.next_allowed_decisions`. The deterministic control plane
  may narrow repeated retry, repair-cycle, or blocker loops to
  `request_human_review` and `block_run`; this does not execute repair or
  approve delivery.
- Do not write source evidence from search summaries alone; source files must be
  durable evidence inputs.
- Do not hand-edit `quality_panel.json`, `quality_summary.md`,
  `quality_panel.html`, `human_approval_ledger.json`, or
  `release_readiness_report.json`. Use the owning deterministic command.
- Do not treat a Quality Panel `pass` or release readiness report as permission
  to deliver or publish.

## Stop Conditions

Stop and report the exact error when:

- `active_repair` exists
- run integrity is contaminated
- gate reports have blocking findings
- target status says `auditable_brief` complete or incomplete
- a command asks for human review or fresh evidence setup
- quality summary / HTML artifacts are stale or hand-edited; rerun
  `briefloop quality summarize`
- approval ledger or release readiness records fail event-log linkage
