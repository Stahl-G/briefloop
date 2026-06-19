# Naming And Compatibility

Read this when the task touches BriefLoop/MABW naming, public links, release
notes, or compatibility claims.

## Current Names

- Public project name: BriefLoop
- Historical implementation lineage: MABW
- CLI: `multi-agent-brief`
- Claude command: `/mabw`
- Python module: `multi_agent_brief`
- Package/distribution: `multi-agent-brief-workflow`
- Experiment namespace: MABW-080

## Compatibility Rules

- Do not rename runtime surfaces by accident.
- Do not change workspace artifact names for public framing only.
- When public docs say BriefLoop, keep compatibility notes for `multi-agent-brief`,
  `/mabw`, package/module paths, and MABW experiment IDs.
- BriefLoop-090 can be a future experiment/readiness label; v0.9.0 is a semver
  release. `experiments 090` is not a current CLI namespace. Do not conflate
  them.

## Current Authority

Use `docs/briefloop-naming.md`, `docs/architecture-status.md`, and
`docs/support-matrix.md` for current public compatibility wording.
