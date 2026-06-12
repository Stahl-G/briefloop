# Multi-Agent-Brief-Workflow

<p align="center">
  <a href="README_en.md">English</a> |
  <a href="README.md">简体中文</a>
</p>

A source-grounded, auditable, agent-collaborative briefing workflow for business, research, market, policy, company-tracking, and management-reporting briefs.

> MABW can observe and suggest, but only what you approve is remembered, and every approved memory stays in an inspectable ledger you can undo.

`Multi-Agent-Brief-Workflow` (MABW) is not an "AI writes a weekly report" prompt. It breaks real briefing work into contract-governed steps: understand the task, discover sources, organize inputs, build a Claim Ledger, assist drafting, audit the result, and render delivery files. Each step has explicit expected artifacts, producer boundaries, transition rules, and inspectable records.

This project is not an investment advice tool, trading signal generator, or replacement for human review.

## Current Status

Current version: **v0.7.0**

- **Working today**: subagent-first workflows across Hermes / Claude Code / Codex / OpenCode, runtime state files, Claim Ledger, deterministic quality gates, feedback and repair planning, provenance projection, audience profile snapshots, controlled Improvement Ledger / Improvement Memory, and Markdown / Word output. 1000+ deterministic tests run in CI without LLM calls.
- **New in v0.7.0**: human-authored, human-approved reader preferences can be recorded in `improvement/ledger.jsonl`, frozen on the next `run` / `start` / `handoff` as `output/intermediate/improvement_memory_snapshot.md`, and exposed through runtime handoff.
- **Not yet**: not an autonomous agent, does not automatically edit brief content, does not automatically learn, and does not provide a long-term memory system. See [architecture status](docs/architecture-status.md) and [roadmap](docs/roadmap.md).

One-line design principle: **the system proposes; humans decide.** For the hard boundaries, see [docs/red-lines-and-anti-patterns.md](docs/red-lines-and-anti-patterns.md).

## The Four Things It Tracks Each Week

The writer-facing model is not "how many control surfaces exist." Each run is meant to keep four practical things visible:

| Question | What it records | Where you see it |
|---|---|---|
| What stage this run is in | Current stage, missing artifacts, blockers, and the next safe action | `/mabw status`, `workflow_state.json`, `agent_handoff.md` |
| Where each number came from | Claim Ledger records, source dates, audit results, and gate findings | `claim_ledger.json`, `quality_gate_report.json`, `source_appendix.md` |
| What reader preferences were approved | Human-approved reader guidance only; unapproved suggestions do not take effect | `improvement/ledger.jsonl`, `improvement_memory_snapshot.md` |
| What checks are guarding delivery | Completion transactions, reader-final gate, source appendix, and delivery checks | `finalize_report.json`, `reader_clean`, `state finalize-complete` |

In plain terms: AI can draft; the system records ledgers; only the operator can make preferences affect later runs. See [docs/what-mabw-keeps-track-of.md](docs/what-mabw-keeps-track-of.md) for the user-facing explanation.

## Why This Exists

In corporate strategy teams, sell-side research, buy-side research, investor relations, management offices, and similar environments, people spend a large amount of time producing daily reports, weekly reports, morning-meeting notes, and leadership briefs. The work matters, but the process is repetitive: find sources, decide what matters, remove stale or duplicate information, write a coherent brief, verify numbers, check whether AI invented anything, edit wording, and format the output.

The deeper problem is that this kind of work does not reliably improve. A junior analyst gets corrected verbally, the correction disappears, and the next person repeats the same mistake. A "this section feels wrong" comment evaporates after the meeting. A stale number enters a brief, and no one can tell where it slipped through.

Software engineering improves through tests, Git history, CI, and review. This project brings the same basic machinery into real briefing work: auditability, traceability, structured feedback, and human approval. The goal is to spend more time on judgment, questions, and decision support, and less time on repetitive copying and formatting.

## What It Solves

The common failure mode of AI-generated reports is not speed. It is control:

- A sentence appears, but its source is unclear.
- Numbers and dates lose attribution.
- Citation relationships break after several editing rounds.
- Too many sources introduce duplicates, stale items, and low-quality material.
- Long prompts make models skip steps.
- The final document looks complete but cannot be audited.

The answer is a contract-governed workflow:

```text
User need → Source discovery → Source governance → Claim Ledger → Agent-assisted drafting → Audit and gates → Markdown / Word output
```

