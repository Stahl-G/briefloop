# Runtime Workspace Protocol

Read this when operating a real BriefLoop workspace.

## Authority

- The generated workspace handoff is the per-run contract.
- `docs/agent-contract.md` is the public cross-runtime contract.
- Python commands own persistent state, frozen artifacts, gates, and events.

## Allowed Actions

- Inspect state with `briefloop status --workspace <workspace>`,
  `state show`, or `state check`.
- Launch handoff with `briefloop run --workspace <workspace>`.
- Use `briefloop run --workspace <workspace> --runtime operator` when
  the host has no dedicated BriefLoop runtime adapter. Operator runtime is a
  host-agnostic compact workflow: it does not assume subagents ran, and it must
  still use deterministic transactions, artifacts, gates, and human-triggered
  delivery. Legacy `--runtime manual` remains a compatibility alias only.
  The generated operator handoff includes artifact ownership buckets:
  `agent_owned_drafts`, `cli_owned_outputs`, `read_only_diagnostics`, and
  `forbidden_direct_edits`; use these buckets instead of treating control JSON
  paths as writable instructions.
- Use `briefloop run --workspace <workspace> --runtime codebuddy` only when
  operating from a source checkout with `.codebuddy/skills/briefloop/` and
  `.codebuddy/agents/briefloop-*.md` available. The generated CodeBuddy
  handoff keeps orchestration in the main CodeBuddy session, explicitly invokes
  role sub-agents for handoff-assigned draft artifacts, and leaves
  deterministic CLI transactions to the main session.
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
  for UTF-8 text sources. For PDF/binary sources, run or provide extraction
  explicitly first; if an adjacent MinerU-derived `.mineru.md` file already
  exists, `briefloop extract` can bind the original source bytes plus that
  derived Markdown representation and seed logical pages/spans from the derived
  text. This is not automatic MinerU execution, binary/PDF parsing by
  BriefLoop itself, rendered-page visual inspection, table/figure extraction,
  semantic support assessment, Claim-Support Matrix generation, citation
  gating, or legal / disclosure review.
- Use `briefloop approval init`, `briefloop approval record`,
  and `briefloop release check` only for internal release-mode approval
  records. These commands write event-linked control records; they do not
  authorize public release or bypass gates.

## Agent Artifact Intake

Scout, Screener, and Claim Draft artifacts pass through deterministic intake
validation before registry acceptance and Claim Ledger freeze. Recoverable
shape drift (alternate field or wrapper names, textual confidence forms) is
normalized deterministically with recorded findings; evidence identity
violations fail closed. Do not "help" by hand-reshaping an agent artifact to
pass intake, and never let a draft invent what only Python may assign:

- `source_url` holding a source name instead of an HTTP(S) URL is fatal
- missing `evidence_text` or missing source identity is fatal
- claim drafts carrying `claim_id` / `CL-*` values are fatal; Python assigns
  claim IDs at freeze
- a silently shrunk candidate universe or a discard without a reason code is a
  finding, not something to paper over

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
- a delivery action is requested while the completion projection
  (`briefloop workbuddy diagnose --json`) does not report
  `delivery_truth.valid=true`, or a completed-delivery claim is requested
  without current-bound `event_truth.delivery_succeeded=true`
- target status says `auditable_brief` complete or incomplete
- a command asks for human review or fresh evidence setup
- quality summary / HTML artifacts are stale or hand-edited; rerun
  `briefloop quality summarize`
- approval ledger or release readiness records fail event-log linkage
