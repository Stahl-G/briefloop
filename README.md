# 🧾 BriefLoop

**AI-assisted business briefings you can question later.**

Formerly **MABW — Multi-Agent Brief Workflow**.
The public project name is now **BriefLoop**; MABW remains as the implementation lineage and compatibility surface.

[English](README.md) | [简体中文](README.zh-CN.md)

[Function Map](docs/features.md) · [Golden Path](docs/golden-path.md) · [Architecture Status](docs/architecture-status.md) · [Roadmap](docs/roadmap.md)

Writer entry: use `/briefloop` in Claude Code (`/mabw` remains a compatibility alias), or `briefloop` / `multi-agent-brief` in a shell.

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
| 6. Keep approved feedback | Only human-approved reader preferences are reused | “Do this next time” becomes visible and reversible |

The design rule is simple:

> Smart parts may write and propose. Authoritative parts must be checkable. Nothing takes effect without a human decision.

---

## 📚 The four questions it keeps answerable

| Question | What BriefLoop records | Where to look |
|---|---|---|
| Where is this run? | Current stage, missing artifacts, blockers, next safe action | `/briefloop status`, `workflow_state.json`, `agent_handoff.md` |
| Where did each number come from? | Claim Ledger entries, source dates, source appendix, gate findings | `claim_ledger.json`, `source_appendix.md`, `quality_gate_report.json` |
| What has it learned? | Human-approved reader preferences only | `improvement/ledger.jsonl`, `improvement_memory_snapshot.md` |
| What guards delivery? | Stage-completion records, reader-final gate, delivery checks | `finalize_report.json`, `state finalize-complete` |

It can observe and propose. Only what you approve is remembered, and it is remembered in a ledger you can open, audit, and undo.

---

## 📦 What you get from a run

A normal delivered brief is usually:

- `output/delivery/brief.md`
- `output/delivery/<report-name>.docx`

The workflow also keeps audit artifacts, such as:

- `output/intermediate/claim_ledger.json`
- `output/source_appendix.md`
- `output/intermediate/quality_gate_report.json`
- `event_log.jsonl`
- `improvement/ledger.jsonl`

Those audit files are not meant to be read by every end user. They exist so a team can answer questions, debug failures, review decisions, and hand the process to someone else.

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
```

Create your first briefing workspace:

```bash
multi-agent-brief onboard
multi-agent-brief init ~/mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ~/mabw-workspace
```

Common setup helpers:

```bash
multi-agent-brief init --from-onboarding onboarding.json <workspace>
multi-agent-brief sources decide --config <workspace>/config.yaml
```

`sources decide` is the explicit source-discovery helper for workspaces that use
the `llm_decide` source profile.

### Windows PowerShell

Windows does not require WSL or Git Bash. PowerShell is the recommended path.

```powershell
winget install Python.Python.3.12

git clone https://github.com/Stahl-G/briefloop.git
cd briefloop

.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

multi-agent-brief version
```

If PowerShell blocks script execution:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

---

## 🤖 Claude Code path

If you use Claude Code, install the writer entrypoint:

```bash
source .venv/bin/activate
multi-agent-brief claude install --repo-workdir .
```

Then use the five main writer commands:

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> [text-or-file]
/briefloop deliver <workspace>
```

`/mabw` remains available as a compatibility alias.

Good next reads:

- [Golden Path](docs/golden-path.md)
- [Weekly Use](docs/weekly-use.md)
- [Claude Code quickstart](docs/claude-code-quickstart.md)

---

## 🧪 Three ways to try it

Start from the product entry that matches your report:

```bash
briefloop new industry-weekly ./weekly-brief
briefloop new management-monthly ./monthly-review
briefloop new document-review ./document-review
```

These entries are user-facing aliases. Internal `report_spec.yaml` values stay
canonical, for example `market_weekly`, `management_monthly`, and
`evidence_extract`. `document-review` prepares a document evidence review
workspace; it does not make legal, compliance, or disclosure judgments.

These three entries are the v0.11 product-baseline target. In the current
v0.10.7 release line they are guarded by readiness checks; advanced Product OS
surfaces such as `solar-periodic`, Quality Panel, SourceHub Lite, and internal
release approvals remain experimental.

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

Current version: **v0.10.7**

Current main entrypoints:

- CLI: `multi-agent-brief`
- shell alias: `briefloop`
- Claude command: `/briefloop`
- compatibility alias: `/mabw`

v0.10.7 releases the accumulated experimental Product OS hardening line, including:

- `ReportSpec`, `ReportPack`, `ReportTemplate`, and `PolicyProfile` contracts
- workspace skeletons and deterministic PolicyProfile resolution
- delivery / audit bundle manifests and clean bundle archives
- experimental `evidence_extract` source/scope registration
- experimental SourceHub Lite setup for local files, RSS feeds, and runtime web-search handoff tasks
- durable source evidence pack materialization and source taxonomy normalization
- internal release-mode approval records
- Quality Panel JSON / Markdown / HTML projections and audit-bundle integration

These features are meant to make report types, source evidence, default policies,
delivery bundles, and operator quality visibility more product-like.

These features are still metadata, defaults, setup paths, approval records, and
projection controls. They do not parse PDFs automatically, execute hidden web
search or crawling, judge industry compliance, detect investment advice, verify
internet rumors, claim IR/disclosure readiness, prove semantic truth, publish
reports, or authorize public release.

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
| Reader Preference | Human-approved guidance such as “lead with business impact” |
| Improvement Ledger | The record of approved feedback |
| Orchestrator / 司乐师 | The runtime role that coordinates stages and boundaries |
| Delivery Bundle | The Markdown / Word files meant for readers |
| Audit Artifacts | Intermediate records used for review, debugging, and accountability |

---

## 🗂️ Useful docs

- [Function Map](docs/features.md)
- [Golden Path](docs/golden-path.md)
- [Weekly Use](docs/weekly-use.md)
- [Architecture Status](docs/architecture-status.md)
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

---

## 📄 License

MIT
