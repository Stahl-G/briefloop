# Support Matrix

Each capability has one of the following statuses:

| Status | Meaning |
|---|---|
| **Supported** | Actively tested, documented, and considered stable for v1.0. |
| **Experimental** | Functional but may change without notice. Not guaranteed for production. |
| **Interface Only** | Abstract interface exists; no concrete implementation shipped. |
| **CLI-only** | Installs and exposes deterministic CLI commands, but does not include source-clone runtime asset trees. |
| **Source-clone-only** | Requires repository files that are not shipped as Python package data. |
| **Deprecated** | Still present but scheduled for removal. Use the replacement. |
| **Not shipped** | Not included in this distribution surface. |

For control-surface capabilities, **Supported** means deterministic commands,
contracts, and regression tests are present. It does not imply output-quality
validation unless that is stated separately.

## Core Pipeline

| Capability | Status |
|---|---|
| Subagent workflow (Scout → Screener → Claim Ledger → Analyst → Editor → Auditor) | Supported |
| Runtime handoff (`agent_handoff.md` + `agent_handoff.json`) | Supported |
| Runtime state control files (`runtime_manifest.json`, `workflow_state.json`, `artifact_registry.json`, `event_log.jsonl`) | Supported |
| Audience profile runtime surface (`audience_profile.md` + `audience_profile_snapshot.md`) | Supported |
| Improvement Ledger / Memory (`improvement/ledger.jsonl`, `improvement/memory.md`, `improvement_memory_snapshot.md`) | Supported |
| Orchestrator control switchboard (`orchestrator_control_switchboard.json`, optional `control_selections.json`) | Supported |
| Feedback control files (`feedback_issues.json`, `repair_plan.json`, conditional `delta_audit_report.json`) | Supported |
| Stage-scoped quality gate control files (`gates/auditor_quality_gate_report.json`, `gates/finalize_quality_gate_report.json`; legacy latest projection `quality_gate_report.json`) | Supported |
| Provenance projection control file (`provenance_graph.json`) | Supported |
| Finalize delivery bundle (`output/delivery/brief.md` + configured DOCX) | Supported |
| Source appendix audit/control copy (`source_appendix.md`) | Supported |
| `multi-agent-brief run --workspace <path>` | Supported |
| `multi-agent-brief status --workspace <path>` | Supported |
| `multi-agent-brief deliver --workspace <path> --target local` | Supported |
| `multi-agent-brief deliver --workspace <path> --target feishu` | Experimental |
| `multi-agent-brief state init/check/show/decide/stage-complete/finalize-complete` | Supported |
| `multi-agent-brief controls build-switchboard/show/select/validate` | Supported |
| `multi-agent-brief runtime install --workspace <path> --runtime opencode\|claude\|codex\|all` | Source-clone-only |
| `multi-agent-brief feedback ingest/plan/resolve/show/validate` | Supported |
| `multi-agent-brief gates check/show/validate` | Supported |
| `multi-agent-brief provenance build/show/validate` | Supported |
| `multi-agent-brief improve propose/list/show/approve/reject/revert/stats/validate/rebuild` | Supported |
| `multi-agent-brief eval-cases list/validate/run` | Supported |
| `multi-agent-brief init --from-onboarding` | Supported |
| `multi-agent-brief onboard` | Supported |
| `multi-agent-brief doctor` | Supported |
| `multi-agent-brief inputs extract` | Experimental |
| `multi-agent-brief inputs classify` | Supported |
| `multi-agent-brief finalize` | Supported |
| `multi-agent-brief audit` | Supported |

Feedback commands structure issues and repair plans for the Orchestrator. They do not automatically edit brief artifacts or execute repair.

Quality gate commands write deterministic gate reports and can block unsafe current-stage continue/finalize decisions. They do not fetch sources, rewrite briefs, execute repair, or create feedback issues automatically.

Evaluation cases are developer/CI regression checks for control-surface behavior. They do not create workflow artifacts, run subagents, fetch sources, score prose, call an LLM judge, or execute repair.

Provenance commands write a deterministic workspace-local audit/debug graph from existing control files. They do not fetch sources, execute workflow stages, replay the runtime, execute repair, verify semantic truth, or block `finalize` by default.

Audience profile files are workspace-local runtime context. The active run uses the frozen per-run snapshot exposed through handoff; these files are not source evidence, artifact contracts, quality gates, provenance graph nodes, or stage blockers.

