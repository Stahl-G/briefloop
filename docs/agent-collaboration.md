# Agent Collaboration Design

This repository contains an internal brief-production pipeline, but it can also be maintained or operated by external coding and research agents such as Codex, Claude Code subagents, OpenCode, or other local model runners.

The collaboration design is intentionally tool-agnostic. Tool-specific agents should map onto the same contracts rather than bypassing the Claim Ledger or audit gates.

## Roles

| External agent role | Typical tool | Responsibility | Must not do |
| --- | --- | --- | --- |
| Maintainer | Codex | Edit code, run tests, update docs, manage Git. | Invent internal company facts or skip smoke tests after script changes. |
| Research Scout | Claude Code subagent or OpenCode agent | Gather public or synthetic source packets. | Write final claims directly into the brief without ledger entries. |
| Source Auditor | Claude Code subagent, local model, or deterministic script | Check source support, freshness, and redaction risks. | Weaken audit gates to make weak output pass. |
| Semantic Auditor | Claude/OpenAI/LiteLLM/local model | Compare draft statements against evidence text. | Treat model judgment as source evidence. |
| Editor Reviewer | Claude Code subagent or Codex | Improve readability and remove internal residue. | Add unsupported facts, numbers, or recommendations. |
| Formatter Reviewer | Codex or document-specific subagent | Validate Markdown/DOCX/PDF rendering fidelity. | Hide rendering defects by changing substantive content. |

## Handoff Contract

All external agents should exchange structured artifacts:

```text
SourcePacket
  -> CandidateItem
  -> Claim Ledger entry
  -> Draft section with [src:CLAIM_ID]
  -> Audit Report finding
  -> Final brief artifact
```

The anti-pattern is:

```text
Research/RAG/Subagent output
  -> Analyst prose
  -> Final brief
```

Every material statement must enter the Claim Ledger before it can be cited in the final brief.

## Suggested Claude Code Subagents

These are public-safe role specs, not private prompt text. All configurations are generated from `configs/agent_roles.yaml`:

- `scout`: extracts candidate reportable items from local sources.
- `screener`: filters, ranks, deduplicates, and capacity-caps Scout candidates.
- `claim-ledger`: converts screened candidates into source-grounded claim entries.
- `analyst`: drafts brief sections using only Claim Ledger entries.
- `auditor`: checks source support, freshness, redaction risk, and harness failures.
- `editor`: improves clarity and structure without adding facts.
- `formatter`: writes and validates final output artifacts.
- `draft-audit-harness`: reviews deterministic audit and quality harness checks.
- `final-quality-harness`: reviews final text delivery gates.
- `rendered-output-harness`: reviews DOCX/PDF rendering fidelity.
- `orchestrator`: coordinates cross-role integration and pipeline changes.

Each subagent should be constrained to its role and should write machine-readable findings where possible.

## Codex Collaboration Pattern

Codex is best used as the local execution and integration agent:

- Read repo context and docs.
- Implement narrow changes.
- Run unit tests and smoke tests.
- Update documentation.
- Preserve public-safe boundaries.
- Commit or push only when explicitly requested.

When Codex coordinates model-backed subagents, it should treat their output as input to the pipeline, not as final truth.

## Delivery Gate

Production-style delivery should follow:

```text
draft facts pass
  -> final text quality pass
  -> rendered document quality pass
  -> delivery allowed
```

Use `DeterministicAuditAgent` and `QualityHarnessAuditAgent` for draft checks. Use `FinalQualityAuditAgent` for final delivery checks when a workflow is ready to enforce strict text and rendered-output quality.

## Safety

Do not store credentials, tokens, webhooks, private paths, raw logs, formal internal deliverables, or personal data in agent prompts, generated configs, or examples. Public examples should use synthetic or public-safe data only.
