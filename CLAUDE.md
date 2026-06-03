# CLAUDE.md

@AGENTS.md

## Claude Code

### Setup (first after clone)

```bash
bash scripts/setup.sh && source .venv/bin/activate
```

### Commands

| Task | Command |
|------|---------|
| Init workspace | `multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json` |
| Run pipeline | `multi-agent-brief run --config ../mabw-workspace/config.yaml` |
| Doctor check | `multi-agent-brief doctor --config ../mabw-workspace/config.yaml` |
| Source decide | `multi-agent-brief sources decide --config ../mabw-workspace/config.yaml` |
| Merge sources | `multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge` |
| Run tests | `python -m pytest -q` |
| Agent config check | `python scripts/generate_agent_configs.py --check` |

### Context Mode Rule (Claude Code extension)

When a user request or command provides a workspace path, classify **that path** as the workspace, even if the current working directory is the source repo.

For example, if you are in `/path/to/repo/` and the user says "generate a brief for ../mabw-workspace", the workspace is `../mabw-workspace` — not the repo. Only `<workspace>/input/` and collected provider outputs are source evidence. Repo README, docs, examples, and agent files are never evidence.

### Conversational Onboarding Policy

When the user asks to initialize, start, or configure a brief workspace:

1. Do not use AskUserQuestion as the primary onboarding path.
2. Ask plain-language questions directly in chat.
3. Cover all onboarding fields: company, industry, task, audience, language, cadence, source style, output style, must-watch, forbidden sources. Ask about each one or confirm its default.
4. Let the user answer naturally in one message.
5. If the user says "unknown", "default", or "choose for me", choose defaults.
6. Convert answers internally to `onboarding.json`.
7. Run: `multi-agent-brief init <workspace> --from-onboarding onboarding.json`
8. Use AskUserQuestion only for optional single-choice refinements.
9. Never use AskUserQuestion for required free-text fields such as company name, title, keywords, focus areas, or source descriptions.
10. Never expose YAML, schema, source_profile, selector_max_items, retrieval_provider, output_formats, or CLI flags unless the user asks as a developer.

Known issue: AskUserQuestion "Other" free-text input may be dismissed by Claude Code TUI in some terminals. Therefore it must not be used for required free-text onboarding.

### Python preparation vs Claude final delivery

The Python CLI runs deterministic preparation tools:

```text
Scout → Screener → Claim Ledger → Auditor → Editor → Formatter
```

This produces **intermediate artifacts** (`draft_brief.md`, `claim_ledger.json`, `audit_report.json`, `source_map.md`) — not a final brief. The Python Auditor checks draft facts.

For real user-facing delivery, Claude Code must orchestrate subagents after the preparation:

```text
analyst → editor → final auditor → formatter
```

The Claude final auditor checks the polished text — distinct from the Python draft-level audit.

**Hard rule:** Do not silently deliver the deterministic Python draft as the final brief. The final brief must be written by Claude Code / Codex / external LLM agents using Claim Ledger and audit outputs.

### Source profiles

- `conservative` — official only
- `research` — official + industry + RSS
- `aggressive_signal` — broad signals, more noise
- `custom` — user edits sources.yaml
- `llm_decide` — agent-readable discovery policy, no LLM at init (default)

### Layout

```text
src/multi_agent_brief/
  cli/         main.py, init_wizard.py
  core/        pipeline, config, schemas, claim ledger
  agents/      Scout, Screener, Analyst, Auditor, Editor, Formatter
  audit/       deterministic, quality harness, final quality
  sources/     providers, registry, doctor
configs/       agent_roles.yaml (source of truth)
scripts/       setup.sh, setup.ps1, generate_agent_configs.py
tests/         pytest suite
```

### Rules

- Python 3.9+, type hints, dataclasses, ABC
- Windows native path is PowerShell: `.\scripts\setup.ps1`, `.\.venv\Scripts\Activate.ps1`, `python -m pytest -q`
- WSL is optional, not required; CMD is not the primary support target
- No network calls in tests
- Generated files have `AUTO-GENERATED` header — edit `configs/agent_roles.yaml`
- No API keys in config — use env var refs
- `user.md` is agent context, never put it in `input/`
