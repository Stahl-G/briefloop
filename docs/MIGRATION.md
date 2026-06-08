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

- Do not restore a Python full-pipeline as the standard generation path.
- Do not treat roadmap goals as implemented modules.
- Do not move hard constraints into user notes when validators or audit checks should enforce them.
- Do not let runtime-specific adapters change the public artifact expectations.
