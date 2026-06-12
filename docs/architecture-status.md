# Architecture Status

This page separates current implementation state from roadmap goals. Use it before planning roadmap-driven changes.

## Implemented Public Baseline

- The standard user path is subagent-first.
- `multi-agent-brief run` creates runtime handoff artifacts rather than generating a full brief by itself.
- Runtime handoff now initializes minimum runtime state and artifact registry control files.
- Feedback issues and bounded repair plans can be structured, validated, and recorded without executing repair.
- Deterministic material-fact, freshness, and target-relevance gates can write a quality gate report without fetching sources or rewriting briefs.
- Packaged public-safe evaluation cases can validate known gates, feedback, runtime blocker, and Hermes path regressions for development and CI.
- Optional deterministic provenance projection can write a workspace-local audit/debug graph from existing control files.
- Workspace-local audience taste profiles can be frozen into per-run snapshots and exposed through runtime handoff as context.
- The Orchestrator control switchboard can surface deterministic control recommendations and record enable/defer/reject selections without executing those controls.
- Finalize writes the reader delivery bundle under `output/delivery/`, appending the source appendix to delivery Markdown/DOCX while retaining `output/source_appendix.md` as an audit/control copy. Delivery artifacts must not expose internal claim IDs, source IDs, evidence text, local paths, or file URLs.
- Runtime asset availability is now explicit: packaged installs include contract configs and public-safe eval fixtures, while source runtime assets such as `.agents/`, `.claude/`, `.opencode/`, `.codex/`, and Hermes plugin files are source-clone-only unless copied into a workspace with `multi-agent-brief runtime install`.
- The Improvement Ledger lifecycle can preserve human-authored, human-approved reader guidance in `improvement/ledger.jsonl`, project approved materializable entries into `improvement/memory.md`, freeze per-run `output/intermediate/improvement_memory_snapshot.md`, and expose only the frozen snapshot through handoff.
- Packaged public-safe evaluation cases now cover Improvement Memory control behavior: unapproved entries are not materialized, approved guidance is frozen, and reverted entries disappear from the next snapshot.
- Python commands provide setup, source tooling, validation, audit support, and rendering.
- Hermes, Claude Code, Codex, OpenCode, and manual fallback are treated as agent runtime surfaces.
- Input governance can extract supported non-text input documents to Markdown with MinerU, then separates evidence from feedback, instructions, and background context.
- Old Python-pipeline framing is deprecated for the standard workflow.

## Roadmap Goals

The roadmap mentions concepts that are not necessarily implemented yet. Treat these as goals unless the code, tests, and support matrix show otherwise:

- Orchestrator contracts
- semantic evidence support verification
- quality evaluation and feedback loops
- private or commercial benchmark suites
- policy packs
- public-safe reference workflows
- FrictionStore, autonomous learning, retrieval memory, runtime-specific guidance filtering, and output-quality validation

## Experimental Or Limited Surfaces

Features marked experimental, interface-only, or CLI-only should not be treated as stable user promises. Check the support matrix and CLI output before relying on them.

## Contributor Rule

Roadmap direction is not proof of implementation. When implementing a roadmap item, first identify the current code path, the owning validator or test, and whether the capability is public, experimental, or internal planning only.
