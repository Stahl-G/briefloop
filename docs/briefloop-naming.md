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
- `briefloop` distribution package name
- historical `multi-agent-brief-workflow` distribution references in archived
  release notes and compatibility docs
- existing artifact names and workspace formats
- MABW experiment IDs such as `MABW-080`

GitHub redirects from the historical
`https://github.com/Stahl-G/multi-agent-brief-workflow` URL are expected to
remain available during the compatibility period, but new public documentation
should use `https://github.com/Stahl-G/briefloop`.

## Deep rename deferral

v1.0 completion means product-facing rename completion, not grep-zero removal
of every historical or implementation name.

These deep renames are explicitly not v1.0 blockers:

- renaming the Python module path `multi_agent_brief` to `briefloop`;
- globally rewriting `mabw.*` schema ids or historical
  `multi-agent-brief-*` schema ids;
- changing historical run IDs, archived reference runs, old release notes, or
  existing workspace contents;
- deleting `/mabw` or `multi-agent-brief` compatibility entrypoints in the same
  release;
- moving old Hermes plugin or integration directory names where that would
  break source-clone or installed-plugin paths.

Post-v1 deep rename or shim migration may be considered only if user friction
or packaging evidence justifies it. Such a migration must preserve old
workspace compatibility, keep package-index install paths clear, and include
source-clone plus non-editable install smoke coverage. It must not rewrite
frozen archives or schema ids in place.

## Compatibility quarantine

Remaining MABW and `multi-agent-brief` references are compatibility records,
not the public product identity. Keep them in explicit compatibility,
history, schema, packaging, or test surfaces. Do not use them as first-user
instructions, launch claims, or recommended writer paths.

| Compatibility surface | Status | Allowed placement | Not allowed |
|---|---|---|---|
| `/mabw` | Deprecated Claude compatibility alias | `.claude/commands/mabw.md`, compatibility docs, tests | README first screen, launch path, new-user examples |
| `multi-agent-brief` | Compatibility CLI and script entrypoint | CLI compatibility notes, package tests, existing automation docs | Primary shell examples for new users |
| `multi_agent_brief` | Python module compatibility surface | Python imports, packaging metadata, source tests | User-facing product name |
| `briefloop` | Distribution/package surface | `pyproject.toml`, PyPI/pipx packaging docs, release-checklist package smokes | Public install claims before a real package-index artifact is published and smoke-tested |
| `multi-agent-brief-workflow` | Historical distribution/package compatibility reference | Old release notes, migration notes, archived install references | `pyproject.toml`, setup banners, first-run product copy, new package-index instructions |
| `MABW-080` | Archived experiment namespace | Experiment docs, scorecards, release archives, reference-run reproduction, tests | Product workspace guidance, WorkBuddy first-user flow, launch path |
| `BriefLoop-090` | Archived experiment/readiness label | Reference-run notes, experiment closeout, research/evaluation discussion | Product version label, CLI namespace, first-user path |
| `mabw.*` schema ids | Old-workspace compatibility ids | Validators, schema fixtures, migration notes | New public product messaging |
| Old release notes and tech reports | Historical archive | `CHANGELOG.md`, `docs/mabw-*`, release references | Current capability claims |

Compatibility surfaces must never imply that BriefLoop is a truth proof,
delivery approval system, autonomous agent runtime, or output-quality
improvement proof.

For v1.0, MABW-080 / BriefLoop-090 are archived measurement surfaces. They are
kept for public evidence audit and explicit experiment reproduction, not for
new-user onboarding, WorkBuddy setup, README launch examples, or ordinary
workspace operation.

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
| Claude delegated stage workflow | `/generate-brief <workspace>` | Supported when following generated Claude handoff or advanced Claude operation; not a first-user writer path |

`/briefloop` is the only recommended Claude writer command for new users. The
BriefLoop skill is an agent operator protocol surface, not the slash-command
implementation.
For Claude runs launched with `briefloop run --runtime claude`, the generated
handoff may instruct the Orchestrator main agent to continue stage execution
with `/generate-brief <workspace>`. That command runs the delegated stage
workflow; `/briefloop run <workspace>` only creates or refreshes handoff files
and does not execute specialists or complete stages.

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
