# Multi-Agent Brief Workflow Toolkit

<p align="center">
  <a href="README_en.md">English</a> |
  <a href="README.md">简体中文</a>
</p>

A source-grounded, audit-ready agent-orchestrated workflow toolkit for producing business, research, market, policy, and management briefs.

> Let code do lookup. Let models do judgment. Keep every important claim traceable.

This project provides workspace initialization, source discovery, source collection, Claim Ledger/audit utilities, document rendering, and multi-runtime agent workflow support. The final brief is written by agent runtime subagents (Hermes, Claude Code, Codex, OpenCode) using Claim Ledger and audit outputs.

```text
Onboarding → Workspace Profile → Source Discovery → Source Collection → Claim Ledger/audit → Agent-assisted Drafting → Final Audit → Rendered Outputs
```

It is not an investment advice tool, trading signal generator, or replacement for human review.

## What Problem This Solves

Most weekly reports and executive briefs still depend on a fragile manual process: collect information, decide what matters, write analysis, verify facts, edit wording, and format the final file. That process is easy to rush, hard to audit, and difficult to reuse across teams.

This repo provides a toolkit that makes the workflow modular, inspectable, and runnable locally:

- Python tools handle source collection, signal filtering, evidence tracking, and audit checks.
- Claude/Codex agents write the final brief from the Claim Ledger.
- Draft and audited Markdown use explicit `[src:CLAIM_ID]` citations; the reader-facing `brief.md` strips those internal IDs.
- Auditors check unsupported numbers, stale sources, duplicate claims, placeholders, and redaction risks.
- Output artifacts keep the draft brief, audit report, claim ledger, and source map separate.

## Project Motivation

This project is an open-source workflow for producing leadership briefs, weekly reports, research notes, market updates, and policy briefings used in corporate strategy teams, securities research, funds, investor relations, management offices, and research desks.

In many organizations, interns, management trainees, and junior analysts spend a large amount of time preparing daily, weekly, and monthly reports. The work is important, but the process is often repetitive: collecting sources, filtering what matters, removing stale or duplicate signals, drafting analysis, checking facts, editing wording, and formatting the final document.

This project turns that workflow into a source-grounded, audit-ready, agent-orchestrated toolkit:

```text
Onboarding → Source Discovery → Source Collection → Claim Ledger/audit → Agent-assisted Drafting → Final Audit → Rendered Outputs
```

It does not replace human judgment and does not provide investment advice. Instead, it helps structure repetitive briefing work so people can spend more time on analysis, discussion, and decision support.

The core principle is:

> Let code do lookup. Let models do judgment. Keep every important claim traceable.

## Why Multi-Agent Instead Of One Prompt

A real briefing process is not one job. It is a small editorial desk:

- **Python preparation tools** handle source collection, signal filtering, evidence tracking, and audit checks.
- **Claude/Codex agents** handle analysis writing, editing, and final delivery audit.
- **Rendering tools** handle Markdown, DOCX, and other output formats.

Splitting these roles reduces hidden reasoning shortcuts. Python tools handle deterministic tasks, agents handle tasks requiring judgment, and the audit trail shows where every claim came from.

## Architecture

```mermaid
flowchart TB
  subgraph "User Onboarding"
    A["Conversational Onboarding<br/>onboarding.json"] --> B["Workspace Profile<br/>config.yaml, sources.yaml, user.md"]
  end

  subgraph "Source Discovery & Collection"
    B --> C["Source Discovery<br/>sources decide"]
    C --> D["Source Collection<br/>Manual/RSS/Web/API"]
  end

  subgraph "Python Support Tools"
    D --> E["Scout<br/>Signal Extraction"]
    E --> F["Screener<br/>Filter & Dedup"]
    F --> G["Claim Ledger<br/>Evidence Tracking"]
    G --> H["Audit Utilities<br/>Deterministic + Quality Checks"]
  end

  subgraph "Agent Runtime (Hermes / Claude Code / Codex / OpenCode)"
    H --> I["Analyst<br/>Draft from Claim Ledger"]
    I --> J["Editor<br/>Polish & Clean"]
    J --> K["Final Auditor<br/>Delivery Check"]
  end

  subgraph "Rendered Outputs"
    K --> L["Formatter<br/>Markdown/DOCX/PDF"]
  end
```

