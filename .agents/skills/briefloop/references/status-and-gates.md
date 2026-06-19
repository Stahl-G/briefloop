# Status, Gates, Finalize, And Delivery

Read this before interpreting `status`, running gates, finalizing, or delivering.

## Status

`multi-agent-brief status --workspace <workspace>` is read-only. Use it first
when the user asks "where is this run?" or "what should happen next?"

Status can show:

- active repair
- run integrity and reference eligibility
- topology-satisfied stages
- `auditable_brief` target complete or incomplete
- next suggested command

## Gates

`gates check` writes stage-scoped gate reports. It is not a read-only helper.
Do not rerun a frozen stage-scoped gate report unless the runtime permits it
through the proper rerun/repair path.

Blocking findings stop stage completion. Warning-only findings are still
evidence and should be reported, but they are not Python proof of semantic
failure.

## Auditable Target

For `assessment_target=auditable_brief`:

- auditor gate must pass before auditor stage completion
- target completion blocks finalize and delivery
- incomplete target blocks downstream reader-facing outputs
- next path is experiment registration/scoring/assessment, not reader delivery

## Delivery Target

For `assessment_target=delivery_brief` or normal workspaces:

- finalize renders reader-facing files
- `state finalize-complete` writes the authoritative run archive
- delivery remains human-triggered and gated
- non-reference-eligible delivery may be useful locally, but it is not clean
  reference evidence
