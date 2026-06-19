# BriefLoop Naming and Compatibility Policy

## Public name

The public project name is **BriefLoop**.

## Subtitle

Open-source loop engineering for auditable business briefings.

## Legacy implementation name

MABW remains the implementation lineage and compatibility surface during the
v0.9 compatibility period.

## Compatibility rule

PR0 does not break existing commands, package names, runtime artifacts,
workspace formats, experiment IDs, reference-run paths, or archived run IDs.

The current compatibility surfaces remain:

- `https://github.com/Stahl-G/briefloop` public repository URL
- `multi-agent-brief` CLI
- `/mabw` Claude commands
- `multi_agent_brief` Python package/module path
- `multi-agent-brief-workflow` distribution package name
- existing artifact names and workspace formats
- MABW experiment IDs such as `MABW-080`

GitHub redirects from the historical
`https://github.com/Stahl-G/multi-agent-brief-workflow` URL are expected to
remain available during the compatibility period, but new public documentation
should use `https://github.com/Stahl-G/briefloop`.

## Naming layers

- BriefLoop: public project name
- brief-loop engineering: paradigm / methodology
- BriefCI: reserved optional technical sub-layer for gates, regression checks,
  and release eligibility; not the public project name
- MABW: historical implementation name and compatibility surface

## Allowed language

- BriefLoop, formerly MABW
- BriefLoop / MABW compatibility period
- BriefLoop is open-source loop engineering for auditable business briefings
- BriefLoop turns briefing failures into findings, repairs, regression cases,
  and release decisions
- MABW remains the current CLI/runtime compatibility surface

## Forbidden language

- BriefLoop proves truth
- BriefLoop eliminates hallucinations
- BriefLoop replaces human review
- BriefLoop makes reports ready to send
- BriefLoop is an autonomous self-improving agent
- Multi-agent architecture itself guarantees quality

## Name-risk note

BriefLoop is the open-source project-facing name during the v0.9 compatibility
period. This is not a trademark clearance statement.