See [docs/architecture.md](docs/architecture.md) for the plain-language architecture guide.

## Current Features

This project provides the following tools and capabilities:

**Workspace & Onboarding:**
- `multi-agent-brief onboard` runs conversational onboarding and writes `onboarding.json`
- `multi-agent-brief init --from-onboarding onboarding.json` creates a brief workspace from onboarding
- `multi-agent-brief run --workspace <path>` hands off to the agent runtime (default: Hermes delegate_task)
- Onboarding mapper auto-translates Chinese role, industry, and audience labels into English config values

**Source Discovery & Collection:**
- `multi-agent-brief sources decide` subcommand resolves `llm_decide` source policy into concrete candidates, with `--merge` to merge back into `sources.yaml`
- Supports manual files, RSS, web search, API, SEC filings, MCP, CLI source providers
- `multi-agent-brief doctor` checks source configuration health

**Subagent Workflow:**
- Scout subagent extracts candidate reportable items
- Screener subagent filters claims by novelty scoring, topic capacity caps, and deduplication
- Claim Ledger subagent records source-grounded evidence
- Analyst subagent drafts the brief from Claim Ledger entries
- Editor subagent polishes readability
- Auditor subagent checks unsupported facts, orphan citations, and process residue
- `multi-agent-brief run --workspace <workspace>` produces a runtime handoff; the agent runtime orchestrates scout → screener → claim-ledger → analyst → editor → auditor → finalize

**Multi-Runtime Support:**
- Hermes (primary): `multi-agent-brief hermes install-plugin` + `/mabw new` in Hermes. Full `delegate_task` pipeline.
- Claude Code: `/generate-brief <workspace>` command
- Codex / OpenCode: agent configs in `.codex/` / `.opencode/`

**Rendering & Output:**
- DOCX renderer (enabled by default)
- Stable `brief.md` / `brief.docx` outputs plus automatically named delivery copies from `output.filename_template`
- `python scripts/check_terms.py` terminology consistency checker prevents spelling drift

## Example Output

The preparation tools create a Markdown draft with source citations:

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

## Getting Started

### Hermes (primary path)

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate

multi-agent-brief hermes install-plugin
hermes plugins enable mabw
```

Then in Hermes:

```text
/mabw new
```

Hermes checks the environment, collects your brief profile in chat, creates the workspace, and runs the full subagent pipeline: scout → screener → claim-ledger → analyst → editor → auditor. See [HERMES.md](HERMES.md) for the full protocol.

> **Finalize delivery gate:** after subagents produce `audited_brief.md`:
> ```bash
> multi-agent-brief finalize --config <workspace>/config.yaml
> ```
> Strips internal `[src:CLAIM_ID]` markers → writes `brief.md` / `brief.docx` → verifies outputs.

### Other runtimes

Claude Code (`/generate-brief <workspace>`), Codex, and OpenCode are also supported. After cloning the repo and `source .venv/bin/activate`:

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

---

## Demo Workspace

```bash
multi-agent-brief init ../mabw-demo --demo
multi-agent-brief run --workspace ../mabw-demo
```

Synthetic data only — shows the full pipeline from source to audit report without real company information.

## Development

```bash
multi-agent-brief init ../mabw-demo --demo
multi-agent-brief run --workspace ../mabw-demo
```

Or use `multi-agent-brief run --workspace <path>` from a cloned repo.

## llm_decide Source Discovery

The default `llm_decide` source mode lets the agent automatically generate search intents and candidate sources based on `user.md`:

```bash
# 1. Onboarding
multi-agent-brief onboard

