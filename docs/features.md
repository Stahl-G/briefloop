# BriefLoop Function Map

BriefLoop has many surfaces because it is not a single report generator. It is
a briefing loop: intake, evidence, claims, writing, audit, delivery, feedback,
and evaluation. This page groups the current functions by what a user can do.

Status labels:

- **Always on**: part of the supported accountability spine.
- **Optional**: enabled by configuration, source setup, or runtime support.
- **Experimental**: implemented but not yet a stable v1.0 contract.
- **Roadmap**: planned or scoped, not an implemented capability.

Current baseline: **v0.9.3**.

## Start And Operate A Brief

| Function | What it does | Status | Entry points |
|---|---|---|---|
| Workspace onboarding | Collects brief purpose, audience, cadence, source mode, and output preferences before creating a workspace | Always on | `multi-agent-brief onboard`, `multi-agent-brief init --from-onboarding` |
| Claude writer command | Gives writers a five-verb interface for normal work | Optional, first-class writer path | `/mabw new`, `/mabw run`, `/mabw status`, `/mabw feedback`, `/mabw deliver` |
| Runtime handoff | Builds the runtime handoff for the external orchestrator and subagents | Always on | `multi-agent-brief run --workspace <workspace>` |
| Status view | Shows current stage, blockers, artifacts, timing buckets, and next safe actions | Always on | `/mabw status`, `multi-agent-brief status` |
| Delivery bundle | Produces reader-facing Markdown and DOCX outputs after finalize checks | Always on | `/mabw deliver`, `multi-agent-brief finalize`, `state finalize-complete` |

## Source And Input Collection

| Function | What it does | Status | Notes |
|---|---|---|---|
| Manual local inputs | Uses local files already placed in the workspace | Always on | Best first path for trying BriefLoop |
| Cached source packages | Reuses pre-downloaded public or private source packs | Optional | Useful for repeatable runs |
| Runtime web search | Lets the active agent runtime gather sources through its own search tool | Optional | No BriefLoop API key required |
| External search APIs | Uses configured search backends such as Tavily, Exa, Brave, Firecrawl, or Serper | Optional | Requires API keys |
| RSS and news feeds | Monitors configured feeds and news APIs | Optional | Useful for weekly tracking |
| SEC / filing tools | Pulls filings and resolves ticker/XBRL filing sources | Optional | Useful for company and investor-relations workflows |
| Feishu / Lark source integration | Pulls configured Feishu/Lark materials through local tooling | Optional | Requires local integration setup |
| MinerU parsing | Parses PDF/DOCX/PPTX/XLSX documents through MinerU | Optional | Premium parsing requires a token |
| MCP / CLI source providers | Lets configured MCP servers or CLI scripts contribute source candidates | Optional | Provider outputs are normalized before use |

## Evidence And Traceability

| Function | What it records | Status | Boundary |
|---|---|---|---|
| Claim Ledger | Key facts, source IDs, source dates, and claim metadata before writing | Always on | Traceability, not proof by itself |
| Source Appendix | Reader-safe source list for delivered briefs | Always on when configured | Raw trace details stay outside delivery |
| Artifact Registry | Expected artifacts, hashes, producers, and validation status | Always on | Python-owned control plane |
| Runtime Manifest | Per-run runtime state, hashes, snapshots, and compatibility metadata | Always on | Frozen per run |
| Event Log | Stage transitions, gate blocks, repairs, finalize events, and other trace entries | Always on | Append-only event trail |
| Run Archive | Stores completed run artifacts and summaries for later review | Always on after archive | Frozen reference surface |

## Gates, Repairs, And Delivery Safety

