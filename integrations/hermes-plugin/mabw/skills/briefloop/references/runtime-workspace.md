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

## Forbidden Actions

- Do not edit control files directly.
- Do not patch frozen artifacts after stage completion.
- Do not use `state decide` to bypass `stage-complete`, `repair complete`, gate
  checks, or `finalize-complete`.
- Do not write source evidence from search summaries alone; source files must be
  durable evidence inputs.

## Stop Conditions

Stop and report the exact error when:

- `active_repair` exists
- run integrity is contaminated
- gate reports have blocking findings
- target status says `auditable_brief` complete or incomplete
- a command asks for human review or fresh evidence setup