# 2. Create workspace
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json

# 3. Generate candidate sources (template mode, no API key needed)
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# 4. Review candidates
cat ../mabw-workspace/source_candidates.yaml

# 5. Merge into sources
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge

# 6. Hand off to agent runtime
multi-agent-brief run --workspace ../mabw-workspace
```

The llm_decide mode does not block the workflow — if you skip `sources decide`, the workflow continues with local `input/` files and prints a warning.

## DOCX Output

When initializing a workspace, the default output formats now include `docx`. After running the pipeline, both `brief.md` and `brief.docx` will appear in the `output/` directory.

The formatter also writes human-readable named delivery copies. The default template is:

```yaml
output:
  filename_template: "{project_name}_{report_date}"
  named_outputs: true
```

DOCX requires the `python-docx` dependency. It is included when installing with `.[dev]`:

```bash
pip install -e ".[dev]"
```

Or install separately:

```bash
pip install "multi-agent-brief-workflow[docx]"
```

The DOCX uses a professional investment-bank-style layout with heading hierarchy, tables, lists, blockquotes, and code blocks. The default footer is "Confidential — Internal Use Only" — customize via `output.footer` in `config.yaml`.

If `python-docx` is not installed, the pipeline continues without interruption but records `docx_generation: skipped_missing_dependency` in `output/intermediate/audit_report.json`.

## Market & Competitor Intelligence Module

New in v0.3.0 — the first pluggable AnalysisModule. Runs between Screener and Analyst, transforming scattered competitor information into structured analysis.

- **Competitor Discovery & Confirmation**: `competitors init | list | merge` CLI — create candidate template, then `/propose-competitors` slash command (LLM-assisted) or manual editing → user reviews → merge into `competitor_universe.yaml`.
- **Targeted Search**: Auto-generates per-competitor × dimension search tasks (capacity / technology / customers / financials).
- **Entity Tagging**: Deterministic EntityEventEnricher tags Claims with entity_ids, event_type, geography, and dimension — between Scout and Screener.
- **Event Aggregation**: Merge same-entity same-type Claims into MarketEvents, infer event status (announced → under_construction → operational).
- **5 Intermediate Artifacts**: `events.json` / `competitor_matrix.json` / `coverage_report.json` / `watchlist.json` / `evidence_pack.json`.
- **Cross-Period Tracking**: `event_history.jsonl` — marks each event as new/changed/unchanged/cancelled/resolved.
- **6 Specialist Audits**: comparison_evidence, capacity_status, metric_basis, market_trend, single_source_confidence, coverage_gap.
- **Generic Interface**: AnalysisModule + Registry — reusable for future earnings/policy/patent modules.

See [Market & Competitor Module docs](docs/modules/market-competitor.zh-CN.md) for details.

All specialist audit checks integrate into the CompositeAuditAgent when the module is enabled.
Modules not enabled in `config.yaml` have zero impact on pipeline behavior.

## Feishu / Lark Integration

Bidirectional Feishu integration via the official [lark-cli](https://github.com/larksuite/cli) tool. Pull data from Feishu Docs, meeting minutes, Base tables, sheets, calendar, and approval tasks as source inputs — or deliver generated briefs to Feishu chats, docs, and Drive.

### Install & Authenticate

```bash
npx @larksuite/cli@latest install      # one-time install
lark-cli config init                    # configure app credentials
lark-cli auth login --recommend         # log in with recommended scopes
lark-cli auth status                    # verify
```

### Using Feishu as Source (Input)

Add to `sources.yaml`:

```yaml
feishu:
  enabled: true
  sources:
    - name: "meeting-notes"
      token: "V1Mdjflk..."       # token from the Feishu doc/minutes URL
      type: minutes               # see supported types below
