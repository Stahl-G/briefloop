# Migration Notes

This page explains the public architecture migration from older Python-pipeline language to the current Orchestrator-first framing.

| Older framing | Current framing |
|---|---|
| Python owns the complete brief workflow | Runtime main agent coordinates delegated subagents |
| `prepare` as the primary generation path | `run` as a runtime handoff launcher |
| Python classes as workflow agents | External runtime roles as subagents |
| Prompt-only workflow control | Contract-governed handoff and validation |
| Quality as a late editing concern | Quality as part of evaluation and feedback loops |
| Private feedback mixed into context | Feedback is governed and separated from evidence |

## Migration Rules

- The current cutover is intentionally fresh-only. A new Codex run writes one
  SQLite `briefloop.db`; JSON/JSONL controls are projections only.
- Existing JSON-only workspaces are unsupported. There is no importer, silent
  migration, dual-read, dual-write, compatibility mode, or fallback.
- `config.yaml` and `sources.yaml` are strict initialization inputs. Their exact
  bytes and normalized bindings are frozen into SQLite; later edits cannot
  change run legality.
- The legacy JSON control-plane runtime is deleted: its CLI command modules
  (`state`, `gates`, `feedback`, `repair`, `improve`, `provenance`, `controls`,
  `approval`, `release`, `inputs`, `semantic-support`, `audit`, `finalize`,
  `deliver`, `analysis-blocks`, `claude`, `hermes`, `workbuddy`), its workspace
  runtime assets (generated role agents, role skills, writer commands, Hermes,
  OpenCode, CodeBuddy, WorkBuddy), and its JSON control files are removed.
  The workspace authority guard classifies only fresh / sqlite / invalid_sqlite.
- Do not restore the deleted legacy JSON runtime or its fail-closed command
  stubs. A workspace is either bootstrapped to SQLite or refused.
- Tavily discovery authority is distinct from execution authority. The
  Experimental runtime-first route keeps the credential in workspace `.env`
  until explicit Human rotation/removal, but each separately Human-confirmed,
  Store-recorded attempt executes a frozen atomic task matrix. Every task
  requests 20 advanced Search results, all eligible unique URLs are Batch
  Extracted in groups of 20, and an under-covered task may receive one
  deterministic 30-day backfill. Exact replay does not redial, failures do not
  auto-retry, and the canonical acquisition bundle retains the exact safe
  exchanges plus per-URL outcomes. Only successful non-empty Extract content
  enters the one Intake transaction; Search snippets are never claims-eligible,
  partial success commits successful URLs only, and zero Search/all-failed
  Extract creates no source or execution authority. HumanSourcePack never counts
  as Tavily success, and legacy source commands are not a fallback. Synthetic
  transport is tested; live results, cost, coverage, and success rate are NOT
  MEASURED.
- Schema 13 adds the normal successor-run and immutable approved-guidance
  snapshot relations for fresh current-schema workspaces. Older development
  SQLite workspaces are unsupported when the schema changes; create a fresh
  workspace. There is no in-product development-schema upgrade path.
- Schema 18 adds the fresh-only Solar Stock Periodic search-plan,
  multi-search acquisition-bundle, per-task outcome, and market-data snapshot
  boundaries. Schema-17 workspaces are not migrated, dual-read, or upgraded in
  place. `solar-stock-periodic` must be initialized in a new schema-18
  workspace; missing market-data snapshots remain missing rather than being
  filled with invented prices or valuation multiples.
- Legacy Improvement JSON/JSONL remains inert. Experimental post-final review
  supports multiple independently Human-authorized append-only assessments on
  one finalized lineage, explicit result selection, Human accept/reject/defer,
  Human-edited guidance drafts, and separate approval/status revisions through
  SQLite Receipts. Generation 2 and later are explicit only. Static HTML stays
  read-only; only the secured loopback Review Session accepts strict Human
  commands. A Human may separately run `briefloop runtime successor-start` with
  a new strict `RunDirection`, new run ID, and the explicit
  `--include-approved-guidance` opt-in. One Core transaction creates the normal
  same-workspace successor and freezes the complete compatible, active-approved
  guidance set for Analyst/Editor only. Current direction and evidence govern;
  guidance is not a fact, source, Claim Ledger input, Gate rule, repair command,
  finalize/delivery authority, or Core policy. Utility is NOT MEASURED.
- Legacy Improvement files and mutators, fast-rerun/080 commands, and the
  Semantic Assessment Report producer/projection/adjudication stack are
  retired. The optional Semantic Assessment Report schema and reference
  validation remain non-blocking. None is a fallback for a SQLite workspace.
- Do not restore a Python full-pipeline as the standard generation path.
- Do not treat roadmap goals as implemented modules.
- Do not move hard constraints into user notes when validators or audit checks should enforce them.
- Do not let runtime-specific adapters change the public artifact expectations.
