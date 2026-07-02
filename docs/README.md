# BriefLoop Public Documentation

BriefLoop is the public project name. MABW remains the implementation lineage
and compatibility surface for CLI commands, runtime commands, package/module
paths, artifact names, workspace formats, and experiment IDs during the v0.9
compatibility period.

This index separates the current English documentation path from Chinese
operator notes and legacy/memo documents. It does not claim the whole `docs/`
tree is fully bilingual.

## High-Traffic English Docs

| Topic | English | Chinese |
|---|---|---|
| README | [README.md](../README.md) | [README.zh-CN.md](../README.zh-CN.md) |
| Function map | [features.md](features.md) | [features.zh-CN.md](features.zh-CN.md) |
| Windows PowerShell setup | [windows-powershell.md](windows-powershell.md) | [windows-powershell.zh-CN.md](windows-powershell.zh-CN.md) |
| Golden path | [golden-path.md](golden-path.md) | [golden-path.zh-CN.md](golden-path.zh-CN.md) |
| Weekly use script | [weekly-use.md](weekly-use.md) | [weekly-use.zh-CN.md](weekly-use.zh-CN.md) |
| Launch validation checklist | [launch-validation.md](launch-validation.md) | [launch-validation.zh-CN.md](launch-validation.zh-CN.md) |
| Architecture status | [architecture-status.md](architecture-status.md) | [architecture-status.zh-CN.md](architecture-status.zh-CN.md) |
| Architecture overview | [architecture.md](architecture.md) | [architecture.zh-CN.md](architecture.zh-CN.md) |
| Orchestrator contracts | [orchestrator-contracts.md](orchestrator-contracts.md) | [orchestrator-contracts.zh-CN.md](orchestrator-contracts.zh-CN.md) |
| Roadmap | [roadmap.md](roadmap.md) | [roadmap.zh-CN.md](roadmap.zh-CN.md) |
| BriefLoop naming policy | [briefloop-naming.md](briefloop-naming.md) | English-first |
| Brief-loop engineering | [brief-loop-engineering.md](brief-loop-engineering.md) | English-first |
| Migration notes | [MIGRATION.md](MIGRATION.md) | [MIGRATION.zh-CN.md](MIGRATION.zh-CN.md) |
| What MABW tracks | [what-mabw-keeps-track-of.md](what-mabw-keeps-track-of.md) | [what-mabw-keeps-track-of.zh-CN.md](what-mabw-keeps-track-of.zh-CN.md) |
| MABW-080 experiment guide | [experiments-080.md](experiments-080.md) | English-first |

`README_en.md` is retained only as a compatibility pointer to `README.md`.

## English-First Reference Docs

- [Claude Code quickstart](claude-code-quickstart.md)
- [Function map](features.md)
- [Runtime agent contract](agent-contract.md)
- [BriefLoop naming policy](briefloop-naming.md)
- [Brief-loop engineering](brief-loop-engineering.md)
- [Evidence Span Registry](evidence-span-registry.md)
- [Claim-Support Matrix](claim-support-matrix.md)
- [MABW-080 experiment guide](experiments-080.md)
- [Onboarding](onboarding.md)
- [Search backends](search-backends.md)
- [Runtime recipes](runtime-recipes.md)
- [Support matrix](support-matrix.md)
- [Red lines and anti-patterns](red-lines-and-anti-patterns.md)
- [Pre-release v0.11.3 Product OS reader-quality reference package](reference-runs/v0.11.3-product-os-reader-quality-reference.md)
- [Pre-release v0.11.4 minimal comparative evaluation packet](evaluation-results/v0.11.4-minimal-comparative-evaluation/README.md)
- [Pre-release v0.11.1 synthetic regression pack](reference-runs/v0.11.1-synthetic-regression-pack.md)
- [BriefLoop-090 A-controlled auditable-brief pilot](reference-runs/briefloop-090-a-controlled-pilot.md)
- [Security](security.md)

## Technical Reports And Architecture References

These longer-form technical notes are design and architecture references. Treat
`docs/architecture-status.md` and `docs/support-matrix.md` as the current
implementation/support source of truth when they differ.

- [BriefLoop architecture reference v0.3.0](briefloop-architecture-reference-v0.3.0.md)
- [MABW architecture reference v0.2.0](mabw-architecture-reference-v0.2.0.md)
- [MABW architecture reference v0.3.0 revision roadmap](mabw-architecture-reference-v0.3.0-revision-roadmap.md)
- [Tech report v0.3.0 abstract draft](tech-report-v0.3.0/abstract-draft-v0.3.0.md)
- [Tech report v0.3.0 industrial related work](tech-report-v0.3.0/industrial-related-work.md)
- [Tech report v0.3.0 v0.9 design rationale](tech-report-v0.3.0/v09-design-rationale.md)

## Chinese-Only Or Memo Docs

The following documents are intentionally not part of the first bilingual
coverage pass. They are either historical memos, contributor prompts, or
specialized notes.

- `docs/architecture-memo-*.md` files whose body is Chinese
- [agent-dev-guide.zh-CN.md](agent-dev-guide.zh-CN.md)
- [agent-dev-prompt.zh-CN.md](agent-dev-prompt.zh-CN.md)
- [mas-v2-evaluation.zh-CN.md](mas-v2-evaluation.zh-CN.md)
- [modules/market-competitor.zh-CN.md](modules/market-competitor.zh-CN.md)
- [charter/Charter_CN.md](charter/Charter_CN.md), because
  [charter/README.md](charter/README.md) is the English charter entrypoint

## Planned Translation Backlog

- Market competitor module guide
- Agent developer guide
- MAS v2 evaluation note
- Selected architecture memos after their public value and currentness are
  reviewed

Do not treat this backlog as implemented coverage. Public-facing English entry
points should link to English documents when an English document exists, and
should explicitly label Chinese-only documents when no English version exists.