```

**Supported source types:**

| Type | What it fetches | How to get the token |
|------|----------------|---------------------|
| `doc` | Feishu Document (Markdown) | Open doc, copy token from URL `.../doc/<token>` |
| `minutes` | Meeting minutes with AI summary/todos | Open minutes, token from URL `.../minutes/<token>` |
| `base` | Base table records | Open Base, token from URL `.../base/<token>`. Also set `table_id`. |
| `sheet` | Spreadsheet values | Open sheet, token from URL `.../sheet/<token>` |
| `agenda` | Today's calendar events | No token required |
| `approval` | Pending approval tasks | No token required |

### Using Feishu as Delivery (Output)

**Send to chat:**

```python
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryTarget

connector = FeishuDeliveryConnector()
connector.deliver(
    DeliveryArtifact(path="output/brief.md", title="Daily Brief"),
    DeliveryTarget(channel="chat", recipient="oc_your_chat_id"),
)
```

Get `chat_id` from the group chat URL (`.../?chat_id=oc_xxxxxxxxxxx`) or group info page.

**Create a Feishu document:**

```python
connector.deliver(
    DeliveryArtifact(path="output/brief.md", title="Weekly Report"),
    DeliveryTarget(channel="doc"),
)
```

**Upload file to Drive:**

```python
connector.deliver(
    DeliveryArtifact(path="output/brief.docx", title="Weekly Report"),
    DeliveryTarget(channel="drive"),
)
```

### Typical Workflow

```python
# After multi-agent-brief run --workspace my-workspace:
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryTarget

FeishuDeliveryConnector().deliver(
    DeliveryArtifact(path="output/brief.md", title="Weekly Brief"),
    DeliveryTarget(channel="chat", recipient="oc_your_chat_id"),
)
```

See [docs/feishu-integration.md](docs/feishu-integration.md) for full details.

## SEC Filing Resolution (disclosure-filing-resolver)

Integrate with [disclosure-filing-resolver](https://github.com/Stahl-G/disclosure-filing-resolver) for automatic SEC EDGAR filing acquisition and XBRL financial data extraction. Ideal for tracking US-listed companies (foreign private issuers, ADRs, or domestic US companies).

### What It Does

| Capability | Description |
|------------|-------------|
| SEC filing download | Automatically downloads 10-K, 10-Q, 8-K, 6-K filing HTML documents |
| 6-K exhibit expansion | Detects 6-K filings and expands Exhibit 99.x files (financial statements, operating reviews) |
| XBRL extraction | Extracts revenue, net income, assets, EPS from SEC companyfacts API |
| iXBRL parsing | Extracts Inline XBRL facts from filing HTML documents |
| Source traceability | Every financial fact carries a SEC source URL for Claim Ledger integration |

### Install

```bash
pip install disclosure-filing-resolver
```

### Configure

Add `filing_resolver` to your workspace `sources.yaml`:

```yaml
filing_resolver:
  enabled: true
  tickers:
    - AAPL      # replace with your target company ticker
    - MSFT
  filing_types:
    - 10-K      # annual report
    - 10-Q      # quarterly report
    - 8-K       # material events
  xbrl: true    # enable XBRL financial data extraction
```

### Auto-Configure via Source Discovery

With `llm_decide` source mode, `sources decide` automatically generates SEC filing candidates:

```bash
# 1. Generate candidate sources (includes SEC EDGAR filing suggestions)
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# 2. Review candidates
cat ../mabw-workspace/source_candidates.yaml
# The filing_sources section lists suggested SEC filing sources

# 3. Edit source_candidates.yaml to set the correct ticker(s)

# 4. Merge into sources.yaml
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge
```

After merging, `sources.yaml` automatically:
- Adds `filing_resolver` to `enabled_providers`
- Configures `filing_resolver` tickers and filing_types

### Set SEC User-Agent

SEC EDGAR requires a declared User-Agent:

```bash
export SEC_USER_AGENT="your_email@example.com disclosure-filing-resolver"
```

### Typical Workflow

```bash
# 1. Install disclosure-filing-resolver
pip install disclosure-filing-resolver

