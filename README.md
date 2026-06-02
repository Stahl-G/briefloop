# Multi-Agent Brief Workflow

A source-grounded, audit-ready multi-agent workflow for producing business, research, market, policy, and management briefs.

This project turns the repeatable briefing workflow used by analysts, strategy teams, investor relations teams, research desks, and management offices into a transparent Python pipeline:

```text
Scout -> Claim Ledger -> Analyst -> Auditor -> Editor -> Formatter
```

It is not an investment advice tool, trading signal generator, or replacement for human review.

## Why This Exists

Most weekly reports and executive briefs still depend on a fragile manual process: collect information, decide what matters, write analysis, verify facts, edit wording, and format the final file. This project makes that workflow modular, inspectable, and reusable.

The core design principle is simple:

> Let code do lookup. Let models do judgment. Keep every important claim traceable.

## Current MVP

The first local MVP supports:

- Local `.md`, `.txt`, and `.json` inputs
- Scout agent that extracts candidate reportable items
- Claim Ledger with source-grounded claims
- Analyst agent that drafts a Markdown brief with `[src:CLAIM_ID]` citations
- Deterministic Auditor for missing claims, unsupported numbers, duplicate claims, and redaction risks
- Editor agent that prepares the final Markdown brief
- Formatter agent that writes:
  - `brief.md`
  - `claim_ledger.json`
  - `audit_report.json`
  - `source_map.md`

## Existing Capability Tracks To Migrate

These capabilities are treated as migration tracks because they already exist in the private workflow and should be generalized before entering this repo:

- DOCX/PDF output
- Feishu, Slack, and Email delivery
- SEC, RSS, and API data connectors

## Future GitHub Project Epics

These are future workstreams and should be tracked as GitHub Project epics:

- Enterprise internal message ingestion
- Complex RAG and historical knowledge retrieval
- Database and semantic layer integration
- Automatic investment analysis guardrails and evaluator

See [docs/github-project.md](docs/github-project.md).

## Quick Start

```bash
cd multi-agent-brief-workflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
multi-agent-brief run examples/basic_market_brief/input --output output/basic_market_brief
```

Open the generated files:

```text
output/basic_market_brief/brief.md
output/basic_market_brief/claim_ledger.json
output/basic_market_brief/audit_report.json
output/basic_market_brief/source_map.md
```

## Example Without Install

```bash
PYTHONPATH=src python3 -m multi_agent_brief.cli.main run examples/basic_market_brief/input --output output/basic_market_brief
```

## Development

```bash
python3 -m pytest -q
```

## Safety

Do not commit credentials, tokens, webhooks, raw internal logs, private reports, customer names, confidential files, or company-specific prompts. All examples in this repo should use public or synthetic data.

## License

MIT
