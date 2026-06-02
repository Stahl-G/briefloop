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
- Keep porting public-safe quality harness checks from the private workflow

### Epic 1B: Harness Migration

Goal: Preserve mature private-pipeline quality gates as clean-room, configurable harness modules.

Issues:

- Add configurable rule packs
- Add previous-report baseline ingestion
- Add source-tier policy by audience/module
- Add publication-mode final-clean checks
- Add editor-fixable vs analyst-blocking failure classification
- Add section taxonomy and required-section checks
- Add model-backed semantic source-support audit

### Epic 2: Output Migration

Goal: Generalize already implemented private output capabilities.

Issues:

- Add DOCX output implementation with synthetic examples
- Add PDF output implementation with synthetic examples
- Wire Feishu delivery implementation, disabled by default
- Wire Slack delivery implementation, disabled by default
- Wire Email delivery implementation, disabled by default
- Add tests for interface-only connectors staying disabled without credentials

### Epic 3: Data Connector Migration

Goal: Generalize already implemented private connector capabilities.

Issues:

- Implement SEC filing connector behind the existing interface
- Implement RSS connector behind the existing interface
- Implement generic authenticated API connector behind the existing interface
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
