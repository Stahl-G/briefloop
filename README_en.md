# BriefLoop

**Open-source loop engineering for auditable business briefings.**
Formerly **MABW — Multi-Agent Brief Workflow**.

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="docs/features.md">🧭 Function Map</a> ·
  <a href="docs/golden-path.md">🚀 Golden Path</a> ·
  <a href="docs/architecture-status.md">🧱 Architecture Status</a> ·
  <a href="docs/roadmap.md">🗺️ Roadmap</a>
</p>

Current version: **v0.10.1**
Public framing: **BriefLoop / MABW compatibility period**
Current CLI: `multi-agent-brief` (`briefloop` shell alias also works)
Current Claude command: `/briefloop` (`/mabw` compatibility alias also works)

## AI briefs that can answer "where did this number come from?"

> When someone asks "where did this number come from?", BriefLoop does not ask the model to improvise an explanation. It opens the ledger.

BriefLoop is an **open-source brief-loop engineering harness** for business, research, market, policy, company-tracking, investor-relations, and management-reporting briefs. It is not a prompt that makes AI write faster. It turns recurring briefings into governed loops: source packs, claim ledgers, quality gates, human decisions, structured findings, scoped repairs, regression cases, and release records.

The v0.10.1 release adds the experimental Product OS PolicyProfile layer while
keeping MABW as the implementation lineage and compatibility surface. Runtime
commands, the Python package, workspace formats, artifact names, and MABW-080
experiment IDs remain unchanged.

This Product OS layer adds experimental ReportSpec, ReportPack, ReportTemplate,
PolicyProfile defaults, resolved policy projection, conservative workspace
skeletons, delivery/audit bundle manifests, a limited deterministic gate
adapter, and public-safe dogfood fixtures. These are product metadata,
deterministic-default, and projection controls only; they do not judge industry
compliance, detect investment advice, verify internet rumors, claim
IR/disclosure readiness, prove semantic truth, publish reports, or authorize
release.

The v0.9.x experimental support surfaces remain traceability, support-record,
and proposal controls: Atomic Claim Graph, Evidence Span Registry,
Claim-Support Matrix, and Semantic Assessment Report. They are not semantic
support proof, accepted support truth, adjudication queues, delivery gates,
release eligibility, or support-sufficiency gates.

It is built for:

- people who produce weekly market briefs, competitor tracking, policy notes, IR drafts, or leadership updates;
- teams that want AI briefs to be traceable instead of merely plausible;
- researchers and investors studying how agent workflows can provide process accountability in domains without clean reward signals.

<p align="center">
  <a href="#quick-start">🚀 Quick Start</a> ·
  <a href="docs/features.md">🧭 Function Map</a> ·
  <a href="docs/reference-runs/v0.7.2-public-solar-integration.md">🔬 Public Run</a> ·
  <a href="docs/reference-runs/v0.7.4-organoid-failure-study.md">🧯 Failure Study</a> ·
  <a href="docs/releases/v0.10.1.md">📦 v0.10.1</a>
</p>

## Why It Is Worth Looking At 👀

**For writers**: you get more than an AI draft. You get a delivery bundle that can point to sources, dates, gates, and revision records.

**For team leads**: briefing quality does not live only in someone's memory. Sources, formats, reader preferences, and quality boundaries can become reusable workflow assets.

**For researchers and investors**: BriefLoop is a dogfooded process-accountability agent workflow. It publishes not only success evidence, but also failure boundaries.

The core claim is deliberately narrow: **traceability, not semantic proof yet**. Important claims link to registered source entries with source/date/gate metadata. That tells you where the claim entered the workflow; it does not yet prove that the source semantically supports every sub-claim.

## How It Works 🧭

| Step | What happens | Why it matters |
|---|---|---|
| 🔎 Source collection | Gather candidate evidence from local files, cached packs, or search providers | The model does not start from empty context |
| 🧾 Claim Ledger | Register important facts with source and date metadata | Numbers, dates, entities, and sources become inspectable |
| ✍️ Agent drafting | Default topology lets Scout find and screen; strict topology keeps Screener independent; Analyst, Delivery Editor, and Auditor do bounded work | Writing is split into stages with contracts |
| 🚦 Gates | Freshness, material-fact, target-relevance, editor-new-fact, and reader-final checks | Deterministic checks do not rely on prompt memory |
| 📦 Delivery and learning | Render Markdown / Word and preserve trace, feedback, and approved preferences | Humans deliver; the system records what happened |

One-line architecture rule: **smart parts have no authority; authoritative parts are deterministic; effective changes require humans; human decisions leave traces.**