# 2. Set environment variable
export SEC_USER_AGENT="your_email@example.com disclosure-filing-resolver"

# 3. Onboard and init workspace
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json

# 4. Discover sources (auto-generates SEC filing candidates)
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml

# 5. Edit source_candidates.yaml to confirm ticker
# 6. Merge
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml --merge

# 7. Hand off to agent runtime
multi-agent-brief run --workspace ../mabw-workspace
```

The brief will automatically include SEC-sourced financial data:

```markdown
- ACME Corp reported revenue of $150.0M for Q1 2026, up 12% year-over-year. [src:FILING_ACME_10Q]
```

See [disclosure-filing-resolver docs](https://github.com/Stahl-G/disclosure-filing-resolver) for full details.

## CLI

### Enable Tavily Live Search

Web search is disabled by default. To enable it:

You can opt in during `init` (the interactive wizard asks), or manually edit `sources.yaml`:

```yaml
web_search:
  enabled: true
  backend: tavily
  api_key_env: TAVILY_API_KEY
  topic: news
  search_depth: basic
  max_results: 5
  search_tasks:
    - query: "manufacturing tariff trade policy"
      domains:
        - "reuters.com"
        - "bloomberg.com"
```

2. Set the environment variable and run:

```bash
export TAVILY_API_KEY=tvly-your-key-here
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml
multi-agent-brief run --workspace ../mabw-workspace
```

PowerShell:

```powershell
$env:TAVILY_API_KEY = Read-Host "Enter your Tavily API key"
multi-agent-brief sources decide --config ../mabw-workspace/config.yaml
multi-agent-brief run --workspace ../mabw-workspace
```

3. Check configuration health:

```bash
multi-agent-brief doctor --config ../mabw-workspace/config.yaml
```

Notes:
- Web search is disabled by default and must be explicitly enabled
- Tavily requires `TAVILY_API_KEY` environment variable
- API keys must be stored in environment variables, not config files
- API keys are never printed or stored in configuration
- If Tavily is enabled but the API key is missing, the pipeline fails immediately (fail-fast)
- Web search results may not provide reliable `published_at` dates — time-sensitive web_search claims should be manually verified
- Web search ingestion includes boilerplate filtering (cookies, privacy policy, TOC, etc.) but is not perfect
- Real-time search feature is not release-ready until live smoke passes

Create a synthetic demo workspace (sample data for feature exploration):

```bash
multi-agent-brief init ../mabw-demo --demo
multi-agent-brief sources decide --config ../mabw-demo/config.yaml
multi-agent-brief run --workspace ../mabw-demo
```

PowerShell:

```powershell
multi-agent-brief init ../mabw-demo --demo
multi-agent-brief sources decide --config ../mabw-demo/config.yaml
multi-agent-brief run --workspace ../mabw-demo
```

Audit an existing brief:

```bash
multi-agent-brief audit <workspace>/output/intermediate/audited_brief.md \
  --ledger <workspace>/output/intermediate/claim_ledger.json \
  --output <workspace>/output/intermediate/audit_report.json
```

PowerShell:

```powershell
multi-agent-brief audit <workspace>/output/intermediate/audited_brief.md `
  --ledger <workspace>/output/intermediate/claim_ledger.json `
  --output <workspace>/output/intermediate/audit_report.json
