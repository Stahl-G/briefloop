# Support Matrix

Each capability has one of the following statuses:

| Status | Meaning |
|---|---|
| **Supported** | Actively tested, documented, and considered stable for v1.0. |
| **Experimental** | Functional but may change without notice. Not guaranteed for production. |
| **Interface Only** | Abstract interface exists; no concrete implementation shipped. |
| **CLI-only** | Requires source clone + manual setup; not available via pip install. |
| **Deprecated** | Still present but scheduled for removal. Use the replacement. |

## Core Pipeline

| Capability | Status |
|---|---|
| Subagent workflow (Scout → Screener → Claim Ledger → Analyst → Editor → Auditor) | Supported |
| Runtime handoff (`agent_handoff.md` + `agent_handoff.json`) | Supported |
| Runtime state control files (`runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, `event_log.jsonl`) | Supported |
| Feedback control files (`feedback_issues.json`, `repair_plan.json`, conditional `delta_audit_report.json`) | Supported |
| Finalize (Markdown + DOCX) | Supported |
| `multi-agent-brief run --workspace <path>` | Supported |
| `multi-agent-brief state init/check/show/decide` | Supported |
| `multi-agent-brief feedback ingest/plan/resolve/show/validate` | Supported |
| `multi-agent-brief init --from-onboarding` | Supported |
| `multi-agent-brief onboard` | Supported |
| `multi-agent-brief doctor` | Supported |
| `multi-agent-brief inputs classify` | Supported |
| `multi-agent-brief finalize` | Supported |
| `multi-agent-brief audit` | Supported |

Feedback commands structure issues and repair plans for the Orchestrator. They do not automatically edit brief artifacts or execute repair.

## Runtimes

| Runtime | Status |
|---|---|
| Hermes (`delegate_task` native pipeline + cron) | Supported |
| Claude Code (`/generate-brief`) | Supported |
| OpenCode (subagent workflow) | Supported |
| Codex (subagent workflow) | Supported |
| Manual (print workflow steps) | Supported |

## Source Providers

| Provider | Status |
|---|---|
| Manual (local md/txt/json files) | Supported |
| Web search — Tavily | Supported |
| Web search — Exa | Supported |
| Web search — Brave | Supported |
| Web search — Firecrawl | Supported |
| Web search — Serper | Supported |
| RSS | Supported |
| SEC Filing resolver | Supported |
| Cached package (Hermes daily cache) | Supported |
| MinerU document parsing | Experimental |
| Local signal discovery | Experimental |
| OpenCLI provider | CLI-only |
| Feishu provider | Experimental |

## Analysis Modules

| Module | Status |
|---|---|
| Market Competitor | Supported |
| Policy & Regulatory | Supported |

## Quality Gates

| Gate | Status |
|---|---|
| Deterministic Audit | Supported |
| Editorial Governance | Supported |
| Final Quality (Final Clean) | Supported |
| Limitation Hygiene | Supported |
| Draft Audit Harness | Supported |
| Rendered Output Harness | Supported |

## Delivery & Output

| Format / Channel | Status |
|---|---|
| Markdown (`brief.md`) | Supported |
| DOCX (`brief.docx`) | Supported |
| Named output copies | Supported |
| PDF | Experimental |
| Feishu delivery | Experimental |
| Slack delivery | Interface Only |
| Email delivery | Interface Only |

## Analysis Tooling

| Tool | Status |
|---|---|
| `analysis-blocks` CLI | Supported |
| `limitation-hygiene` CLI | Supported |
| Audience profiles (`management` / `research` / `IR` / `legal-compliance`) | Supported |

## Installation & Distribution

| Method | Status |
|---|---|
| Source clone + `bash scripts/setup.sh` + `pip install -e ".[dev]"` | Supported |
| `pip install multi-agent-brief-workflow` (PyPI) | Experimental |
| Homebrew (`brew install multi-agent-brief`) | CLI-only |
| curl installer (`install.sh`) | CLI-only |
| PowerShell installer (`install.ps1`) | CLI-only |
| Hermes plugin (`integrations/hermes-plugin/`) | Supported |

## Legacy / Deprecated

| Item | Status |
|---|---|
| `multi-agent-brief prepare` | Deprecated (use `run` instead) |
| Python `BriefPipeline` | Removed |
| `multi-agent-brief start` | Deprecated (alias for `run`) |
| `multi-agent-brief handoff` | Deprecated (use `run`) |
