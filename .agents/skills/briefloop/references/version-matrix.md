# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-codex-skill-v0.3.0`

BriefLoop is the only current project and product name. The former project
acronym is retired; literal compatibility identifiers may survive only on
explicitly classified historical or compatibility surfaces.

## Release Lines

- Prior release line: `v0.13.0`.
- Prepared release line: `v0.14.0`.
- Treat v0.14.0 as a release target until both its tag and non-draft GitHub
  Release exist. After they exist, treat v0.14.0 as the current release. Do
  not infer release status from this file alone.

This skill is verified against the post-v0.13 source tree that is being cut as
v0.14.0. The skill deliberately contains both version strings so the same
contract check remains valid before and after the version-only release cut.

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

## Read-Only Product Surfaces

The three-page Brief HTML and init web wizard are read-only interaction
surfaces in the v0.14.0 release target. They do not create authority. LAJ
remains Experimental and NOT MEASURED. The Improvement Ledger lifecycle is
retired; a page that reports it unavailable is not a write path.

## Unsupported And Retired

- JSON-only workspaces, JSON authority, migration, import, dual-read,
  dual-write, and compatibility fallback
- `operator` or another runtime as a fallback for a Codex run
- legacy handoff, state, gates, repair, finalize, delivery, controls,
  provenance, feedback, improvement, and source-mutator commands on SQLite
- `eval-cases` and `experiments 080`
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
