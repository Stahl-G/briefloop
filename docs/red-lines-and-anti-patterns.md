# Red Lines And Anti-Patterns

This page records public-safe rationale for v0.6 control-surface regression checks.

## Red Lines

- Do not turn `multi-agent-brief run` into a Python brief generator.
- Do not let Python auto-repair prose or claim the repair is semantically complete.
- Do not let `finalize` stand in for quality gates.
- Do not treat feedback text as claim evidence.
- Do not treat `FeedbackIssue` as guidance: guidance must be human-authored and human-approved, with no automatic path from issue or gate finding to materialized audience memory.
- Do not tell users "the system learned this" unless the user approved it and it is inspectable in the Improvement Ledger.
- Do not let future-stage artifacts or issues globally block a fresh workspace.
- Do not let planned blocking issues be bypassed by `continue`.
- Do not require reader-facing briefs to retain internal `[src:CLAIM_ID]` markers.
- Do not fetch live market data inside deterministic gate checks.
- Do not present provenance projection as semantic proof that a source supports a claim.
- Do not present a precision gate as sufficient by itself when it can be passed by omitting important information.
- Do not present coverage checks or Screener output as full-recall proof.
- Do not execute arbitrary shell strings from evaluation fixtures.
- Do not ship private prompts, internal paths, real URLs, tokens, or commercial benchmark cases as public fixtures.

## Anti-Patterns

| Anti-pattern | Why it is unsafe |
|---|---|
| Full Python pipeline resurrection | Breaks the subagent-first runtime boundary. |
| Global required-artifact blocking | Makes fresh workspaces look blocked by downstream artifacts that are not due yet. |
| Repair plan equals repair completion | Lets the Orchestrator skip unresolved feedback. |
| Gate findings auto-create feedback issues | Hides the Orchestrator decision that should route repair or human review. |
| Precision-only quality gate | Can reward omission: unsupported claims disappear, but important missing topics are not surfaced. Pair with coverage-side checks when the risk is omission. |
| Screener as recall proof | Screener ranks and caps candidates; it does not prove the source universe contains no other material item. |
| Final brief as auditable source surface | Reader-facing output should not need internal claim markers; use the delivery source appendix and `output/source_appendix.md` audit copy for source lists. |
| Provenance graph as truth graph | The projection records citation/control relationships; semantic support still needs audit or human review. |
| LLM-as-judge default eval | Turns deterministic regression checks into model-dependent scoring. |
| Shell-string eval commands | Expands the fixture runner into an arbitrary command executor. |

These red lines are enforced through focused tests, packaged public-safe evaluation cases, and the support matrix.

中文红线：`FeedbackIssue` 只是证据，不是读者偏好。Guidance 必须由人撰写、由人批准；从 issue 或 gate finding 到可物化 audience memory 不存在自动路径。不要告诉用户“系统学会了这一点”，除非这条偏好已经由用户批准，并且能在 Improvement Ledger 里查看和撤销。