| Function | What it protects | Status | Boundary |
|---|---|---|---|
| Stage-complete transactions | Prevents a stage from advancing without required artifacts and recorded state | Always on | CLI transaction, not prompt memory |
| Quality gates | Freshness, material facts, target relevance, editor-new-fact checks, and related findings | Always on | Deterministic gates block when configured to block |
| Reader-final gate | Rejects reader-facing residue such as internal claim IDs, process wording, malformed source markers, and blank citation rows | Always on for final delivery | Reader surface only |
| Run integrity contamination | Marks frozen-artifact changes, replay hazards, and integrity violations explicitly | Always on | Contaminated runs are not clean reference evidence |
| Repair routing | Routes blockers and findings to scoped repair paths | Supported, still improving | Repair does not erase the original trace |

## Feedback And Approved Memory

| Function | What it does | Status | Boundary |
|---|---|---|---|
| Feedback capture | Records user feedback for review and repair/intake workflows | Supported | Feedback is not automatically memory |
| Improvement Ledger | Stores human-approved reader guidance in an append-only, auditable ledger | Always on when used | No autonomous learning |
| Improvement Memory snapshot | Freezes approved guidance into the next run's runtime surface | Always on when Improvement Ledger is used | Takes effect on future runs, not retroactively |
| Supersede / revert hygiene | Prevents obvious guidance rot and records reversibility | Supported | Human-controlled |

## Experimental Support-Record Surfaces

These v0.9.x surfaces are implemented as optional experimental control planes.
They improve traceability and support records, but they do not turn BriefLoop
into a truth-proof system.

| Function | What it adds | Status | Not a claim of |
|---|---|---|---|
| Atomic Claim Graph | Optional atom-level decomposition of Claim Ledger entries | Experimental | Automatic atomization correctness |
| Evidence Span Registry | Optional source-pack byte binding and span trace records | Experimental | Semantic support proof |
| Claim-Support Matrix | Optional atom-to-evidence support rows with validation and gate/status projection | Experimental | Automatic support assessment, truth proof, or release eligibility |
| Semantic support assessment proposals | Structured multi-assessor proposal layer for support labels | Roadmap | A single model judge deciding truth |
| Human adjudication queue | Human resolution of disputed support assessments | Roadmap | Automatic adjudication |
| Release eligibility | Explicit release/reference classification from support and evaluation records | Roadmap | Hidden quality claims |

## Evaluation And Dogfooding

| Function | What it does | Status | Boundary |
|---|---|---|---|
| Deterministic test suite | Runs 1,000+ tests without LLM calls | Always on in CI | Tests contracts and control behavior, not model quality |
| Synthetic demos | Shows the evidence chain on safe example materials | Supported | Demo behavior is not a production-quality claim |
| Reference run reports | Publishes selected public-safe integration and failure studies | Supported | Each report states what it proves and does not prove |
| MABW-080 / BriefLoop-090 experiments | Registers, scores, and summarizes controlled experiment runs | Experimental | Early evidence, not broad quality proof |

## Output Formats

| Function | Output | Status |
|---|---|---|
| Markdown delivery | `output/delivery/brief.md` | Always on |
| DOCX delivery | `output/delivery/<named>.docx` | Supported |
| Source appendix | Appended delivery source list plus audit copy when configured | Supported |
| PDF / advanced rendering | Available through renderer/tooling paths where configured | Optional |

## CLI Discovery Commands

Use these commands when you want the machine-readable feature catalog rather
than this product-facing map:

```bash
multi-agent-brief features
multi-agent-brief features --info <feature-id>
multi-agent-brief features --json
multi-agent-brief recommend --text "Track competitors and SEC filings"
multi-agent-brief setup <workspace>
multi-agent-brief doctor
```

## Not Functions

BriefLoop currently does **not** provide:

- autonomous report generation without human delivery;
- automatic long-term memory;
- automatic semantic support proof;
- release eligibility scoring from the Claim-Support Matrix;
- investment advice, trading signals, or legal advice;
- a guarantee that every linked source semantically supports every sub-claim.

The short version: BriefLoop can leave an auditable trail and block known
failure modes. It does not prove truth.
