# GitHub Project Plan

This file is a local blueprint for a GitHub Project board.

## Project Views

- Roadmap: grouped by epic
- Backlog: all open items
- Migration Track: existing private capabilities to generalize
- Safety Review: privacy, redaction, and disclaimer tasks

## Status Columns

- Backlog
- Ready
- In Progress
- Needs Review
- Done

## Epics

### Epic 1: MVP Pipeline

Goal: Keep the local pipeline small, testable, and runnable without API keys.

Issues:

- Define source, candidate, claim, audit, and report schemas
- Implement local source loader
- Implement Claim Ledger
- Implement deterministic analyst draft
- Implement deterministic audit
- Implement Markdown/source-map output
- Add basic tests

### Epic 2: Output Migration

Goal: Generalize already implemented private output capabilities.

Issues:

- Add DOCX output adapter with synthetic examples
- Add PDF output adapter with synthetic examples
- Add Feishu delivery interface, disabled by default
- Add Slack delivery interface, disabled by default
- Add Email delivery interface, disabled by default

### Epic 3: Data Connector Migration

Goal: Generalize already implemented private connector capabilities.

Issues:

- Add SEC filing connector
- Add RSS connector
- Add generic authenticated API connector
- Add connector-level source metadata and tests
- Add stale-source detection in audit

### Epic 4: Enterprise Internal Messages

Goal: Design a safe, opt-in internal message ingestion layer.

Issues:

- Define internal message connector interface
- Add allowlist and denylist config
- Add redaction and PII review gate
- Add read-only mode
- Add synthetic Slack/Feishu/Email examples

### Epic 5: Complex RAG

Goal: Add historical retrieval without letting RAG bypass the Claim Ledger.

Issues:

- Define retrieved chunk schema
- Add local corpus loader
- Add BM25 or keyword retrieval
- Add optional vector index adapter
- Require every RAG-derived statement to become a claim
- Add stale-context checks

### Epic 6: Database And Semantic Layer

Goal: Support structured data while avoiding hard-coded enterprise metric logic.

Issues:

- Define metric definition schema
- Define entity aliases
- Define peer groups
- Add DuckDB/Postgres adapter prototype
- Add metric source priority rules
- Add tests for metric provenance

### Epic 7: Automatic Investment Analysis Guardrails

Goal: Allow investment-style analysis only with clear disclaimers, source support, and non-advice boundaries.

Issues:

- Define investment-analysis output constraints
- Add disclaimer enforcement
- Add recommendation-language detector
- Add risk/caveat completeness checks
- Add evaluator for overclaiming