## The Four Things It Tracks Each Week 🧩

The writer-facing model is not "how many control surfaces exist." Each run is meant to keep four practical things visible:

| Question | What it records | Where you see it |
|---|---|---|
| What stage this run is in | Current stage, missing artifacts, blockers, and the next safe action | `/briefloop status`, `workflow_state.json`, `agent_handoff.md` |
| Where each number came from | Claim Ledger records, source dates, audit results, and stage-scoped gate findings | `claim_ledger.json`, `gates/*_quality_gate_report.json`, `source_appendix.md` |
| What reader preferences were approved | Human-approved reader guidance only; unapproved suggestions do not take effect | `improvement/ledger.jsonl`, `improvement_memory_snapshot.md` |
| What checks are guarding delivery | Completion transactions, reader-final gate, source appendix, and delivery checks | `finalize_report.json`, `reader_clean`, `state finalize-complete` |

> BriefLoop can observe and suggest, but only what you approve is remembered, and every approved memory stays in an inspectable ledger you can undo.

See [docs/what-mabw-keeps-track-of.md](docs/what-mabw-keeps-track-of.md) for the user-facing explanation.

## Evidence To Inspect 🔬

- [v0.7.2 public solar integration summary](docs/reference-runs/v0.7.2-public-solar-integration.md): shows Improvement Memory materialization, gate execution, and control-plane closure. It is an integration reference, not proof of output-quality improvement or strict causal effect.
- [v0.7.4 organoid-industry failure study](docs/reference-runs/v0.7.4-organoid-failure-study.md): a real external research case that exposed the current source-to-claim semantic support boundary. BriefLoop traced how errors propagated; it did not prove semantic correctness.
- [v0.10.1 release notes](docs/releases/v0.10.1.md): experimental Product OS PolicyProfile layer: ReportSpec/ReportPack registry, workspace skeletons, bundle projection, resolved policy projection, limited deterministic gate adapter, and public-safe dogfood fixtures. It does not provide industry compliance judgment, investment-advice detection, IR readiness, semantic truth, automatic publication, or release authority.
- [v0.9.4 release notes](docs/releases/v0.9.4.md): experimental Semantic Assessment Report proposal surface: schema, reference validation, proposal projection, status visibility, and public-safe dogfood fixtures. It does not create support truth, adjudication queues, delivery gates, or release authority. The MABW-080 operator sequence remains documented in the [MABW-080 experiment guide](docs/experiments-080.md).
- [Evidence Span Registry](docs/evidence-span-registry.md): mainline experimental span schema, source-pack byte binding, archive projection, and Source Appendix trace view. It is not semantic support proof or a support-sufficiency gate.
- [Claim-Support Matrix](docs/claim-support-matrix.md): mainline experimental support-record schema, cross-artifact validation, and gate/status projection from explicit atom-to-evidence rows. It is not automatic support assessment, truth proof, or release eligibility.

We publish failure analysis because accountability applies to this project too.

## What You Get 📦

The final delivery bundle contains only `output/delivery/brief.md` and `output/delivery/<named>.docx`. When source appendix output is configured, the source list is appended to those delivery files; standalone `output/source_appendix.md`, Claim Ledger, audit report, and audited brief remain audit/control records, not extra reader handoff files.

The example below is **synthetic** and only demonstrates structure:

`output/delivery/brief.md` excerpt:

```markdown
## 2. Market Updates
This week, the sample photovoltaic module spot price fell 1.8% week over week, marking a third consecutive weekly decline.
Company N announced that Phase I of its sample-state factory started production this week, with planned annual capacity of 2 GW...
```

Corresponding `output/intermediate/claim_ledger.json` excerpt:

```json
{
  "claim_id": "CL-0012",
  "statement": "The sample module spot price fell 1.8% week over week.",
  "source_id": "SRC-003",
  "source_date": "2026-06-05",
  "support": "supported"
}
```

`output/intermediate/gates/auditor_quality_gate_report.json` excerpt (`output/intermediate/quality_gate_report.json` is only the latest/legacy projection):

```json
{
  "gate_id": "freshness",
  "status": "pass",
  "findings": []
}
```

In a contract-following run, important numbers in the delivered brief should link to registered source entries with source/date metadata. Stale source metadata and obvious unsupported numbers should be surfaced by audit and gates instead of silently entering the final document. The execution trace is recorded in `event_log.jsonl`.

## Quick Start

