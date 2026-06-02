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
- Auditor agent interface with deterministic audit and semantic-audit adapter hooks
- Deterministic Auditor for missing claims, unsupported numbers, duplicate claims, and redaction risks
- Reporting-window checks that flag stale sources in weekly briefs
- Editor agent that prepares the final Markdown brief
- Formatter agent that writes:
  - `brief.md`
  - `claim_ledger.json`
  - `audit_report.json`
  - `source_map.md`

## Example Output

The MVP creates a Markdown brief with source citations:

```markdown
## Market

- Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels. [src:MARKETDA_867A7D67D0]
```

Every source-backed statement is also written to `claim_ledger.json`:

```json
{
  "claim_id": "MARKETDA_867A7D67D0",
  "statement": "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels.",
  "source_id": "MARKET_DATA",
  "evidence_text": "Synthetic module price checks showed a 3.5% week-over-week decline in selected spot-market channels."
}
```

The audit report records whether the draft is distribution-ready:

```json
{
  "audit_status": "pass",
  "audit_score": 100,
  "findings": []
}
```

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

Or run from a config file:

```bash
multi-agent-brief run --config examples/basic_market_brief/config.yaml
```

The example config enables a strict weekly reporting window:

```yaml
report:
  date: "2026-06-02"
  max_source_age_days: 14
  fail_on_stale_source: true
```

When this mode is enabled, a three-month-old source cannot pass as a weekly item.

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

## CLI

Create a synthetic demo workspace:

```bash
multi-agent-brief init brief-demo
multi-agent-brief run --config brief-demo/config.yaml
```

Audit an existing brief:

```bash
multi-agent-brief audit output/basic_market_brief/brief.md \
  --ledger output/basic_market_brief/claim_ledger.json \
  --output output/basic_market_brief/audit_report.json
```

Print the version:

```bash
multi-agent-brief version
```

## Auditor Agent Interface

The pipeline-level `AuditorAgent` delegates to an audit backend that implements `AuditAgentInterface`.

Current audit backends:

- `DeterministicAuditAgent`: checks source IDs, unsupported numbers, duplicate claims, missing source evidence, and redaction risks.
- `DeterministicAuditAgent`: also checks reporting-window freshness when `report.date` and `report.max_source_age_days` are configured.
- `NoOpSemanticAuditAgent`: placeholder adapter for future model-backed semantic source-support review.
- `CompositeAuditAgent`: runs deterministic audit first, then an optional semantic audit adapter.

This keeps the MVP runnable without API keys while leaving a clean interface for Claude/OpenAI/LiteLLM or local-model audit agents.

## Development

```bash
python3 -m pytest -q
```

## Safety

Do not commit credentials, tokens, webhooks, raw internal logs, private reports, customer names, confidential files, or company-specific prompts. All examples in this repo should use public or synthetic data.

## License

MIT
