# Industry Weekly Demo Reference Workspace

This is a public-safe, compressed BriefLoop reference package for first-time
readers. It can be inspected without API keys, private sources, or a runtime
agent.

BriefLoop helps you produce briefing packages that can be questioned, reviewed,
repaired, and handed off. It provides traceability and process accountability,
not semantic proof.

## What This Shows

This example shows the artifact chain behind a short synthetic industry weekly:

```text
synthetic public-safe source excerpts
-> registered claims
-> deterministic checks
-> source appendix
-> reader-facing final brief
```

Open these files first:

- [artifacts/final_brief.md](artifacts/final_brief.md) - the reader-facing
  delivery artifact.
- [artifacts/claim_ledger.json](artifacts/claim_ledger.json) - the claims and
  source excerpts behind the brief.
- [artifacts/quality_summary.md](artifacts/quality_summary.md) - the human
  review summary of deterministic checks.
- [artifacts/source_appendix.md](artifacts/source_appendix.md) - the reader-safe
  source appendix.
- [artifacts/quality_gate_report.json](artifacts/quality_gate_report.json) -
  machine-readable gate findings.
- [artifacts/event_log_excerpt.jsonl](artifacts/event_log_excerpt.jsonl) - a
  short event trace showing where control actions would be recorded.

## What Went In

The demo uses three synthetic source excerpts:

| Source | Example content |
|---|---|
| `SRC-001` | Northstar Grid Components reported a monthly order-intake increase. |
| `SRC-002` | Public grid-modernization tender notices emphasized transformer and switchgear lead times. |
| `SRC-003` | A regional storage integrator disclosed delays tied to battery-container inspections. |

These are not private facts, live market data, or benchmark inputs. They are
small public-safe fixtures designed to show how a BriefLoop package is
inspectable.

## What Came Out

The final brief has three reader-facing sections:

1. weekly signal;
2. why it matters;
3. watch items for the next run.

The Claim Ledger links each material sentence back to a source excerpt. The
Quality Summary shows one warning that a reader should review manually. The
Source Appendix keeps source labels reader-safe and avoids internal claim IDs in
the final brief.

## Boundaries

This package does not prove that the final brief is correct, complete, or better
than another writing process. It does not demonstrate automatic truth checking,
source-support sufficiency, delivery approval, or publication readiness.

It demonstrates the process surface: where claims are recorded, where checks
appear, and where a reviewer can start asking questions.