**Install From Source — macOS / Linux**

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
```

**Install From Source — Windows PowerShell**

Windows does not require WSL or Git Bash. PowerShell is the recommended Windows path.

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

The demo is for reviewers and GitHub visitors who want to inspect the evidence chain on synthetic materials. It is not required before using the product.

Advanced Windows installer: `irm https://raw.githubusercontent.com/Stahl-G/briefloop/main/scripts/install.ps1 | iex` exists, but is currently an Experimental CLI-only installer asset. The default path is source clone plus `scripts/setup.ps1`.

For the Claude Code writer path, install the writer entrypoint:

```bash
source .venv/bin/activate
multi-agent-brief claude install --repo-workdir .
```

Then use the five writer verbs inside Claude Code CLI or the Claude Desktop Code tab:

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> [text-or-file]
/briefloop deliver <workspace>
```

`/mabw` remains a compatibility alias for the same five writer verbs.

For a shell-level local-first product skeleton from a packaged experimental report pack:

```bash
briefloop new market-weekly ./weekly-brief
```

This creates editable workspace files and `report_spec.yaml`; it does not run agents, render templates, deliver, or approve publication.
`briefloop new` can accept an explicit `--policy-profile` or deterministic
`--industry` hint; the selected profile and resolution source are written into
`report_spec.yaml` rather than inferred silently at gate time.

See [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) for the full Claude Code path. Writer-facing operator notes are available in [docs/golden-path.md](docs/golden-path.md) and [docs/weekly-use.md](docs/weekly-use.md); Chinese versions are [docs/golden-path.zh-CN.md](docs/golden-path.zh-CN.md) and [docs/weekly-use.zh-CN.md](docs/weekly-use.zh-CN.md).

## Product Boundary 🧱

Current release baseline: v0.10.1

The v0.10.1 release adds the experimental Product OS PolicyProfile layer: ReportSpec / ReportPack / ReportTemplate contracts, conservative workspace skeletons, delivery/audit bundle projection, resolved policy projection, limited deterministic gate adaptation, and public-safe dogfood fixtures. It does not rename the CLI, Python package, workspace artifacts, or experiment IDs. It does not judge industry compliance, detect investment advice, verify internet rumors, claim IR/disclosure readiness, prove semantic truth, publish reports automatically, or create release authority.

It is still not an autonomous agent, does not automatically edit brief content, does not automatically learn, does not provide a long-term memory system, and is not an investment advice tool, trading signal generator, or replacement for human review. See [architecture status](docs/architecture-status.md), [roadmap](docs/roadmap.md), and [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md).

## Why This Exists

In corporate strategy teams, sell-side research, buy-side research, investor relations, management offices, and similar environments, people spend a large amount of time producing daily reports, weekly reports, morning-meeting notes, and leadership briefs. The work matters, but the process is repetitive: find sources, decide what matters, remove stale or duplicate information, write a coherent brief, verify numbers, check whether AI invented anything, edit wording, and format the output.

The deeper problem is that this kind of work does not reliably improve. A junior analyst gets corrected verbally, the correction disappears, and the next person repeats the same mistake. A "this section feels wrong" comment evaporates after the meeting. A stale number enters a brief, and no one can tell where it slipped through.

Software engineering improves through tests, Git history, CI, and review. This project brings the same basic machinery into real briefing work: auditability, traceability, structured feedback, and human approval. The goal is to spend more time on judgment, questions, and decision support, and less time on repetitive copying and formatting.

## Three On-Ramps

BriefLoop does not have a "lite mode." The entry cost can be reduced, but the accountability spine stays: Claim Ledger, gates, human delivery, execution trace, and frozen snapshots.

| Path | Best for | How to start | What does not get lighter |
|---|---|---|---|
| Look once | You want to decide whether the project is relevant | Read the [public integration summary](docs/reference-runs/v0.7.2-public-solar-integration.md), then run `bash scripts/demo.sh` and `bash scripts/demo-deep-dive.sh` | The demo shows control behavior and traceability, not output-quality improvement |
| Run once | You want to try a few local materials | Skip search backend setup, use a small set of local text sources, and follow the [golden path](docs/golden-path.md) | Claim Ledger, gates, reader-final gate, and human delivery still apply |
| Live with it | You want a weekly workflow | Configure search sources, cadence, feedback, and approved preferences with the [weekly-use script](docs/weekly-use.md) | Unapproved preferences do not take effect; approved preferences only affect later frozen runs |

Do not use "audit an arbitrary external AI report" as the lightweight entrypoint. Without a Claim Ledger, external drafts can only receive shallow checks and cannot provide BriefLoop's accountability guarantees.

## Opening A New Sector

BriefLoop is useful when a one-off sector study needs to become a long-running, traceable monitoring workflow. For a new sector such as organoids, AI power demand, energy-storage supply chains, or a regulatory theme, start this way:

1. **Do one exploration pass first**: use Deep Research, human research, or expert interviews to build an initial map of the key questions, regulators, company universe, product categories, keywords, databases, media sources, and event types worth tracking.
2. **Do not treat the exploration report as the evidence layer**: it is a draft source universe, watchlist, and taxonomy. Important claims in later briefs still need to trace back to original filings, regulatory documents, company releases, financing disclosures, papers, or trusted media.
3. **Turn the map into workspace configuration**: encode policy, company/product, financing, and commercialization sections in `user.md` / onboarding config, and maintain reusable source lists, keywords, and company watchlists.
4. **Run weekly before going daily**: first use BriefLoop to process new weekly information, deduplicate, screen stale items, build the Claim Ledger, render the source appendix, and learn which events actually change judgment.
5. **Use feedback to shape the workflow**: stable requirements such as "lead with business impact before background," "do not make management decisions for the reader," or "verify this data type from primary sources" should enter feedback first, then be human-approved into the Improvement Ledger or later templates/gates.
6. **Increase cadence only after the shape stabilizes**: move from weekly to daily alerts or topic-specific monitoring only after the source pool, section structure, reader preferences, and gates are stable.

In short: Deep Research is for opening the map; BriefLoop is for monitoring the territory. Sector research is not a one-time search dump. It is an ongoing information-governance process.

## Run Your Own Materials

### Claude Code (Five-Verb Primary Path)

After installing from source, activate the virtual environment and install the
writer entrypoint:

```bash
source .venv/bin/activate

