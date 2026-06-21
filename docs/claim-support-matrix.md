# Claim-Support Matrix

The Claim-Support Matrix is an optional experimental control surface on
mainline after the v0.9.1 release. It records explicit atom-to-evidence-span
support records when `output/intermediate/claim_support_matrix.json` is present.

It is a support-record control plane. It is not semantic support assessment,
truth proof, release eligibility, or an automatic source-support judge.

## What It Records

Each row links one Atomic Claim Graph atom to one Evidence Span Registry span,
or records an explicit no-span support state for labels such as `unsupported`,
`insufficient_evidence`, or `not_applicable`.

The row vocabulary can express:

- direct, partial, weak, inferential, unsupported, contradicted, insufficient,
  and not-applicable support records
- support strength and required action
- repair owner routing metadata
- decision source, such as human or llm-assisted human review

Rows are explicit records supplied by an operator, assessor, or later review
surface. Python validates and consumes these rows; Python does not decide
whether an evidence span semantically supports an atom.

## Runtime Checks

When the matrix file is absent, BriefLoop treats it as not available and does
not block the run.

When the matrix file is present, BriefLoop validates:

- matrix schema, IDs, vocabularies, and duplicate atom-span relation rows
- Claim Ledger dependency validity
- Atomic Claim Graph dependency validity
- Evidence Span Registry dependency validity
- `claim_id`, `atom_id`, and non-null `evidence_span_id` references
- high-materiality atom row coverage
- whether support labels that require evidence actually include a span

Invalid present matrices are marked invalid with
`claim_support_matrix_validation_error:*` and are not consumed for support
projection findings.

## Gate And Status Projection

When a present matrix is valid, BriefLoop projects the explicit support records
into deterministic status summaries and quality-gate findings.

The projection can surface:

- blocking rows for high-materiality `unsupported`, `contradicted`, or
  `insufficient_evidence` records
- weak-support downgrade or adjudication needs
- inferential-support framing needs

This projection is policy visibility over explicit records. It is not a
semantic assessor and does not prove that the records are correct.

## Reader Boundary

The matrix is an internal control artifact. Reader-facing prose should keep
Claim Ledger citations such as `[src:CL-0001]`. It should not cite matrix row
IDs, evidence span IDs, atom IDs, source paths, raw excerpts, or local files.

## Dogfood Boundary

For dogfood tests, use public-safe or synthetic workspaces with a present Atomic
Claim Graph, Evidence Span Registry, and Claim-Support Matrix. Inspect
`multi-agent-brief status --workspace <workspace>` and quality-gate reports to
confirm projection behavior.

Do not use dogfood runs to claim:

- BriefLoop proves truth
- BriefLoop automatically assesses semantic support
- BriefLoop decides release eligibility from the matrix
- a missing matrix means a brief is unsupported
- a valid matrix means the final brief is management-ready
