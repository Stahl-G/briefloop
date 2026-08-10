# CLAUDE.md

## Claude Code Role

This file is for developing and running BriefLoop inside Claude Code.

For runtime-neutral instructions, see `AGENTS.md`. The active runtime is the
SQLite-only Codex ControlStore path; the legacy JSON control plane and its
Claude Code writer command (`/briefloop`) are removed.

## First Response In Claude Code

If the user greets you, asks what to do next, or asks how to use BriefLoop from
Claude Code, point them to the Codex runtime path first:

```text
briefloop init <workspace> --from-onboarding onboarding.json
briefloop runtime install --workspace <workspace> --runtime codex
briefloop run --workspace <workspace> --runtime codex
```

Then follow the Store-derived next action:

```text
briefloop runtime next --workspace <workspace>
```

`run` returns the current `CoreRunNextAction`; `runtime next` / `invocation-*` /
`apply` drive the SQLite ControlStore runtime. The legacy five-verb writer
command and `/generate-brief` delegated workflow are removed.

## Standard Claude Code Path

For a real brief workspace:

```bash
briefloop onboard
briefloop init <workspace> --from-onboarding onboarding.json
briefloop runtime install --workspace <workspace> --runtime codex
briefloop run --workspace <workspace> --runtime codex
```

Then operate the run through the Store-derived action:

```bash
briefloop runtime next --workspace <workspace>
briefloop runtime invocation-start --workspace <workspace>
briefloop runtime invocation-validate --workspace <workspace> --envelope <path>
briefloop runtime invocation-accept --workspace <workspace> --envelope <path>
briefloop runtime apply --workspace <workspace> --action <path>
```

For a demo workspace:

```bash
briefloop init <workspace> --demo
briefloop run --workspace <workspace> --runtime codex
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
briefloop version
briefloop onboard
briefloop init <workspace> --from-onboarding onboarding.json
briefloop run --workspace <workspace> --runtime codex
briefloop runtime next --workspace <workspace>
briefloop status --workspace <workspace> --json
briefloop doctor --config <workspace>/config.yaml
python scripts/generate_agent_configs.py --check
```

## Context Mode

When the user provides a workspace path, treat that path as the workspace even if the current shell is inside the source repository.

Workspace evidence comes from workspace input files, source configuration, collected provider outputs, and intermediate artifacts. Repository docs, examples, README files, and agent configs are development references.

## Runtime Roles

The Codex runtime dispatches role invocations from the packaged workspace kit
(`briefloop runtime install --runtime codex`). The role inventory is the 8-role
Codex kit:

```text
source-planner
→ source-provider
→ scout
→ screener
→ claim-ledger
→ analyst
→ editor
→ auditor
```

The role instructions live in `configs/agent_roles.yaml` and the packaged kit
`src/multi_agent_brief/runtime_kits/codex/`. Python CLI commands provide setup,
source discovery, validation, control, and rendering; the runtime host binds
Store-derived actions to deterministic domain services.

## Generated And Hand-Maintained Files

Generated platform adapter files come from:

```text
configs/agent_roles.yaml
scripts/generate_agent_configs.py
```

Generated targets are limited to the packaged Codex runtime kit
(`src/multi_agent_brief/runtime_kits/codex/`) and `docs/agents/`.

Hand-maintained operating contracts:

```text
AGENTS.md
CLAUDE.md
.agents/AGENTS.md
.agents/skills/briefloop/
```

Do not regenerate hand-maintained operating contracts from `configs/agent_roles.yaml`.

## Focused Tests

For launcher and runtime handoff changes:

```bash
python -m pytest tests/test_start_commands.py tests/test_runtime_assets.py tests/test_agent_config_generation.py -q
```

For onboarding changes:

```bash
python -m pytest tests/test_onboarding*.py tests/test_init*.py -q
```

For skill contract changes:

```bash
python -m pytest tests/test_skill_contracts.py tests/test_briefloop_skill_freshness.py -q
```

For final validation:

```bash
python -m pytest -q
```

## Development Governance

Prefer deletion and simplification over new abstraction.

Python CLI is harness/tooling, not the brief-generation runtime. Do not add a Python full-run pipeline, `BriefPipeline`, `prepare` runtime, or a new full-run generator.

`main.py` should remain a thin CLI router. Command behavior belongs in command modules.

`AGENTS.md` and `SKILL.md` files are operating contracts. Keep them short, concrete, and positive. Use frontmatter descriptions for routing and `references/` for long material.

When documenting agent behavior, describe the active path first: inputs, action, output, handoff. Place deprecated paths and failure cases in tests, validators, or legacy stubs rather than in runtime-facing prompt text.

If an issue is already fixed, report the evidence and stop instead of making unnecessary changes.

Before final response, report files changed, tests run, and known risks.
