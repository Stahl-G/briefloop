# BriefLoop Skill Version Matrix

Skill contract version: `briefloop-operator-skill-v0.1`
Last verified against BriefLoop runtime: `v0.9.1` plus post-v0.9.1 mainline support-record surfaces
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
- Experimental optional artifacts on mainline after v0.9.1:
  - Atomic Claim Graph: `atomic_claim_graph.json`
  - Evidence Span Registry: `evidence_span_registry.json`
  - Claim-Support Matrix: `claim_support_matrix.json`
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
- Do not describe planned v0.9+ support-sufficiency controls as completed.
- BriefLoop-090 is a future readiness/fresh-rerun label, not a current CLI namespace or supported command surface. Current experiment commands remain under `multi-agent-brief experiments 080`.
- If runtime behavior conflicts with this skill, prefer:
  - `docs/architecture-status.md`
  - `docs/support-matrix.md`
  - current CLI help
  - the workspace's generated runtime handoff

## Planned / Not Yet Authoritative

These are roadmap directions unless current code, tests, and support matrix say
otherwise:

- Finding Candidate System
- Release Eligibility Scorecard
- semantic support scoring
- support-sufficiency gates