```

Print the version:

```bash
multi-agent-brief version
```

## Auditor Agent Interface

The auditor subagent delegates to an audit backend that implements `AuditAgentInterface`.

Current audit backends:

- `DeterministicAuditAgent`: checks source IDs, unsupported numbers, duplicate claims, missing source evidence, redaction risks, and reporting-window freshness.
- `QualityHarnessAuditAgent`: ports public-safe quality gates from local workflow prototypes, including placeholders, internal process residue, `needs_recrawl`, low source density, and possible unit inflation.
- `NoOpSemanticAuditAgent`: placeholder adapter for future model-backed semantic source-support review.
- `CompositeAuditAgent`: runs deterministic audit first, then an optional semantic audit adapter.

This keeps the MVP runnable without API keys while leaving a clean interface for Claude, OpenAI, LiteLLM, or local-model audit agents.

See [docs/harness.md](docs/harness.md) for the current harness and migration backlog.

For strict final-delivery gates, see [docs/harness_matrix.md](docs/harness_matrix.md). For Codex, Claude Code subagent, and external-agent handoff patterns, see [docs/agent-collaboration.md](docs/agent-collaboration.md).

## Agent Support

This repository can generate Codex and Claude Code agent configurations from a single role manifest.

- `configs/agent_roles.yaml` is the source of truth.
- `scripts/generate_agent_configs.py` generates platform-specific files.
- `AGENTS.md` provides project-level instructions for Codex and other coding agents.
- `.agents/skills/*/SKILL.md` provides Codex-compatible skills.
- `.codex/agents/*.toml` provides Codex custom agents.
- `.claude/agents/*.md` provides Claude Code subagents.
- `docs/agents/` documents platform adaptation and harness subagents.

Regenerate configs:

```bash
python scripts/generate_agent_configs.py --write
```

PowerShell:

```powershell
python scripts/generate_agent_configs.py --write
```

Check generated files:

```bash
python scripts/generate_agent_configs.py --check
```

PowerShell:

```powershell
python scripts/generate_agent_configs.py --check
```

See [docs/windows-powershell.md](docs/windows-powershell.md) for native Windows setup. WSL is optional, not required.

## Multi-Runtime Agent Mode

- **Hermes（主路径）**：`multi-agent-brief hermes install-plugin` then `/mabw new`. Full `delegate_task` subagent pipeline.
- **Claude Code**：`/generate-brief <workspace>` command
- **Codex / OpenCode**：agent configs in `.codex/` / `.opencode/`

### Two-Layer Architecture

| Layer | Purpose | Characteristics |
|-------|---------|-----------------|
| Python CLI | Deterministic tooling: init, doctor, sources, audit, finalize, handoff | Testable, no API keys required |
| Agent subagents | Interactive source planning, extraction, analysis, editing | Model-assisted judgment |

### Available Subagents

| Subagent | Purpose |
|----------|---------|
| `source-planner` | Generate/refine source candidates and search tasks |
| `scout` | Extract candidate reportable items from sources |
| `screener` | Rank, dedupe, and capacity-cap candidates |
| `claim-ledger` | Build source-grounded claim entries |
| `analyst` | Draft management-ready brief sections |
| `editor` | Improve readability without adding facts |
| `auditor` | Review final brief against ledger and audit report |

See [docs/claude-code-workflow.md](docs/claude-code-workflow.md) and [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md).

## Roadmap

The roadmap now prioritizes a stable v1.0 baseline before any v2.0 MAS Runtime work.

Public direction:

- **v0.6: Orchestrator Contracts And Feedback Loop** — make the main agent explicit and demonstrate an output → feedback → bounded repair loop early.
- **v0.7: FrictionStore And Improvement Proposals** — turn recurring failures, audit findings, and human feedback into controlled improvement proposals.
- **v0.8: Policy Packs And Runtime Parity** — support different briefing contexts while keeping runtime artifact expectations aligned.
- **v0.9: Distribution And Reference Workflows** — improve installation, setup diagnostics, and public-safe demos.
- **v1.0: Stable Orchestrated Brief Workflow** — freeze a local-first, auditable, contract-governed baseline.
- **v2.0: MAS Runtime Research Track** — after v1.0, explore richer runtime coordination concepts.

See [docs/roadmap.md](docs/roadmap.md) for the public roadmap, [docs/architecture-status.md](docs/architecture-status.md) for current implementation status, [docs/MIGRATION.md](docs/MIGRATION.md) for migration notes, [docs/orchestrator-contracts.md](docs/orchestrator-contracts.md) for the public contract model, [docs/orchestrator-architecture.md](docs/orchestrator-architecture.md) for the v0.6 control model, [docs/mas-v2-evaluation.zh-CN.md](docs/mas-v2-evaluation.zh-CN.md) for the v2.0 technical evaluation, and [docs/repo-metadata.md](docs/repo-metadata.md) for suggested GitHub description and topics. v0.6.4 builds on shared Orchestrator authority, runtime state, the feedback/repair control plane, and deterministic quality gates with packaged public-safe evaluation cases for developer and CI regression checks. It does not mean Python scores prose, calls an LLM judge, edits briefs, executes repair, live-fetches market data, recrawls sources, makes semantic truth judgments, or builds a provenance graph. Detailed implementation plans, schema drafts, private evaluation cases, and commercial scenario design are intentionally kept out of the public repository until the corresponding capabilities are stable and ready to publish.

## Safety And Non-Investment-Advice Disclaimer

Do not commit credentials, tokens, webhooks, raw internal logs, private reports, customer names, confidential files, internal paths, or company-specific prompts. All examples in this repo should use public or synthetic data.

This project can help structure research and briefing workflows, but it does not provide legal, financial, investment, trading, or compliance advice. Human review remains required before any real-world distribution or decision-making use.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

Current version: **v0.6.4** — packaged public-safe evaluation cases for control-surface regression

v0.6.4 adds `multi-agent-brief eval-cases list/validate/run` with five packaged public-safe workspace control cases and one Hermes static invariant case. Eval cases use structured allowlisted actions instead of shell strings. They validate gates, feedback, runtime blockers, and Hermes path invariants without creating workflow artifacts, editing briefs, executing repair, fetching sources, or calling an LLM judge.

[View full changelog →](CHANGELOG.md)

## Development

```bash
python -m pytest -q
```

PowerShell:

```powershell
python -m pytest -q
```

## Contributing

This project is currently maintained mainly by one person and is still at an early stage.

Contributions, issues, discussions, and trial feedback are welcome, especially from people who have worked on weekly reports, management briefs, research notes, market updates, policy briefings, internal reporting workflows, or AI-assisted office work.

The project needs feedback from different industries, roles, and career stages to become useful in real-world workflows.

Useful contributions include:

* real briefing scenarios;
* pain points from weekly, monthly, or daily reporting work;
* industry-specific report structures;
* role-specific templates for strategy, investment, IR, legal, compliance, or management teams;
* suggestions for Source Providers, Screener logic, Claim Ledger design, or audit checks;
* synthetic examples and public-safe demos;
* documentation, tests, and safety improvements.

Even a single issue describing a real workflow, a template suggestion, or a failure case can help make the project more useful.

## License

MIT

## Interactive Onboarding Questions

The initialization wizard asks the following 13 questions (with conditional follow-ups):

1. **Company** — target company or organization
2. **Role** — strategy/IR/research/policy/management support/other
3. **Industry** — manufacturing/banking/fund/internet/general research
4. **Brief Title** — custom brief name
5. **Audience** — management, strategy, research, IR, marketing, etc.
6. **Focus Areas** — comma-separated, e.g. sales data, autonomous driving, policy, supply chain
7. **Cadence** — weekly, biweekly, monthly, ad hoc
8. **Items Per Brief** — default 8
9. **Historical Retrieval / RAG** — enable or not (default off). If enabled, choose provider: Ollama local / Gemini API
10. **Output Formats** — comma-separated, e.g. markdown, docx
11. **Max Source Age** — default 14 days
12. **Source Profile** — conservative / research / aggressive signal / custom / LLM decide
13. **Live Web Search** — enable or not (default off). If enabled, choose backend: tavily / exa / brave / firecrawl / serper
