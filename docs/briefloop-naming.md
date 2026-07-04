# BriefLoop Naming and Compatibility Policy

## Public name

The public project name is **BriefLoop**.

## Subtitle

Open-source loop engineering for auditable business briefings.

## Legacy implementation name

MABW remains the implementation lineage and compatibility surface during the
v1.0 public-product rename period.

## Compatibility rule

The public rename does not break existing commands, package names, runtime
artifacts, workspace formats, experiment IDs, reference-run paths, or archived
run IDs.

The current compatibility surfaces remain:

- `https://github.com/Stahl-G/briefloop` public repository URL
- `briefloop` public CLI
- `multi-agent-brief` CLI retained for compatibility
- `/briefloop` Claude writer command
- `/mabw` Claude command retained as a deprecated compatibility command
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

## Entrypoint layers

| Layer | Entrypoint | Audience |
|---|---|---|
| Public product name | BriefLoop | Docs, releases, repository identity |
| Public shell command | `briefloop` | Users, docs, first-run examples |
| Compatibility shell command | `multi-agent-brief` | Scripts and existing users during compatibility period |
| Writer command | `/briefloop` | Claude Code writers |
| Compatibility writer command | `/mabw` | Existing Claude Code users during compatibility period |
| Agent protocol | BriefLoop skill | Runtime/coding agents reading operation rules |
| Deterministic control plane | `briefloop`, `multi-agent-brief` | CLI transactions, validators, tests, scripts |
| Legacy delegated command | `/generate-brief <workspace>` | Compatibility/debug only; not a first-user path |

`/briefloop` is the only recommended Claude writer command for new users. The
BriefLoop skill is an agent operator protocol surface, not the slash-command
implementation.

## Allowed language

- BriefLoop, formerly MABW
- BriefLoop / MABW compatibility period
- BriefLoop is open-source loop engineering for auditable business briefings
- BriefLoop turns briefing failures into findings, repairs, regression cases,
  and release decisions
- MABW remains an implementation-lineage and compatibility surface

## Forbidden language

- BriefLoop proves truth
- BriefLoop eliminates hallucinations
- BriefLoop replaces human review
- BriefLoop makes reports ready to send
- BriefLoop is an autonomous self-improving agent
- Multi-agent architecture itself guarantees quality

## Name-risk note

BriefLoop is the open-source project-facing name during the v1.0 public-product
rename period. This is not a trademark clearance statement.
