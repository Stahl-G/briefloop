# CLAUDE.md

@AGENTS.md

## Claude Code Specific Instructions

### Setup (do this first after clone)

```bash
bash scripts/setup.sh
source .venv/bin/activate
```

Do NOT attempt to run `multi-agent-brief` CLI without first running setup. The package must be installed in editable mode.

### Quick command reference

| Task | Command |
|------|---------|
| Setup | `bash scripts/setup.sh && source .venv/bin/activate` |
| Init workspace | `multi-agent-brief init my-workspace --language zh-CN --company "Name" --industry solar --title "Weekly Brief" --audience management --source-profile research` |
| Run pipeline | `multi-agent-brief run --config my-workspace/config.yaml` |
| Doctor check | `multi-agent-brief doctor --config my-workspace/config.yaml` |
| Run tests | `python3 -m pytest -q` |
| Demo | `multi-agent-brief init --demo && multi-agent-brief run --config brief-demo/config.yaml` |
| Regenerate agent configs | `python3 scripts/generate_agent_configs.py --write` |
| Check agent configs | `python3 scripts/generate_agent_configs.py --check` |

### Workflow when user asks to generate a brief

1. Confirm the workspace exists; if not, run `init` with user's parameters
2. Ensure source files are in `workspace/input/` (`.md`, `.txt`, or `.json`)
3. Run `multi-agent-brief run --config workspace/config.yaml`
4. Show the user the generated `workspace/output/brief.md`
5. If audit fails, show findings and help fix before re-running

### Source profiles

- `conservative` — official sources only, no web search
- `research` — balanced official + industry + RSS (default)
- `aggressive_signal` — broad signal discovery including social media
- `custom` — user edits sources.yaml manually
- `llm_decide` — generates agent-readable discovery policy, does NOT call LLM at init time

### Project layout

```text
src/multi_agent_brief/
  cli/          # CLI entry points (main.py, init_wizard.py)
  core/         # Pipeline, config, schemas, claim ledger
  agents/       # Scout, Screener, Analyst, Auditor, Editor, Formatter
  audit/        # Deterministic, quality harness, final quality, semantic
  sources/      # Source providers (manual, rss, stubs), registry, doctor
  connectors/   # Legacy connector stubs (not wired into pipeline)
  delivery/     # Delivery stubs (email, slack, feishu)
  outputs/      # Source map, docx/pdf stubs
  models/       # Model provider abstraction
configs/        # agent_roles.yaml (single source of truth)
scripts/        # setup.sh, generate_agent_configs.py
tests/          # pytest test suite
```

### Coding conventions

- Python 3.9+ compatible, type hints on all public functions
- Dataclasses for data models, ABC for interfaces
- No heavy dependencies in core — PyYAML is the only optional dep
- Tests use `tmp_path` fixture, no network calls in tests
- All generated files (`.codex/`, `.claude/agents/`, `.agents/`) have `AUTO-GENERATED` header — edit `configs/agent_roles.yaml` instead

### Do not

- Do not run `multi-agent-brief run` without a valid workspace (run `init` first)
- Do not put API keys in config files — use env var references only
- Do not modify generated agent config files directly — use `scripts/generate_agent_configs.py`
- Do not let `user.md` enter `input/` directory — it is agent context, not source evidence