Each stage is handled by a specialist role: Scout, Screener, Claim Ledger, Analyst, Editor, and Auditor. An Orchestrator coordinates stage transitions, validation, and decisions. State is written to inspectable workspace files. For details, see [docs/architecture.md](docs/architecture.md). For the full technical reference, see [docs/mabw-architecture-reference-v0.1.2.md](docs/mabw-architecture-reference-v0.1.2.md).

## What Output Looks Like

The final delivery is a clean `brief.md` / `brief.docx`, but the real difference is in the intermediate artifacts. The example below is **synthetic** and only demonstrates structure. A full reference run is planned for v0.7.1.

`output/brief.md` excerpt:

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

`output/intermediate/quality_gate_report.json` excerpt:

```json
{
  "gate_id": "freshness",
  "status": "pass",
  "findings": []
}
```

The point is simple: every important number in the delivered brief should have a registered source and date in the Claim Ledger. Stale sources and unsupported numbers should be stopped by audit gates instead of silently entering the final document. The execution trace is recorded in `event_log.jsonl`.

## Quick Start

### Claude Code (Five-Verb Primary Path)

```bash
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
bash scripts/setup.sh
source .venv/bin/activate

multi-agent-brief claude install --repo-workdir .
```

Then use the five writer verbs inside Claude Code CLI or the Claude Desktop Code
tab:

```text
/mabw new
/mabw run <workspace>
/mabw status <workspace>
/mabw feedback <workspace> [text-or-file]
/mabw deliver <workspace>
```

`/mabw` is the writer-facing entrypoint. The full delegated subagent workflow
still runs through `/generate-brief <workspace>`. `status` calls the read-only
`multi-agent-brief status` helper, `feedback` records and triages without acting downstream, and `deliver` must go
through gates, the reader-final gate, and `state finalize-complete`.

See [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) for the full Claude Code path. Chinese writer-facing operator notes are available in [docs/golden-path.zh-CN.md](docs/golden-path.zh-CN.md) and [docs/weekly-use.zh-CN.md](docs/weekly-use.zh-CN.md).

### Other Runtimes

```bash
multi-agent-brief onboard
multi-agent-brief init ../mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace ../mabw-workspace --runtime claude
```

Hermes, OpenCode, Codex, and manual fallback keep their existing entrypoints.
The five-verb product entrypoint first ships on Claude Code only, to avoid a
false cross-runtime parity contract.

The Hermes plugin remains available for the native `delegate_task` path:

```bash
multi-agent-brief hermes install-plugin
hermes plugins enable mabw
```

Runtime installation details, workspace-local runtime kits, and common issues are covered in [docs/claude-code-quickstart.md](docs/claude-code-quickstart.md) and [docs/runtime-recipes.md](docs/runtime-recipes.md).

### Use Your Own Materials / Optional Capabilities

- Local input handling and onboarding: [docs/onboarding.md](docs/onboarding.md)
- Web search backends such as Tavily: [docs/search-backends.md](docs/search-backends.md)
- Source discovery candidate merge, including the `llm_decide` source profile: `multi-agent-brief sources decide --config <workspace>/config.yaml --merge`
- Feishu integration for collection and delivery: [docs/feishu-integration.md](docs/feishu-integration.md)
- SEC filing parsing: [docs/opencli-source-provider.md](docs/opencli-source-provider.md)
- Windows PowerShell: [docs/windows-powershell.md](docs/windows-powershell.md)

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
- **Contributors**: start from a [good first issue](https://github.com/Stahl-G/multi-agent-brief-workflow/issues). Before submitting changes, read [red lines and anti-patterns](docs/red-lines-and-anti-patterns.md).

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
- **v0.8**: evaluation experiments and policy packs — define guidance manifestation / regression evaluation, compare against single-model baselines, and advance the mode registry plus a second policy pack.
- **v0.9**: distribution and reference workflows — easier no-API-key setup, reference runs, and documentation cleanup.
- **v1.0**: stable baseline — frozen schemas, stable CLI surfaces, security threat model, and clear support boundaries.

See the full [roadmap](docs/roadmap.md). For implemented vs planned capability, see [architecture status](docs/architecture-status.md).

## Documentation Index

[Architecture](docs/architecture.md) ·
[Technical reference v0.1.2](docs/mabw-architecture-reference-v0.1.2.md) ·
[Orchestrator contracts](docs/orchestrator-contracts.md) ·
[Quality gates](docs/harness.md) ·
[Evaluation cases](docs/evaluation-cases.md) ·
[Improvement Ledger](docs/modules/improvement.md) ·
[Support matrix](docs/support-matrix.md) ·
[Security](docs/security.md) ·
[Migration guide](docs/MIGRATION.md)

## License

MIT