multi-agent-brief claude install --repo-workdir .
```

Then use the five writer verbs inside Claude Code CLI or the Claude Desktop Code
tab:

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> [text-or-file]
/briefloop deliver <workspace>
```

`/briefloop` is the BriefLoop writer command. `/mabw` is retained as a
compatibility alias during the BriefLoop transition. `status` calls the read-only
`multi-agent-brief status` helper, `feedback` records and triages without
acting downstream, and `deliver` must go through gates, the reader-final gate,
and `state finalize-complete`. `/generate-brief <workspace>` remains an
advanced/legacy full delegated workflow command for debugging or direct
subagent execution; it is not the first path for new writers.

See [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) for the full Claude Code path. Writer-facing operator notes are available in [docs/golden-path.md](docs/golden-path.md) and [docs/weekly-use.md](docs/weekly-use.md); Chinese versions are [docs/golden-path.zh-CN.md](docs/golden-path.zh-CN.md) and [docs/weekly-use.zh-CN.md](docs/weekly-use.zh-CN.md).

### Other Runtimes

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

Claude Code is the first-class writer / five-verb path. Hermes remains a
supported delegated/scheduled runtime path. OpenCode, Codex, and manual fallback
keep their existing entrypoints.

The Hermes plugin remains available for the native `delegate_task` path:

```bash
multi-agent-brief hermes install-plugin
hermes plugins enable mabw
```

Runtime installation details, workspace-local runtime kits, and common issues are covered in [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) and [docs/runtime-recipes.md](docs/runtime-recipes.md).

### Use Your Own Materials / Optional Capabilities

- Local input handling and onboarding: [docs/onboarding.md](docs/onboarding.md)
- Web search options, from runtime-provided search to Tavily/Exa/Brave API backends: [docs/search-backends.md](docs/search-backends.md)
- Source discovery candidate merge, including the `llm_decide` source profile: `multi-agent-brief sources decide --config <workspace>/config.yaml --merge`
- Feishu integration for collection and delivery: [docs/feishu-integration.md](docs/feishu-integration.md)
- SEC filing parsing: [docs/opencli-source-provider.md](docs/opencli-source-provider.md)
- Windows PowerShell: [docs/windows-powershell.md](docs/windows-powershell.md)
- Documentation language matrix: [docs/README.md](docs/README.md)

Common command snippets:

```bash
multi-agent-brief init --from-onboarding onboarding.json
multi-agent-brief sources decide --config <workspace>/config.yaml
```

## Record An Approved Reader Preference

v0.7.0 adds controlled Improvement Ledger / Improvement Memory. It stores human-authored, human-approved reader preferences such as "lead with the decision-relevant number when evidence supports it." It is not an automatic learning system and does not edit the brief by itself.

