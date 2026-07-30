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
- Tavily discovery authority is distinct from execution authority. The
  Experimental runtime-first route keeps the credential in workspace `.env`
  until explicit Human rotation/removal, but each separately Human-confirmed,
  Store-recorded attempt permits at most one provider call. Exact replay does
  not redial, failures do not auto-retry, and safe failed responses remain
  auditable without source or execution authority. A successful attempt uses
  one Intake transaction to promote a Store-native source pack plus execution
  authorization. Search snippets remain claims-ineligible, and legacy source
  commands are not a fallback. Synthetic transport is tested; live results and
  cost are NOT MEASURED.
- Schema 11 adds append-only source-acquisition attempt authorization and
  transaction-relation records. Pre-schema-11 discovery histories remain
  verifiable under their historical semantics but cannot be retroactively
  authorized for another provider call.
- Legacy Improvement JSON/JSONL remains inert. Experimental post-final review
  stores one qualified advisory assessment, Human accept/reject/defer,
  Human-edited guidance drafts, and separate approval/status revisions through
  SQLite Receipts. Static HTML stays read-only; only the secured loopback
  Review Session accepts strict Human commands. No approved guidance is
  consumed by later runs until the separate snapshot/precedence unit ships.
- Retained legacy commands and assets may remain in the tree until the separate
  deletion unit, but the authority guard prevents them from acting on a SQLite
  workspace or continuing a JSON-only workspace.
- Do not restore a Python full-pipeline as the standard generation path.
- Do not treat roadmap goals as implemented modules.
- Do not move hard constraints into user notes when validators or audit checks should enforce them.
- Do not let runtime-specific adapters change the public artifact expectations.
