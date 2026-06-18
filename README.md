# 🧾 MABW — Process Accountability for the Briefing Loop

**An AI briefing workflow that can answer "where did this number come from?"**

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a>
</p>

Current version: **v0.8.5**

> When someone asks where a number came from, MABW does not ask the model to improvise an explanation. It opens the ledger.

MABW turns AI-assisted business briefing into an accountable loop with four steps: **find and screen evidence -> freeze key facts into a Claim Ledger -> write only from the ledger -> audit independently.** A human delivers the final brief. Always.

The v0.8.5 preview release adds the MABW-080 experiment harness path: validate an experiment case, register a completed run, build a deterministic scorecard draft, and import external manifestation assessment. It is measurement infrastructure, not proof that output quality improved.

The core claim is deliberately narrow: **traceability, not semantic proof yet.** Important claims link to registered source entries with source, date, and gate metadata. That tells you where a claim entered the workflow; it does not yet prove the source semantically supports each sub-claim. We published [a failure study](docs/reference-runs/v0.7.4-organoid-failure-study.md) where exactly that boundary was exposed by an external reviewer, because accountability applies to this project too.

## 🚀 Get Started

**Install From Source — macOS / Linux**

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
```

**Install From Source — Windows PowerShell**

Windows does not require WSL or Git Bash. PowerShell is the recommended Windows path.

```powershell
winget install Python.Python.3.12

git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow

.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1

multi-agent-brief version
```

If PowerShell blocks script execution:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

**Create Your First Brief**

```bash
multi-agent-brief onboard
multi-agent-brief init ~/mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ~/mabw-workspace
```

**Optional: Inspect The Demo**

```bash
bash scripts/demo.sh
bash scripts/demo-deep-dive.sh
```

The demo is for reviewers and visitors who want to inspect the evidence chain on synthetic materials. Real use starts with onboarding and a workspace created for your own brief.

Advanced Windows installer: `irm https://raw.githubusercontent.com/Stahl-G/multi-agent-brief-workflow/main/scripts/install.ps1 | iex` exists, but is currently an Experimental CLI-only installer asset. The default path is source clone plus `scripts/setup.ps1`.

1,000+ deterministic tests run in CI with zero LLM calls.

## 🧯 What Broke, And What Didn't

I'm a management trainee at a manufacturer. Two months into the job, I was writing real weekly briefings for executives and doing what everyone does: orchestrating a carefully prompted role chain for finding, screening, analysis, editing, and audit.

The deterministic parts never broke. Everything entrusted to prompts eventually did. Facts mutated in handoffs. Weak sources became confident conclusions. The system needed so much human re-verification that it was no better than one long prompt.

The lesson MABW is built on: **if a rule actually matters, it cannot live in a prompt.** It has to become a schema, a validator, a gate, a transaction, an event log, or a test.

## 🧱 The Design Rule

> The smart parts have no authority.
> The authoritative parts are deterministic.
> Nothing takes effect without a human.
> Nothing passes a human without a record.

Concretely: Python owns state and ledgers and never calls an LLM. The LLM runtime owns drafts and executes content work through a contract handoff. Humans own approval. **One writer per field**: no module gets to "helpfully" update state it does not own, because shared writers are how audit trails and rollbacks die.

## 📚 What A Run Leaves Behind

A delivered brief is just two files: `output/delivery/brief.md` and a `.docx`. Everything else a run produces exists so the brief can be questioned later. Synthetic excerpts below use fictional entities and show structure only.

`output/delivery/brief.md`

```markdown
## 2. Market Updates
This week, the sample PV module spot price fell 1.8% week over week,
the third consecutive weekly decline. Company N announced Phase I of
its sample-state factory started production, with planned capacity of 2 GW...
```

`output/intermediate/claim_ledger.json`: the registered entry behind that number:

```json
{
  "claim_id": "CL-0012",
  "statement": "The sample module spot price fell 1.8% week over week.",
  "source_id": "SRC-003",
  "source_date": "2026-06-05",
  "support": "supported"
}
```

`output/intermediate/gates/auditor_quality_gate_report.json` and `output/intermediate/gates/finalize_quality_gate_report.json`: deterministic checks that can block audit completion or delivery. `output/intermediate/quality_gate_report.json` is a latest/legacy projection. There is no force flag.

```json
{
  "gate_id": "freshness",
  "status": "pass",
  "findings": []
}
```

In a contract-following run, important numbers in the delivered brief link back to registered source entries. Stale sources and unsupported numbers are surfaced by gates and audit instead of silently entering the final document. The full execution trace lives in `event_log.jsonl`.

## 🧩 The Four Things It Tracks Every Week

The writer-facing mental model is not "28 control surfaces." Each run keeps four practical questions answerable:

| Question | What it records | Where you look |
|---|---|---|
| Where is this run? | Current stage, missing artifacts, blockers, next safe action | `/mabw status`, `workflow_state.json` |
| Where did each number come from? | Claim Ledger entries, source dates, stage-scoped gate findings | `claim_ledger.json`, `gates/*_quality_gate_report.json`, `source_appendix.md` |
| What has it learned? | Human-approved reader preferences only; unapproved suggestions never take effect | `improvement/ledger.jsonl` (append-only, hash-chained, revertible) |
| What is guarding delivery? | Stage-completion transactions, reader-final gate, delivery checks | `finalize_report.json`, `state finalize-complete` |

