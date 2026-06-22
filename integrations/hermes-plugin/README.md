# MABW Hermes Plugin

A thin Hermes plugin adapter for Multi-Agent Brief Workflow / BriefLoop.

Hermes tools + one slash command:

- `mabw_env_doctor` — inspect local MABW/Hermes readiness
- `mabw_create_onboarding` — write `onboarding.json` from chat-collected answers
- `mabw_init_workspace` — `multi-agent-brief init --from-onboarding`
- `mabw_run_handoff` — `multi-agent-brief run --workspace --runtime hermes`
- `/mabw <workspace>` — print the workflow path

## Installed Skills

- `briefloop`: BriefLoop operator protocol. Use with `skill_view("briefloop")`
  when deciding how to operate workspaces, gates, repair, status, public claims,
  or compatibility surfaces.
- `mabw-workflow`: Hermes runtime workflow helper. Use with
  `skill_view("mabw-workflow")` when running the MABW workflow through Hermes
  delegation.

`briefloop` is not a slash command. `mabw-workflow` is not the canonical
BriefLoop operator protocol; it is the Hermes workflow helper.

## Install

From the MABW repository root:

```bash
# Copy into Hermes plugins directory
cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw

# Enable
hermes plugins enable mabw

# Verify
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

One-liner:

```bash
rm -rf ~/.hermes/plugins/mabw && cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw && hermes plugins enable mabw
```

## Requirements

- `multi-agent-brief` on PATH, or `MABW_BIN=/path/to/multi-agent-brief`
- Hermes with plugin support

## Use in Hermes

```
/mabw /Users/you/mabw-workspace
```

Collect the brief profile in chat, then call the tools in order:

```text
mabw_create_onboarding  → mabw_init_workspace  → mabw_run_handoff
```

After handoff, read `agent_handoff.md` and continue the delegated workflow.