```bash
multi-agent-brief improve propose --workspace <workspace> \
  --guidance "Lead with the decision-relevant number when evidence supports it." \
  --category audience_mismatch \
  --scope brief \
  --source-summary "Operator-created audience guidance proposal."

multi-agent-brief improve approve --workspace <workspace> --entry-id AG-0001 --by <operator>
multi-agent-brief improve rebuild --workspace <workspace>
multi-agent-brief run --workspace <workspace> --skip-doctor
```

`approve` does not change an already-created current-run snapshot; the next `run` / `start` / `handoff` freezes the new snapshot. Runtime agents read only `output/intermediate/improvement_memory_snapshot.md`, not live `improvement/memory.md`. See [docs/modules/improvement.md](docs/modules/improvement.md).

## Looking For Collaborators

This project is developed from real manufacturing and briefing work. It needs more real scenarios more than it needs more features. If any of the following describes you, GitHub Issues or Discussions are welcome:

- **Pilot users**: you work in strategy, research, IR, management office, or a similar role and produce real weekly reports, competitor tracking, or leadership briefs. You are willing to run this on your workflow and report friction points.
- **Evaluation collaborators**: you work on LLM agents or multi-agent systems and are interested in comparing contract-governed workflows against single-model baselines. The project can provide system design, realistic scenarios, and run data.
- **Contributors**: start from a [good first issue](https://github.com/Stahl-G/briefloop/issues). Before submitting changes, read [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md).

## Glossary

| Chinese term | English | Meaning |
|---|---|---|
| 司乐师 | Orchestrator | Runtime main agent that coordinates, validates, records decisions, and gates delivery |
| 事实账本 | Claim Ledger | Registry of important claims and their evidence references |
| 运行交接单 | Runtime Handoff | Artifact that passes execution context and contract references to a runtime |
| 产物契约 | Artifact Contract | Definition of files produced, consumed, and validated by each stage |
| 质量门禁 | Quality Gate | Quality check before transition or delivery |
| 溯源图 | Provenance Graph | Audit graph projected from state, artifacts, claims, feedback, and gates |
| 控制台 | Control Switchboard | Control surface for available controls, recommendations, and Orchestrator selections |
| 信息侦察员 / 筛选师 / 分析师 / 编辑师 / 审计师 | Scout / Screener / Analyst / Editor / Auditor | Specialist roles for workflow stages |

## Roadmap Summary

- **v0.7**: Improvement Ledger — freeze human-authored, human-approved reader preferences into per-run Improvement Memory snapshots; no automatic learning, FrictionStore auto-detection, or output-quality guarantee.
- **v0.8**: measurement, fast-rerun, role topology, and evaluation — timing projection, same-evidence reruns, default/strict topology choices, and controlled experiment tooling without weakening accountable artifacts.
- **v0.9**: support-sufficiency core. Current path: Atomic Claim Graph -> Evidence Span Registry -> Claim-Support Matrix -> Semantic Assessment Report proposal surface. Human adjudication, coverage/omission gates, semantic regression, release eligibility, quality packs, and finding-to-repair workflows are deferred semantic-governance surfaces, not the next default implementation track.
- **v0.10**: Product OS and report packs — ReportSpec, ReportPack registry, zero-config workspaces, template rendering, delivery/audit bundle projection, `evidence_extract`, SourceHub Lite, release modes, and human approval records without weakening the accountability spine.
- **v1.0**: stable weekly/monthly/evidence-extract CLI product, frozen report contracts, compatibility policy, and threat model.

See the full [roadmap](docs/roadmap.md). For implemented vs planned capability, see [architecture status](docs/architecture-status.md).

## Documentation Index

[Function Map](docs/features.md) ·
[Architecture](docs/architecture.md) ·
[Technical reference v0.1.2](docs/mabw-architecture-reference-v0.1.2.md) ·
[Orchestrator contracts](docs/orchestrator-contracts.md) ·
[Quality gates](docs/harness.md) ·
[Evaluation cases](docs/evaluation-cases.md) ·
[Improvement Ledger](docs/modules/improvement.md) ·
[Evidence Span Registry](docs/evidence-span-registry.md) ·
[Claim-Support Matrix](docs/claim-support-matrix.md) ·
[Public integration summary](docs/reference-runs/v0.7.2-public-solar-integration.md) ·
[Failure study](docs/reference-runs/v0.7.4-organoid-failure-study.md) ·
[v0.10.1](docs/releases/v0.10.1.md) ·
[v0.9.4](docs/releases/v0.9.4.md) ·
[MABW-080 experiment guide](docs/experiments-080.md) ·
[Support matrix](docs/support-matrix.md) ·
[Security](docs/security.md) ·
[Migration guide](docs/MIGRATION.md)

## License

MIT
