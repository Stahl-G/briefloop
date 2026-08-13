# BriefLoop DeepSeek Harness (DSH) runtime kit — experimental

Status: **experimental 0.16.0 slice**. This kit is workspace comfort material
for driving a BriefLoop workspace from a DeepSeek Harness session. It never
writes the SQLite ControlStore, never decides legality, and the Store never
re-binds on install. Fresh Store initialization remains Codex-bound in this
slice.

## What is in the kit

- `presets/briefloop-<role>/agent.cordis.yml` — one DSH agent preset per
  BriefLoop role (source-planner, source-provider, scout, screener,
  claim-ledger, analyst, editor, auditor). Each preset carries the role
  contract as its persona plus read-only shell/filesystem/todo/skill tool rows.
- `presets/briefloop-<role>/preset.yml` — display metadata for each preset.
- `skills/briefloop/SKILL.md` — the DSH BriefLoop operator skill.
- `skills/briefloop/references/controlstore-v2.md` — the operating protocol
  reference.

Preset files are generated from `configs/agent_roles.yaml` by
`scripts/generate_agent_configs.py --target dsh`; edit the manifest and
regenerate instead of editing presets by hand.

## Install into a workspace

```bash
briefloop runtime install --workspace <workspace> --runtime dsh
briefloop run --workspace <workspace> --runtime dsh
```

`run --runtime dsh` prints the DSH handoff; it is a launcher and never
generates the brief through a Python pipeline.

## Make presets discoverable in DSH

Copy each `presets/briefloop-<role>` directory into the DSH preset root
(`${DSH_HOME:-$HOME/.dsh}/.agent-presets/` by default). One preset directory
per role; the roster mounts them like any other preset.

## Operating protocol

1. One DSH session runs the operator skill and the `briefloop` CLI.
2. `briefloop runtime continue --workspace <workspace>` returns the exact
   Store-derived decision.
3. For `delegate` with `delegate_exact_role`, start a DSH subagent on the
   matching BriefLoop preset and hand it the exact `RoleTaskEnvelope`.
4. The subagent writes only the envelope's allowed scratch files, runs the
   embedded preflight commands, and returns; the operator runs
   `runtime continue` again.
5. Deterministic transitions and Human decisions go through exact Store
   actions only (`runtime apply`), never through prompt text or a plugin.

## Boundaries

- The kit is replaceable; reinstall with the same command.
- DSH plugin tools that wrap the CLI must call the CLI only — never open
  `briefloop.db` directly.
- Do not edit presets to weaken role contracts; the deterministic Python
  layer revalidates every proposal regardless of preset text.
