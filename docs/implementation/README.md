# Implementation Overviews

This directory contains public-safe implementation overviews for roadmap milestones.

These pages are not private engineering plans. They describe the intended public module boundaries, sequencing, acceptance criteria, and non-goals so contributors can understand what a milestone means before code lands.

Detailed schema drafts, private evaluation examples, prompt notes, failure taxonomies, and commercial scenario design stay out of the public repository until they are implemented and safe to publish.

## Current Pages

- [v0.5.9 Orchestrator Contract Preparation](v0.5.9-orchestrator-prep.md)
- [v0.6.0 Explicit Orchestrator Contract](v0.6.0-explicit-orchestrator-contract.md)

## How To Use

1. Read [MABW Agent 开发指南](../agent-dev-guide.zh-CN.md) for the repository development model and architecture invariants.
2. Use [MABW 开发 Agent Prompt](../agent-dev-prompt.zh-CN.md) when assigning a coding agent or PR-fix agent to work in this repository.
3. Read [Architecture Status](../architecture-status.md). Roadmap goals are not proof that code exists.
4. Read [Migration Notes](../MIGRATION.md) before changing legacy Python-pipeline wording.
5. Read [Orchestrator Contract Model](../orchestrator-contracts.md) before changing runtime handoff, agent role, or workflow-control language.
6. Read [Orchestrator Architecture](../orchestrator-architecture.md) for the v0.6 main-agent control model.
7. Use these implementation overviews to keep changes narrow and staged.

Each implementation slice should answer:

- What public behavior or contract boundary is being clarified?
- Which runtime entry or generated adapter reads the new contract?
- Which validator, smoke check, or regression test prevents rollback?
- What is explicitly left for a later milestone?
