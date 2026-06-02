# Architecture

## Core Workflow

```text
Data Connectors
  -> Scout
  -> Claim Ledger
  -> Analyst
  -> Auditor
  -> Editor
  -> Formatter
  -> Output Artifacts
```

## Agent Responsibilities

### Scout

Scout loads sources, extracts candidate reportable items, and turns them into claims. Scout does not write final analysis.

### Claim Ledger

The Claim Ledger is the central control point. Every material fact, number, date, risk, or interpretation that appears in the brief should be traceable to a claim.

### Analyst

Analyst drafts the brief using only Claim Ledger claims. In the MVP, this is deterministic. Future model-backed analysts must keep source references.

### Auditor

Auditor checks references and source support. The MVP includes deterministic audit. A future semantic audit adapter can compare the draft against source evidence.

### Editor

Editor improves structure and readability. Editor must not invent new facts or unsupported numbers.

### Formatter

Formatter writes output files. It should not change the substance of the brief.

## Migration Tracks

The following existing private workflow capabilities should be migrated as clean-room modules:

- DOCX output
- PDF output
- Feishu delivery
- Slack delivery
- Email delivery
- SEC filing connector
- RSS connector
- API connector

Each migration should include:

- A public interface
- Synthetic or public sample data
- No credentials
- Tests
- Documentation

