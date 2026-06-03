# CLAUDE.md

@AGENTS.md

## Claude Code

### Setup (first after clone)

```bash
bash scripts/setup.sh && source .venv/bin/activate
```

Windows (PowerShell):

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
multi-agent-brief version
```

### Commands

| Task | Command |
|------|---------|
| Init workspace | `multi-agent-brief init ws --language zh-CN --company "Name" --industry finance --title "Brief" --audience management --source-profile research` |
| Run pipeline | `multi-agent-brief run --config ws/config.yaml` |
| Doctor check | `multi-agent-brief doctor --config ws/config.yaml` |
| Source decide | `multi-agent-brief sources decide --config ws/config.yaml` |
| Merge sources | `multi-agent-brief sources decide --config ws/config.yaml --merge` |
| Run tests | `python -m pytest -q` |
| Agent config check | `python scripts/generate_agent_configs.py --check` |

### Init: interactive vs non-interactive

**`multi-agent-brief init` (no args)** → enters interactive wizard with 13 prompts. The agent must STOP and let the user answer each question. Do NOT pre-fill answers.

**`multi-agent-brief init ws --language zh-CN --company "Name" --industry finance --title "..." --audience management --source-profile research`** → non-interactive, uses CLI args. Agent can run this directly without asking user.

**Important:** If you pass even ONE arg (like `--language`), the entire interactive wizard is SKIPPED. So either pass ALL args or pass NONE.

**Agent behavior in non-interactive environments** (Bash tool, CI, pipe):
- If no CLI args are passed and stdin is not a TTY, init uses default settings and prints guidance.
- Agents should either:
  - (A) Use AskUserQuestion to collect user preferences, then pass ALL args as CLI args.
  - (B) Tell the user to run `! cd <repo> && multi-agent-brief init <target>` in their terminal.
- Do NOT attempt to pipe stdin to the init wizard — the Bash tool does not support interactive input.

### Generate a brief (workflow)

1. Ask user: company, industry, title, audience (if not using interactive init)
2. `init` workspace
3. Ensure source files in `ws/input/` (`.md`, `.txt`, `.json`)
4. `run` pipeline → show `ws/output/brief.md`
5. If audit fails → show findings → fix → re-run

### Source profiles

- `conservative` — official only
- `research` — official + industry + RSS (default)
- `aggressive_signal` — broad signals, more noise
- `custom` — user edits sources.yaml
- `llm_decide` — agent-readable discovery policy, no LLM at init

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

## Conversational Onboarding Policy

When the user asks to initialize, start, or configure a brief workspace:

1. Do not use AskUserQuestion as the primary onboarding path.
2. Ask plain-language questions directly in chat.
3. Ask at most 4 questions before choosing recommended defaults.
4. Let the user answer naturally in one message.
5. If the user says "unknown", "default", or "choose for me", choose defaults.
6. Convert answers internally to `onboarding.json`.
7. Run: `multi-agent-brief init --from-onboarding onboarding.json`
8. Use AskUserQuestion only for optional single-choice refinements.
9. Never use AskUserQuestion for required free-text fields such as company name, title, keywords, focus areas, or source descriptions.
10. Never expose YAML, schema, source_profile, selector_max_items, retrieval_provider, output_formats, or CLI flags unless the user asks as a developer.

Known issue: AskUserQuestion "Other" free-text input may be dismissed by Claude Code TUI in some terminals. Therefore it must not be used for required free-text onboarding.
