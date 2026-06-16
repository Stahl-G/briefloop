# MABW Public Documentation

This index separates the current English documentation path from Chinese
operator notes and legacy/memo documents. It does not claim the whole `docs/`
tree is fully bilingual.

## High-Traffic English Docs

| Topic | English | Chinese |
|---|---|---|
| README | [README.md](../README.md) / [README_en.md](../README_en.md) | [README.zh-CN.md](../README.zh-CN.md) |
| Windows PowerShell setup | [windows-powershell.md](windows-powershell.md) | [windows-powershell.zh-CN.md](windows-powershell.zh-CN.md) |
| Golden path | [golden-path.md](golden-path.md) | [golden-path.zh-CN.md](golden-path.zh-CN.md) |
| Weekly use script | [weekly-use.md](weekly-use.md) | [weekly-use.zh-CN.md](weekly-use.zh-CN.md) |
| Launch validation checklist | [launch-validation.md](launch-validation.md) | [launch-validation.zh-CN.md](launch-validation.zh-CN.md) |
| Architecture status | [architecture-status.md](architecture-status.md) | [architecture-status.zh-CN.md](architecture-status.zh-CN.md) |
| Architecture overview | [architecture.md](architecture.md) | [architecture.zh-CN.md](architecture.zh-CN.md) |
| Orchestrator contracts | [orchestrator-contracts.md](orchestrator-contracts.md) | [orchestrator-contracts.zh-CN.md](orchestrator-contracts.zh-CN.md) |
| Roadmap | [roadmap.md](roadmap.md) | [roadmap.zh-CN.md](roadmap.zh-CN.md) |
| Migration notes | [MIGRATION.md](MIGRATION.md) | [MIGRATION.zh-CN.md](MIGRATION.zh-CN.md) |
| What MABW tracks | [what-mabw-keeps-track-of.md](what-mabw-keeps-track-of.md) | [what-mabw-keeps-track-of.zh-CN.md](what-mabw-keeps-track-of.zh-CN.md) |

## English-First Reference Docs

- [Claude Code quickstart](claude-code-quickstart.md)
- [Runtime agent contract](agent-contract.md)
- [Onboarding](onboarding.md)
- [Search backends](search-backends.md)
- [Runtime recipes](runtime-recipes.md)
- [Support matrix](support-matrix.md)
- [Red lines and anti-patterns](red-lines-and-anti-patterns.md)
- [Security](security.md)

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
