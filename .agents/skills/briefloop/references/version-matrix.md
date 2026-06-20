# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-operator-skill-v0.1`
Last verified against BriefLoop runtime: `v0.9.1`
Public project name: BriefLoop
Historical implementation name: MABW

## Supported Current Surfaces

- CLI: `multi-agent-brief`
- Claude command: `/mabw`
- No `/briefloop` user command; BriefLoop skill is an agent protocol surface.
- Python package/module path: `multi_agent_brief`
- Distribution package name: `multi-agent-brief-workflow`
- Assessment targets:
  - `delivery_brief`
  - `auditable_brief`
- MABW-080 experiment operations:
  - `validate-case`
  - `scaffold-condition`
  - `register-run`
  - `score-run`
  - `export-blind-pack`
  - `import-assessment`
  - `summarize`

## Compatibility Rules

- Do not rename runtime surfaces unless the task is explicitly a compatibility
  migration.
- Do not describe planned v0.9+ support-sufficiency controls as implemented.
- BriefLoop-090 is a future readiness/fresh-rerun label, not a current CLI namespace or supported command surface. Current experiment commands remain under `multi-agent-brief experiments 080`.
- If runtime behavior conflicts with this skill, prefer:
  - `docs/architecture-status.md`
  - `docs/support-matrix.md`
  - current CLI help
  - the workspace's generated runtime handoff

## Planned / Not Yet Authoritative

These are roadmap directions unless current code, tests, and support matrix say
otherwise:

- Atomic Claim Graph
- Evidence Span Registry
- Claim-Support Matrix
- Finding Candidate System
- Release Eligibility Scorecard