> It observes and it proposes. But only what you approve is remembered: in a ledger you can open, audit, and undo.

## 🔬 Evidence, Including The Failures

- **[Public solar integration run (v0.7.2)](docs/reference-runs/v0.7.2-public-solar-integration.md)**: Improvement Memory materialization, gate execution, and control-plane closure on public materials. It is an integration reference, not a causal claim about output quality.
- **[Organoid-industry failure study (v0.7.4)](docs/reference-runs/v0.7.4-organoid-failure-study.md)**: a real external research task where an external reviewer caught semantic mismatches the gates passed *by design*. Includes a five-error taxonomy of how each mistake entered the pipeline. This is the honest current boundary of the system.
- **[v0.8.5 preview release notes](docs/releases/v0.8.5.md)**: MABW-080 experiment harness metadata, deterministic scorecard drafts, assessment import, and delivery snapshot convenience copies. The operator sequence is documented in the [MABW-080 experiment guide](docs/experiments-080.md).

We can say precisely which ledger line each error entered through. That is what the system is for, and it is also why we publish the failure analysis.

## 🚫 What It Is Not

MABW is not an autonomous agent. It does not auto-edit brief content, does not auto-learn, has no long-term memory system, and is not an investment-advice tool or a replacement for human review.

The current first-class writer path is Claude Code. Hermes is supported as a delegated / scheduled runtime. OpenCode, Codex, and manual entrypoints exist, but they are not yet end-to-end validated as the primary writer path.

## 🛠️ Why This Exists

Coding agents improved fast because their loop has infrastructure: tests, CI, git history, code review. Business briefings have none of that. A junior analyst gets corrected verbally and the correction evaporates; the next hire repeats the mistake. A stale number slips into a brief and nobody can say at which step. The work is important, repetitive, and structurally unable to get better.

MABW moves that same machinery: auditability, structured feedback, human gating, execution traces, into a domain with no clean reward signal, where the human *is* the reward channel and deterministic gates build the reward surface.

## 🧑‍💻 Using It For Real Work

Install the writer entrypoint and use five verbs inside Claude Code:

```bash
source .venv/bin/activate
multi-agent-brief claude install --repo-workdir .
```

```text
/mabw new
/mabw run <workspace>
/mabw status <workspace>      # strictly read-only
/mabw feedback <workspace>    # recorded immediately; takes effect only after approval
/mabw deliver <workspace>     # always human-triggered, gated, no force flag
```

Three on-ramps, one spine. There is no lite mode: entry cost drops, the accountability spine does not. Claim Ledger, gates, human delivery, event trace, and frozen snapshots stay present.

| Path | Time | What you do |
|---|---:|---|
| Look once | ~5 min | Read the reference runs, run the demos |
| Run once | ~30 min | A few local text files, no search backend, `new -> run -> status -> deliver` |
| Live with it | weekly | Configured sources, feedback loop, approved preferences |

Full paths: [Claude Code quickstart](docs/claude-code-quickstart.md) · [golden path](docs/golden-path.md) · [weekly use](docs/weekly-use.md) · [onboarding](docs/onboarding.md) · [search backends](docs/search-backends.md) · [MABW-080 experiment guide](docs/experiments-080.md) · [docs index](docs/README.md) · [roadmap](docs/roadmap.md) · [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md)

## 🧭 A Note On Provenance

I build and use MABW as part of my actual weekly briefing work at a listed manufacturer, in a role that touches strategy and investor relations. Nothing from my employer enters this repository: no data, no documents, no non-public information. What crosses over is discipline, not data: patterns rewritten from memory in vocabulary that holds for any company. And where this project makes guarantees, they are written as mechanisms: schemas, gates, transactions, tests, not as promises.

## 🎼 Why The Orchestrator Is Called 司乐师

The runtime orchestrator is named after the office in the Chinese ritual-music tradition responsible for keeping ensembles in time and in order. It does not write; it dispatches the specialist roles and holds them to their contracts. In the default topology, Scout also performs screening while keeping screened candidates as a separate artifact; strict topology keeps Screener independent. Not a strict historical reconstruction: a project term for the thing that maintains tempo, boundaries, and delivery discipline.

## 🗺️ Roadmap

**v0.8**: measurement, fast-rerun, role topology, and evaluation — timing projection, same-evidence reruns, default/strict topology choices, and controlled experiment tooling without weakening accountable artifacts.

**v0.9**: distribution and reference workflows.

**v1.0**: frozen schemas and CLI surface, compatibility policy, and threat model.

## 🤝 Collaboration

This project needs real scenarios more than it needs features. If you write recurring briefings in strategy, equity research, IR, policy tracking, or similar work and want to run your real workflow through it, open an issue or discussion. If you research agent evaluation and want a dogfooded process-accountability system with run data, that is also welcome.

Start with a [good first issue](https://github.com/Stahl-G/multi-agent-brief-workflow/issues). Read [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md) first.

## License

MIT
