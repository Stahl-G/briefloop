# BriefLoop Public Documentation

BriefLoop is the only current project and product name. The former project
acronym is retired. Literal compatibility and history identifiers such as
`multi-agent-brief`, `/mabw`, `multi_agent_brief`, `mabw.*`, and `MABW-080`
remain only where existing commands, schemas, workspaces, or archived
experiments require them; they are not an implementation-lineage alias.

This index separates the current English documentation path from Chinese
operator notes and legacy/memo documents. It does not claim the whole `docs/`
tree is fully bilingual.

## High-Traffic English Docs

| Topic | English | Chinese |
|---|---|---|
| README | [README.md](../README.md) | [README.zh-CN.md](../README.zh-CN.md) |
| Contact | [contact.md](contact.md) | [contact.zh-CN.md](contact.zh-CN.md) |
| Fifteen-minute pilot | [15-minute-pilot.md](15-minute-pilot.md) | [15-minute-pilot.zh-CN.md](15-minute-pilot.zh-CN.md) |
| Getting started | [getting-started.md](getting-started.md) | English-first |
| Weekly loop | [weekly-loop.md](weekly-loop.md) | English-first |
| Troubleshooting | [troubleshooting.md](troubleshooting.md) | English-first |
| Golden reference workspace | [industry-weekly-demo](../examples/reference-workspaces/industry-weekly-demo/README.md) | English-first |
| Function map | [features.md](features.md) | [features.zh-CN.md](features.zh-CN.md) |
| Windows PowerShell setup | [windows-powershell.md](windows-powershell.md) | [windows-powershell.zh-CN.md](windows-powershell.zh-CN.md) |
| Golden path | [golden-path.md](golden-path.md) | [golden-path.zh-CN.md](golden-path.zh-CN.md) |
| Weekly use script | [weekly-use.md](weekly-use.md) | [weekly-use.zh-CN.md](weekly-use.zh-CN.md) |
| Launch validation checklist | [launch-validation.md](launch-validation.md) | [launch-validation.zh-CN.md](launch-validation.zh-CN.md) |
| Release checklist | [release-checklist.md](release-checklist.md) | English-first |
| v1.0 pilot evidence gate | [v1-pilot-evidence.md](v1-pilot-evidence.md) | English-first |
| Architecture status | [architecture-status.md](architecture-status.md) | [architecture-status.zh-CN.md](architecture-status.zh-CN.md) |
| Architecture overview | [architecture.md](architecture.md) | [architecture.zh-CN.md](architecture.zh-CN.md) |
| Orchestrator contracts | [orchestrator-contracts.md](orchestrator-contracts.md) | [orchestrator-contracts.zh-CN.md](orchestrator-contracts.zh-CN.md) |
| Roadmap | [roadmap.md](roadmap.md) | [roadmap.zh-CN.md](roadmap.zh-CN.md) |
| BriefLoop naming policy | [briefloop-naming.md](briefloop-naming.md) | English-first |
| Migration notes | [MIGRATION.md](MIGRATION.md) | [MIGRATION.zh-CN.md](MIGRATION.zh-CN.md) |
| What MABW tracks | [what-mabw-keeps-track-of.md](what-mabw-keeps-track-of.md) | [what-mabw-keeps-track-of.zh-CN.md](what-mabw-keeps-track-of.zh-CN.md) |
| Archived MABW-080 experiment guide | [experiments-080.md](experiments-080.md) | English-first |

`README_en.md` is retained only as a compatibility pointer to `README.md`.

## English-First Reference Docs

- [Getting started](getting-started.md)
- [Contact](contact.md)
- [Fifteen-minute pilot](15-minute-pilot.md)
- [Weekly loop](weekly-loop.md)
- [Troubleshooting](troubleshooting.md)
- [Claude Code quickstart](claude-code-quickstart.md)
- [Function map](features.md)
- [Runtime agent contract](agent-contract.md)
- [BriefLoop naming policy](briefloop-naming.md)
- [Evidence Span Registry](evidence-span-registry.md)
- [Claim-Support Matrix](claim-support-matrix.md)
- [Archived MABW-080 experiment guide](experiments-080.md)
- [Onboarding](onboarding.md)
- [Search backends](search-backends.md)
- [Runtime recipes](runtime-recipes.md)
- [Support matrix](support-matrix.md)
- [Red lines and anti-patterns](red-lines-and-anti-patterns.md)
- [Release checklist](release-checklist.md)
- [v1.0 pilot evidence gate](v1-pilot-evidence.md)
- [Pre-release v0.11.3 Product OS reader-quality reference package](reference-runs/v0.11.3-product-os-reader-quality-reference.md)
- [Pre-release v0.11.4 minimal comparative evaluation packet](evaluation-results/v0.11.4-minimal-comparative-evaluation/README.md)
- [Pre-release v0.11.1 synthetic regression pack](reference-runs/v0.11.1-synthetic-regression-pack.md)
- [v0.7.4 organoid-industry failure study](reference-runs/v0.7.4-organoid-failure-study.md)
- [BriefLoop-090 experiment closeout](reference-runs/briefloop-090-experiment-closeout.md)
- [BriefLoop-090 A-controlled auditable-brief pilot](reference-runs/briefloop-090-a-controlled-pilot.md)
- [Security](security.md)
- [Release notes v0.11.12](releases/v0.11.12.md)
- [Release notes v0.11.9](releases/v0.11.9.md)

## Technical Reports And Architecture References

These longer-form technical notes are design and architecture references. Treat
`docs/architecture-status.md` and `docs/support-matrix.md` as the current
implementation/support source of truth when they differ.

- [BriefLoop architecture reference v0.3.0](briefloop-architecture-reference-v0.3.0.md)
- [Tech report v0.3.0 abstract draft](tech-report-v0.3.0/abstract-draft-v0.3.0.md)
- [Tech report v0.3.0 industrial related work](tech-report-v0.3.0/industrial-related-work.md)
- [Tech report v0.3.0 v0.9 design rationale](tech-report-v0.3.0/v09-design-rationale.md)

## Chinese-Only Or Memo Docs

The following documents are intentionally not part of the first bilingual
coverage pass. They are either historical memos, contributor prompts, or
specialized notes.

- [agent-dev-guide.zh-CN.md](agent-dev-guide.zh-CN.md)
- [agent-dev-prompt.zh-CN.md](agent-dev-prompt.zh-CN.md)
- [modules/market-competitor.zh-CN.md](modules/market-competitor.zh-CN.md)
- [charter/Charter_CN.md](charter/Charter_CN.md), because
  [charter/README.md](charter/README.md) is the English charter entrypoint

## Planned Translation Backlog

- Market competitor module guide
- Agent developer guide

Do not treat this backlog as implemented coverage. Public-facing English entry
points should link to English documents when an English document exists, and
should explicitly label Chinese-only documents when no English version exists.

## Archive

Superseded architecture references, dated memos, and one-off design notes live
under [docs/archive/](archive/README.md). Archived documents are historical
records: they are not updated for current behavior, and current docs must not
cite them as implementation or support truth.
