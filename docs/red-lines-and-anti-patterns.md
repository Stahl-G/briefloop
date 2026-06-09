# Red Lines And Anti-Patterns

This page records public-safe rationale for v0.6 control-surface regression checks.

## Red Lines

- Do not turn `multi-agent-brief run` into a Python brief generator.
- Do not let Python auto-repair prose or claim the repair is semantically complete.
- Do not let `finalize` stand in for quality gates.
- Do not treat feedback text as claim evidence.
- Do not let future-stage artifacts or issues globally block a fresh workspace.
- Do not let planned blocking issues be bypassed by `continue`.
- Do not require reader-facing briefs to retain internal `[src:CLAIM_ID]` markers.
- Do not fetch live market data inside deterministic gate checks.
- Do not present provenance projection as semantic proof that a source supports a claim.
- Do not execute arbitrary shell strings from evaluation fixtures.
- Do not ship private prompts, internal paths, real URLs, tokens, or commercial benchmark cases as public fixtures.

## Anti-Patterns

| Anti-pattern | Why it is unsafe |
|---|---|
| Full Python pipeline resurrection | Breaks the subagent-first runtime boundary. |
| Global required-artifact blocking | Makes fresh workspaces look blocked by downstream artifacts that are not due yet. |
| Repair plan equals repair completion | Lets the Orchestrator skip unresolved feedback. |
| Gate findings auto-create feedback issues | Hides the Orchestrator decision that should route repair or human review. |
| Final brief as auditable source surface | Reader-facing output should not need internal claim markers; use `source_appendix.md` for reader-facing source lists. |
| Provenance graph as truth graph | The projection records citation/control relationships; semantic support still needs audit or human review. |
| LLM-as-judge default eval | Turns deterministic regression checks into model-dependent scoring. |
| Shell-string eval commands | Expands the fixture runner into an arbitrary command executor. |

These red lines are enforced through focused tests, packaged public-safe evaluation cases, and the support matrix.
