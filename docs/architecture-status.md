# Architecture Status

This page separates current implementation state from roadmap goals. Use it before planning roadmap-driven changes.

## Implemented Public Baseline

- The standard user path is subagent-first.
- `multi-agent-brief run` creates runtime handoff artifacts rather than generating a full brief by itself.
- Runtime handoff now initializes minimum runtime state and artifact registry control files.
- Feedback issues and bounded repair plans can be structured, validated, and recorded without executing repair.
- Python commands provide setup, source tooling, validation, audit support, and rendering.
- Hermes, Claude Code, Codex, OpenCode, and manual fallback are treated as agent runtime surfaces.
- Input governance separates evidence from feedback, instructions, and background context.
- Old Python-pipeline framing is deprecated for the standard workflow.

## Roadmap Goals

The roadmap mentions concepts that are not necessarily implemented yet. Treat these as goals unless the code, tests, and support matrix show otherwise:

- Orchestrator contracts
- evidence and execution provenance
- quality evaluation and feedback loops
- policy packs
- public-safe reference workflows

## Experimental Or Limited Surfaces

Features marked experimental, interface-only, or CLI-only should not be treated as stable user promises. Check the support matrix and CLI output before relying on them.

## Contributor Rule

Roadmap direction is not proof of implementation. When implementing a roadmap item, first identify the current code path, the owning validator or test, and whether the capability is public, experimental, or internal planning only.
