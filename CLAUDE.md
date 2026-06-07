# CLAUDE.md

## Claude Code Role

This file is for developing and running Multi-Agent Brief Workflow inside Claude Code.

For runtime-neutral instructions, see `AGENTS.md`. For Claude Code execution, use the repository slash command and subagents.

## Standard Claude Code Path

For a real brief workspace:

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
```

Then run in Claude Code:

```text
/generate-brief ../mabw-workspace
```

For a demo workspace:

```bash
multi-agent-brief init ../mabw-demo --demo
```

Then run:

```text
/generate-brief ../mabw-demo
```

## Repository Development Setup

```bash
bash scripts/setup.sh
source .venv/bin/activate
python -m pytest -q
```

Windows PowerShell:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

## Useful Commands

```bash
multi-agent-brief version
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge
multi-agent-brief finalize --config ../mabw-workspace/config.yaml
python scripts/generate_agent_configs.py --check
```

## Context Mode

When the user provides a workspace path, treat that path as the workspace even if the current shell is inside the source repository.

Workspace evidence comes from workspace input files, source configuration, collected provider outputs, and intermediate artifacts. Repository docs, examples, README files, and agent configs are development references.

## Subagent Workflow

Claude Code uses the external subagent workflow:

```text
source-planner
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
→ formatter/finalize
```

Python CLI commands provide setup, source discovery, input governance, audit checks, runtime handoff, and final rendering tools. The auditable brief is written by subagents and rendered through `finalize`.

## Generated Files

Prefer editing generation sources:

```text
configs/agent_roles.yaml
scripts/generate_agent_configs.py
src/multi_agent_brief/hermes/
```

After editing generated content:

```bash
python scripts/generate_agent_configs.py
python scripts/generate_agent_configs.py --check
```

## Focused Tests

For launcher and runtime handoff changes:

```bash
python -m pytest tests/test_start_commands.py tests/test_hermes_adapter.py tests/test_agent_config_generation.py -q
```

For onboarding changes:

```bash
python -m pytest tests/test_onboarding*.py tests/test_init*.py -q
```

For final validation:

```bash
python -m pytest -q
```

## Implementation Style

Keep user-facing prompts and skills short and positive.

Use complete section rewrites for architecture language changes instead of partial line patches.

When changing generated files, update the generator source and regenerate derived outputs.