Improvement Ledger files are human-governed workspace memory controls. Approved materializable guidance is projected into `improvement/memory.md` and frozen into `output/intermediate/improvement_memory_snapshot.md` during `run`/`start`/`handoff`. The snapshot is taste/audience guidance only; it is not evidence, source material, Claim Ledger input, repair instruction, semantic proof, or an output-quality guarantee.

Control switchboard files are runtime control context. Python surfaces deterministic recommendations and records Orchestrator enable/defer/reject selections; selection is not execution and does not run gates, feedback planning, provenance projection, source discovery, repair, or subagents.

Source appendices are reader-facing delivery artifacts generated during finalize from cited Claim Ledger sources. They are not source evidence, semantic proof, runtime state, provenance graphs, or workflow gates.

## Runtimes

| Runtime | Status |
|---|---|
| Hermes (`delegate_task` native pipeline + cron) | Supported |
| Claude Code (`/mabw` five-verb writer entrypoint + `/generate-brief` compatibility; installable with `multi-agent-brief claude install`) | Supported |
| OpenCode (subagent workflow) | Supported |
| Codex (custom-agent workflow via `runtime install`) | Experimental |
| Manual (print workflow steps) | Supported |

Claude Code is the first-class writer / five-verb path (`new`, `run`, `status`,
`feedback`, `deliver`). Hermes remains a supported delegated/scheduled runtime
path. Codex custom-agent assets are installable into a workspace, but Codex
remains Experimental until a real Codex control-trace smoke validates the
end-to-end specialist workflow. Other runtimes keep their existing workflow
entrypoints.

Runtime source assets under `.agents/`, `.claude/`, `.codex/`, `.opencode/`,
and `integrations/hermes-plugin/` are source-clone assets. Package-only installs
ship the CLI, packaged contracts, and packaged eval fixtures, but they do not
ship those source runtime directories as Python package data. Use
`multi-agent-brief runtime install --workspace <workspace> --runtime opencode|claude|codex|all`
from a source clone to copy OpenCode/Claude Code/Codex workspace-local runtime kits.

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
| Material-Fact / Freshness / Target-Relevance Gates | Supported |

## Evaluation & Regression Tooling

| Tool | Status |
|---|---|
| Packaged public-safe evaluation cases (`eval-cases`) | Supported |
| Workspace provenance projection (`provenance`) | Supported |
| Runtime asset parity check (`scripts/check_runtime_asset_parity.py`) | Source-clone-only |
| Private/commercial benchmark cases | Not shipped |
| LLM-as-judge prose scoring | Not shipped |

## Delivery & Output

| Format / Channel | Status |
|---|---|
| Markdown (`output/delivery/brief.md`) | Supported |
| DOCX (`output/delivery/<named>.docx`) | Supported |
| Source appendix audit/control copy (`source_appendix.md`) | Supported |
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
| Homebrew formula source (`Formula/`) | Experimental; not a primary release path |
| curl installer (`install.sh`) | Experimental CLI-only installer asset |
| PowerShell installer (`install.ps1`) | Experimental CLI-only installer asset |
| Hermes plugin (`integrations/hermes-plugin/`) | Supported |

| Runtime asset | Source clone | Wheel / sdist / PyPI package |
|---|---|---|
| Packaged contracts (`configs/*.yaml`) | Supported | Supported |
| Packaged eval fixtures (`eval-cases`) | Supported | Supported |
| `.agents/skills/**` | Supported | Source-clone-only |
| `.agents/hermes-skills/**` | Supported | Source-clone-only |
| `.claude/agents/**` and `.claude/commands/**` | Supported | Source-clone-only |
| `.opencode/agents/**` and `.opencode/commands/**` | Supported | Source-clone-only |
| `.codex/config.toml` and `.codex/agents/**` | Experimental | Source-clone-only; installable into a workspace with `runtime install --runtime codex` |
| `integrations/hermes-plugin/**` | Supported | Source-clone-only |
| `scripts/install.sh`, `scripts/install.ps1`, `Formula/` | Supported | Source-clone-only |

## Legacy / Deprecated

| Item | Status |
|---|---|
| `multi-agent-brief prepare` | Deprecated (use `run` instead) |
| Python `BriefPipeline` | Removed |
| `multi-agent-brief start` | Deprecated (alias for `run`) |
| `multi-agent-brief handoff` | Deprecated (use `run`) |
