# 🧾 BriefLoop

**AI-assisted business briefings you can question later.**

[English](README.md) | [简体中文](README.zh-CN.md)

Website: [briefloop.ai](https://briefloop.ai) · Contact: [contact@briefloop.ai](mailto:contact@briefloop.ai)

[OpenAI Build Week](#openai-build-week-2026) · [15-Minute Pilot](docs/15-minute-pilot.md) · [Getting Started](docs/getting-started.md) · [Weekly Loop](docs/weekly-loop.md) · [Troubleshooting](docs/troubleshooting.md) · [Reference Workspace](examples/reference-workspaces/industry-weekly-demo/README.md) · [Contact](docs/contact.md)

Writer entry: use `/briefloop` in Claude Code, or `briefloop` in a shell.

---

## OpenAI Build Week 2026

BriefLoop was built with both **Codex** and **GPT-5.6**. They served
different roles, and neither could make a model output authoritative on
its own.

| Participant | How it was used | Authority boundary |
|---|---|---|
| **Codex** | The primary engineering environment for architecture decomposition, Python implementation, testing, adversarial review, failure analysis, documentation, and scoped repair. Codex is also the host for BriefLoop’s current Experimental SQLite runtime path. | Codex could propose and implement bounded changes, but it could not approve its own work, merge changes, or authorize product and research claims. |
| **GPT-5.6** | Generated candidate claims and weekly-brief drafts for the controlled comparison. GPT-5.6 Sol with maximum reasoning in Codex, together with an Academic Research Skill, supported related-work research, candidate protocol design, and technical-report drafting. A separate GPT-5.6 Sol Pro discussion challenged the controls, falsification criteria, citations, and claim boundaries. | Model outputs remained proposals. They were not automatically treated as evidence, accepted experimental results, or publication-ready conclusions. |
| **Human maintainer** | Selected the research question, verified primary sources, defined invariants and acceptance criteria, froze the experimental protocol, accepted or rejected revisions, and approved merges and publication. | Final architecture, experiment, risk-acceptance, merge, and publication authority remained human-owned. |

The Academic Research Skill https://github.com/imbad0202/academic-research-skills organized the research workflow; it was not used as source evidence. The underlying papers, official documentation, repository artifacts, and primary publications remained the cited sources.

### Judge quickstart — no API key required

Requires Python 3.12.

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

The deterministic demo creates a public-safe reference workspace with a
reader brief, Claim Ledger, Quality Panel, source appendix, and event-log
excerpt. It does not call a model, fetch live sources, or prove output
quality.

* [Technical report — Architecture Reference v0.6.1](https://briefloop.ai/reports/briefloop-architecture-reference-v0.6.1.en.html)
* [15-Minute Pilot](docs/15-minute-pilot.md)
* [Public reference workspace](examples/reference-workspaces/industry-weekly-demo/README.md)

### Evidence boundary

The Prompt, Skill, and BriefLoop comparison is reported only from frozen
artifacts, hashes, and completed review records. This README does not
claim that BriefLoop has already won the comparison, automatically
resolves every knowledge conflict, or removes the need for human review.
It does not prove semantic truth.

---

## ✨ In one sentence

BriefLoop is an open-source workflow for recurring business briefings.

It does not try to be “a better prompt.” It keeps track of the process behind a brief:

- Which source did this number come from?
- Which claims were registered?
- Which checks passed or failed?
- Which reader preferences were approved by a human?
- What should be reused next time, and what should not?

> When someone asks where a number came from, BriefLoop does not ask the model to improvise an explanation. It opens the ledger.

---

## 🧯 The problem

Many teams write the same kind of material every week:

- market updates
- industry briefings
- competitor tracking
- policy monitoring
- equity research notes
- investor-relations materials
- executive briefings
- project status reports

LLMs can draft these documents quickly. The hard part is not drafting. The hard part is trust.

Common problems:

1. **Sources disappear.**
   A number enters the final brief, and two weeks later nobody knows where it came from.

2. **Small errors become confident conclusions.**
   A weak source, stale data point, or misread paragraph can survive several editing passes and look authoritative.

3. **Feedback evaporates.**
   “Lead with the business impact,” “do not use generic wording,” or “verify this category against the original filing” often stays in someone’s head instead of the workflow.

4. **Handoffs are painful.**
   The real rules for writing the brief live with one experienced person, not in a visible process.

BriefLoop turns those hidden rules into a workflow that can be inspected, repeated, and improved.

---

## 👥 Who is this for?

BriefLoop is useful for:

- analysts, associates, management trainees, IR teams, strategy teams, and market-intelligence teams who write recurring briefings;
- teams that want AI-assisted writing to be traceable instead of merely fluent;
- researchers studying agent workflows, human-in-the-loop systems, and auditable AI processes.

It is not the right tool if you only want:

- a one-click pretty report generator;
- an autonomous agent that publishes without review;
- a system that proves every claim is true;
- a way to make an externally generated AI report “safe” after the fact.

---

## 🧱 What BriefLoop actually does

Think of BriefLoop as a briefing pipeline with ledgers.

| Step | What happens | Why it matters |
|---|---|---|
| 1. Prepare materials | Collect local files, source packs, or search results | The model should not start from nothing |
| 2. Register claims | Important numbers, dates, entities, and claims are written into a Claim Ledger | Later you can ask where a claim came from |
| 3. Draft by roles | Scout, Analyst, Editor, Auditor, and related roles work within boundaries | Writing becomes staged work, not one giant prompt |
| 4. Run gates | Quality gates check source age, new facts, missing support, and delivery state | Deterministic checks do not rely on prompt memory |
| 5. Deliver by human action | Final delivery is explicitly triggered by a human | The system does not publish or bypass review |
| 6. Inspect the audit trail | Accepted actions leave SQLite receipts; JSON, Markdown, and HTML views remain replaceable projections | Reviewability does not create a second runtime authority |

The design rule is simple:

> Agents may write and propose. Deterministic services accept authoritative effects, and delivery remains human-triggered.

---

## 📚 The four questions it keeps answerable

| Question | What BriefLoop records | Where to look |
|---|---|---|
| Where is this run? | Current Store revision, stage, blockers, and next safe action | `briefloop status`, `briefloop runtime next`, SQLite ControlStore receipts |
| Where did each number come from? | Claim Ledger entries, source dates, source appendix, gate findings | `claim_ledger.json`, `source_appendix.md`, `quality_gate_report.json` |
| What took effect? | Accepted strict requests, transaction receipts, and invocation lineage | `briefloop.db` through supported status and runtime views |
| What guards delivery? | Store-backed gate evaluations, package readiness, and explicit human approval | Receipt-backed runtime actions and read-only status projections |

Agents can observe and propose. Only strict requests accepted by deterministic
services change the Store, and delivery stays human-controlled. A Store-native
reusable-guidance or Improvement Ledger surface is not shipped yet.

---

## 📦 What you get from a run

A normal delivered brief is usually:

- `output/delivery/brief.md`
- `output/delivery/<report-name>.docx`

The SQLite ControlStore is the sole runtime authority for a fresh Codex run,
stored in the workspace-local `briefloop.db`. Depending on the run stage,
BriefLoop may also write replaceable audit and reader projections such as:

- `output/intermediate/claim_ledger.json`
- `output/source_appendix.md`
- `output/intermediate/quality_gate_report.json`
- `output/intermediate/quality_panel.html`

Those projections are not meant to be read by every end user, and they are not
read back as runtime legality. They help a team answer questions, debug
failures, review decisions, and hand the process to someone else.

---

## 🔎 A tiny example

A delivered brief might say:

```markdown
This week, the sample PV module spot price fell 1.8% week over week,
the third consecutive weekly decline.
```

BriefLoop expects that claim to have a registered entry:

```json
{
  "claim_id": "CL-0012",
  "statement": "The sample PV module spot price fell 1.8% week over week.",
  "source_id": "SRC-003",
  "evidence_text": "Example source excerpt showing the week-over-week price change.",
  "metadata": {
    "published_at": "2026-06-05",
    "source_title": "Example PV price sheet"
  }
}
```

If a source is stale, a number is unregistered, or an editor adds a new material fact late in the process, gates should surface the issue instead of letting it silently enter the final document.

---

## 🚀 Quick start

### macOS / Linux

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

`scripts/demo.sh` creates an API-free reference workspace with a final brief,
Claim Ledger, Quality Panel HTML, Quality Summary, source appendix, and
event-log excerpt.

For the shortest walkthrough, use the
[15-Minute Pilot](docs/15-minute-pilot.md).

Package-index installs are not the launch path yet. `pipx` / PyPI packaging is
tracked in [pipx And PyPI Packaging Prep](docs/packaging-pipx.md); do not use
`pipx install briefloop` until release notes say a real package-index artifact
has been published and smoked.

When you are ready to use your own materials, create your first briefing
workspace:

```bash
briefloop onboard
briefloop init ~/briefloop-workspace --from-onboarding onboarding.json
briefloop runtime install --workspace ~/briefloop-workspace --runtime codex
briefloop run --workspace ~/briefloop-workspace --runtime codex
```

New runs use a fresh SQLite `briefloop.db` as their only runtime authority.
JSON-only workspaces are not migrated or accepted as control input. The Codex
adapter is Experimental: it freezes `role_topology=single_session`, executes
separate Receipt-backed role invocations in one shared Codex session, and keeps
JSON, Markdown, HTML, status and Quality Panel files as replaceable projections.

Common setup helpers:

```bash
briefloop init --from-onboarding onboarding.json <workspace>
briefloop run --workspace <workspace> --runtime codex
```

For workspaces that use the `llm_decide` source profile, source discovery runs
through the runtime-host route (`run --runtime codex` → `runtime next`).

### Windows PowerShell

Windows does not require WSL or Git Bash. PowerShell is the recommended path.

```powershell
winget install Python.Python.3.12

git clone https://github.com/Stahl-G/briefloop.git
cd briefloop

.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

briefloop version
```

If PowerShell blocks script execution:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

---

## 🤖 Runtime transition

The former Claude/Hermes/OpenCode/operator JSON-control paths are not entrypoints
for a new SQLite run. Their retained assets are historical surfaces pending the
separate legacy deletion unit. Do not use them to continue or migrate a
JSON-only workspace.

For the active Experimental Codex path, install the packaged workspace kit:

```bash
briefloop runtime install --workspace <workspace> --runtime codex
briefloop run --workspace <workspace> --runtime codex
```

Good next reads:

- [Getting Started](docs/getting-started.md)
- [Weekly Loop](docs/weekly-loop.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Claude Code quickstart](docs/claude-code-quickstart.md)

---

## 🧪 Three ways to try it

Start from the product entry that matches your report:

| Report job | Start with | Best for |
|---|---|---|
| Industry or market weekly | `industry-weekly` | recurring market updates, competitor tracking, policy monitoring |
| Management monthly | `management-monthly` | executive reviews, monthly operating updates, management briefing packs |
| Document review | `document-review` | reviewing a set of documents with page/source traceability |

```bash
briefloop new industry-weekly ./weekly-brief
briefloop new management-monthly ./monthly-review
briefloop new document-review ./document-review
```

`document-review` prepares a document evidence review workspace. It does not
make legal, compliance, or disclosure judgments.

Start with the three supported entries above. Other experimental entries are
documented later for users who already understand the basic loop.

| Path | Best for | What to do |
|---|---|---|
| Look once | You want to understand the project quickly | Run the demos and read the reference runs |
| Run once | You want to test it on a few local files | Create a workspace, add materials, run one brief |
| Use weekly | You want a recurring workflow | Configure sources, sections, reader preferences, and feedback |

Optional demo commands:

```bash
bash scripts/demo.sh
bash scripts/demo-deep-dive.sh
```

The demos use synthetic materials. They show the evidence chain and gate behavior. Real use starts with your own workspace and sources.

---

## 🧭 Current status

Current version: **v0.13.0**

Current main entrypoints:

- CLI: `briefloop`
- Experimental SQLite-only Codex runtime: `briefloop run --workspace <path>
  --runtime codex`, followed by `briefloop runtime next`,
  `invocation-start`, `invocation-accept|fail`, and `apply`
- experimental WorkBuddy / CodeBuddy guide: [docs/workbuddy.md](docs/workbuddy.md)
- experimental offline-shadow LAJ: `briefloop experiments laj shadow-run` and
  `briefloop experiments laj present` for public/synthetic advisory evaluation
  and standalone JSON/Markdown/HTML presentation; an explicitly supplied
  current-report-bound `laj.json` may be displayed read-only with
  `briefloop quality summarize --laj-view <laj.json>`

v0.13.0 makes the SQLite ControlStore the sole runtime authority:

- JSON-only workspaces are classified unsupported (no importer, no migration,
  no dual read/write); the Codex runtime host (`briefloop run --workspace
  <path> --runtime codex` followed by `briefloop runtime next`,
  `invocation-start`, `invocation-accept|fail`, and `apply`) is the active
  execution path, verified green on macOS and Windows without fail-fast
- retired JSON/operator public commands fail closed with typed rejections
  (`runtime_command_unsupported` / `legacy_workspace_unsupported`) and zero
  writes; LEGACY-DELETE tier-1 removes their handler layer, six unreachable
  modules, and three unreferenced scripts while the legacy JSON runtime-state
  stack remains as declared internal debt tracked for LEGACY-DELETE-2
- source discovery runs through the runtime-host route (`sources decide` is
  retired by design); finalize and delivery run as typed Store actions

The carried-forward v0.11 product-baseline, WorkBuddy adapter,
operator-runtime, and semantic-support auditor hardening line includes:

- `ReportSpec`, `ReportPack`, `ReportTemplate`, and `PolicyProfile` contracts
- workspace skeletons and deterministic PolicyProfile resolution
- delivery / audit bundle manifests and clean bundle archives
- supported `industry-weekly`, `management-monthly`, and `document-review`
  product entrypoints
- bounded `evidence_extract` source/scope registration, source locks, logical
  page inventory seeds, and text-span seed registries
- experimental SourceHub Lite setup for local files, RSS feeds, and runtime web-search handoff tasks
- durable source evidence pack materialization and source taxonomy normalization
- internal release-mode approval records
- Quality Panel JSON / Markdown / HTML projections and audit-bundle integration
- standalone experimental LAJ JSON / Markdown / HTML second-opinion artifacts
  and an optional read-only Quality Panel section for an explicit bound view;
  they do not affect workflow status, gates, finalization, delivery, repair,
  approval, recommended authoritative actions, or next-action authority, and
  evaluator efficacy is not measured
- reader-quality warning/projection surfaces for template conformance,
  materiality selection, support-calibrated wording, citation profiles,
  coverage/omission, and scoped final-abstract diagnostics
- trajectory-regulation decision narrowing for repeated retry/repair/blocker
  loops
- experimental source-clone WorkBuddy Skill packaging and first-use routing
- experimental source-clone CodeBuddy project Skill and role-agent handoff
  through `--runtime codebuddy`
- `operator` runtime for host-agnostic compact operation; historical `manual`
  manifests are read-only and require an explicit reset to a canonical runtime
- proposal-only Semantic Support Auditor surfaces and human adjudication records
  that do not create support truth, gates, delivery approval, or release
  authority
- public-safe reference, synthetic regression, minimal comparative evaluation,
  launch smoke, and release checklist guardrails

These features are meant to make report types, source evidence, default policies,
delivery bundles, and operator quality visibility more product-like.

These features are still contracts, metadata, defaults, setup paths, approval
records, deterministic warnings, and projection controls. They do not parse PDFs automatically,
execute hidden web search or crawling, judge industry compliance, detect
investment advice, verify internet rumors, claim IR/disclosure readiness,
prove semantic truth, publish reports, or authorize public release.

---

## 🚧 What it is not

BriefLoop currently does not:

- publish reports automatically;
- bypass human review;
- prove that a source semantically supports every sub-claim;
- replace legal, compliance, investment, or disclosure judgment;
- claim that generated content is ready for IR, SEC, or regulatory disclosure;
- turn unapproved feedback into long-term memory;
- promise one-click perfect reports.

The current core claim is deliberately narrow:

> **Traceability, not semantic proof yet.**
> BriefLoop aims to make a briefing process traceable, reviewable, and accountable. Semantic proof and automatic judgment are future work, not current guarantees.

---

## 💡 Why this exists

Coding agents improved quickly because software work has infrastructure: tests, CI, git history, code review, and rollbacks.

Business briefings usually do not. A junior analyst is corrected verbally, and the correction disappears. A stale number enters a weekly report, and nobody can tell which step allowed it. A team learns what “good” looks like, but the learning stays informal.

BriefLoop brings software-engineering discipline into recurring briefing work: auditability, structured feedback, human gates, execution traces, and tests.

The goal is not to remove human judgment. The goal is to let humans spend more time judging, questioning, and advising, and less time re-checking the same preventable mistakes.

---

## 📖 Glossary

| Term | Plain meaning |
|---|---|
| Claim Ledger | A record of important claims, numbers, sources, and dates |
| Source Pack | The set of materials available to a run |
| Quality Gate | A check that must pass before a stage or delivery can proceed |
| ControlStore | The authoritative SQLite state for a fresh Codex run |
| Transaction Receipt | A deterministic record of an accepted Store action |
| Orchestrator / 司乐师 | The runtime role that coordinates stages and boundaries |
| Delivery Bundle | The Markdown / Word files meant for readers |
| Audit Artifacts | Intermediate records used for review, debugging, and accountability |

---

## 🗂️ Useful docs

First-user path:

- [Getting Started](docs/getting-started.md)
- [Weekly Loop](docs/weekly-loop.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Golden reference workspace](examples/reference-workspaces/industry-weekly-demo/README.md)

Architecture reference and contributor docs:

- [Function Map](docs/features.md)
- [Golden Path](docs/golden-path.md)
- [Architecture Status](docs/architecture-status.md)
- [Naming and compatibility](docs/briefloop-naming.md)
- [Roadmap](docs/roadmap.md)
- [Red lines and anti-patterns](docs/red-lines-and-anti-patterns.md)
- [Product OS reader-quality reference package](docs/reference-runs/v0.11.3-product-os-reader-quality-reference.md)
- [Minimal comparative evaluation packet](docs/evaluation-results/v0.11.4-minimal-comparative-evaluation/README.md)
- [Synthetic regression pack](docs/reference-runs/v0.11.1-synthetic-regression-pack.md)
- [Public solar integration run](docs/reference-runs/v0.7.2-public-solar-integration.zh-CN.md)
- [Organoid-industry failure study](docs/reference-runs/v0.7.4-organoid-failure-study.md)
  ([中文](docs/reference-runs/v0.7.4-organoid-failure-study.zh-CN.md))

---

## 🤝 Collaboration

This project needs real scenarios more than it needs more concepts.

You are welcome to participate if you:

- write recurring market, strategy, policy, IR, or executive briefings;
- want to pilot BriefLoop on a real workflow;
- research agent evaluation, human-in-the-loop systems, or auditable AI workflows;
- want to contribute through issues, docs, tests, or example scenarios.

Start with a [good first issue](https://github.com/Stahl-G/briefloop/issues). Please read [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md) before contributing.

For human contact, see [briefloop.ai](https://briefloop.ai) or
[docs/contact.md](docs/contact.md). Use
[security@briefloop.ai](mailto:security@briefloop.ai) for private security
reports.

---

## 📄 License

MIT
