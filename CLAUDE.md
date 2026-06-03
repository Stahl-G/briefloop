# CLAUDE.md

@AGENTS.md

## Context Mode Rule

This repository has two modes:

1. **Repository development mode**
   - If the current directory contains `pyproject.toml`, `src/`, `tests/`, or `scripts/`, treat this as the source repository.
   - In this mode, `AGENTS.md` and `CLAUDE.md` are contributor instructions for developing this tool.
   - Debug code, edit files, and run tests normally.

2. **Generated workspace mode**
   - If the current directory contains `config.yaml`, `sources.yaml`, `user.md`, and `input/`, treat this as an end-user brief workspace.
   - In this mode, `user.md` is user context, and only `input/` contains source evidence.
   - Do not treat repository README, examples, agent docs, or generated config files as source evidence.

Before running `multi-agent-brief run`, identify which mode you are in.

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
| Init workspace | `multi-agent-brief init ../mabw-workspace --language zh-CN --company "Name" --industry finance --title "Brief" --audience management --source-profile research` |
| Run pipeline | `multi-agent-brief run --config ../mabw-workspace/config.yaml` |
| Doctor check | `multi-agent-brief doctor --config ../mabw-workspace/config.yaml` |
| Source decide | `multi-agent-brief sources decide --config ../mabw-workspace/config.yaml` |
| Merge sources | `multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge` |
| Run tests | `python -m pytest -q` |
| Agent config check | `python scripts/generate_agent_configs.py --check` |

### Init: interactive vs non-interactive

**`multi-agent-brief init` (no args)** → enters interactive wizard with 13 prompts. The agent must STOP and let the user answer each question. Do NOT pre-fill answers.

**`multi-agent-brief init ../mabw-workspace --language zh-CN --company "Name" --industry finance --title "..." --audience management --source-profile research`** → non-interactive, uses CLI args. Agent can run this directly without asking user.

**Important:** If you pass even ONE arg (like `--language`), the entire interactive wizard is SKIPPED. So either pass ALL args or pass NONE.

**Agent behavior in non-interactive environments** (Bash tool, CI, pipe):
- If no CLI args are passed and stdin is not a TTY, init uses default settings and prints guidance.
- Agents should either:
  - (A) Use AskUserQuestion to collect user preferences, then pass ALL args as CLI args.
  - (B) Tell the user to run setup from the repo, then initialize the workspace outside the repo, e.g. `multi-agent-brief init ../mabw-workspace`.
- Do NOT attempt to pipe stdin to the init wizard — the Bash tool does not support interactive input.

### Generate a brief (workflow)

1. Ask user: company, industry, title, audience (if not using interactive init)
2. `init` workspace
3. Ensure source files in `../mabw-workspace/input/` (`.md`, `.txt`, `.json`)
4. `run` pipeline → show `../mabw-workspace/output/brief.md`
5. If audit fails → show findings → fix → re-run

### Real Brief Generation Rule

When the user asks to generate, run, improve, or deliver a real brief/workspace,
do NOT stop after `multi-agent-brief run`.

`multi-agent-brief run` is only the deterministic collection / ledger / draft / artifact step.
For a real user-facing brief, Claude Code must orchestrate the subagents.

**The Python CLI does not automatically spawn Claude Code subagents.** In Claude Code,
use `/generate-brief <workspace>` or ask Claude Code to run the subagent-assisted workflow.
The subagents are prompt-layer orchestration, not Python SDK calls.

Required sequence:

1. Initialize or locate the workspace.
2. Use the `source-planner` subagent when sources/search tasks need to be created, reviewed, or improved.
3. Run `multi-agent-brief doctor --config <workspace>/config.yaml`.
4. Run `multi-agent-brief run --config <workspace>/config.yaml`.
5. Use the `analyst` subagent to rewrite `output/brief.md` from `output/claim_ledger.json` and `user.md`.
   - The analyst must write in the workspace output language.
   - The analyst must use only claims in claim_ledger.json.
   - Every important statement must preserve `[src:CLAIM_ID]`.
   - The analyst must include source dates where available.
6. Use the `editor` subagent to polish the final brief.
   - Remove internal process residue.
   - Remove invalid `[SRC:]`, `[SOURCE:]`, `[src:]` markers.
   - Preserve valid `[src:CLAIM_ID]`.
7. Use the `auditor` subagent to audit `brief.md` against `claim_ledger.json`.
8. Re-run formatter or DOCX conversion so `brief.docx` reflects the edited final Markdown.
9. Only then summarize final artifacts to the user.

Hard rule: if `.claude/agents/analyst.md`, `.claude/agents/editor.md`, or `.claude/agents/auditor.md` exists,
use those subagents for real brief delivery. Do not silently deliver the deterministic Python draft as final.

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
