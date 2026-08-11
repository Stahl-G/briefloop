# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-codex-skill-v0.3.0`

BriefLoop is the only current project and product name. The former project
acronym is retired; literal compatibility identifiers may survive only on
explicitly classified historical or compatibility surfaces.

## Release Lines

- Prior release line: `v0.14.0`.
- Current release line: `v0.15.2`. It removes the legacy JSON control-plane
  runtime; the SQLite Codex ControlStore runtime is the only active runtime.
- Prepared release line: `v0.15.2`. The tag and non-draft GitHub Release are
  the release authority; later development-main capabilities are not silently
  attributed to the existing tag.

This skill is verified against the post-v0.15.2 development tree. Schema-17
post-final execution evidence and schema-18 solar-stock search/market-data
controls are development-main capabilities until a later release cut names
them explicitly.

## Active Runtime Contract

- Codex is the only active fresh runtime.
- `briefloop.db`, ControlStore receipts, and ledger relations are the sole
  runtime authority.
- Strict Pydantic requests are the only write boundary.
- `CoreRunNextAction` is the sole sequence authority, with exactly
  `delegate`, `deterministic`, `human_decision`, `blocked`, and `complete`.
- Every agent task is a Receipt-backed invocation governed by a
  `RoleTaskEnvelope`; agent output is scratch-only proposal material.
- The root host alone performs `invocation-accept`, `invocation-fail`, and
  deterministic `runtime apply` effects.
- Human decisions require the exact strict request named by the action.
- Stale or forged actions and envelopes fail closed.
- `package_ready` and `delivered` are distinct terminal effects.

Support status remains Experimental until a real public-safe Codex run proves
the end-to-end packaged runtime path. Traceability is not truth proof or a
quality guarantee.

## Post-Final Product Surfaces

The static Brief and Quality views remain read-only projections. The local
actionable review session is a separate loopback surface whose Human actions
enter Store-backed services; editing the exported HTML itself never writes
authority. AI Second Opinion remains optional advisory and NOT MEASURED.

On unreleased development main, Store-native post-final Human review records
disposition, edited guidance, and separate approval/status. A separate explicit
`briefloop runtime successor-start` transaction can freeze compatible
active-approved guidance for Analyst/Editor only when
`--include-approved-guidance` is present. Current direction and evidence govern;
utility is NOT MEASURED and guidance has no Claim Ledger, Gate, repair,
finalize/delivery, or Core authority.

The experimental `solar-stock-periodic` ReportPack is schema-18 and fresh-only.
Its Tavily source plan freezes 20 independent equity/event/theme tasks, up to
20 results per task, grouped Extract batches, one conditional 30-day backfill,
and an 800-unique-URL safety envelope. Its deterministic market-data channel
freezes provider/manual inputs and calculated comparison fields; unavailable
values remain explicit instead of being fabricated.

## Unsupported And Retired

- JSON-only workspaces, JSON authority, migration, import, dual-read,
  dual-write, and compatibility fallback
- `operator` or another runtime as a fallback for a Codex run
- legacy handoff, state, gates, repair, finalize, delivery, controls,
  provenance, feedback, improvement, and source-mutator commands on SQLite
- `eval-cases` and `experiments 080`
- legacy Improvement JSON/JSONL files, `improve` mutators, fast-rerun, and the
  Semantic Assessment Report producer/projection/adjudication stack
- direct agent writes to SQL, receipts, ledger rows, canonical artifacts,
  frozen revisions, approval, gates, or delivery
- reconstructing legality from status, HTML, Markdown, JSON/JSONL, Quality
  Panel, checkout bytes, file existence, prompts, or memory

## Repo And Public-Claim Boundary

Detailed repository work uses `references/repo-development.md`; release and
demo wording uses `references/public-claims.md`. Current code, tests,
`docs/architecture-status.md`, `docs/support-matrix.md`, and CLI help override
older prose. Planned controls remain not authoritative until code, tests, and
the support matrix expose them.
