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
- Do not restore a Python full-pipeline as the standard generation path.
- Do not treat roadmap goals as implemented modules.
- Do not move hard constraints into user notes when validators or audit checks should enforce them.
- Do not let runtime-specific adapters change the public artifact expectations.
