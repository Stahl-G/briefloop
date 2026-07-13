# Product Decision and Handoff Templates

Use only the template needed for the current request.

## Product Decision Card

```markdown
# Product Decision

**Decision:**
DO NOW / DO BEFORE PILOT / DO AFTER V1 / EXPERIMENT / REJECT / NEED EVIDENCE

**User and job:**
Who is completing what task.

**Problem:**
The current pain, consequence, and situation.

**Evidence:**
Observed evidence and what remains an assumption.

**Why now / why not now:**
Why this belongs in the current version or later.

**Product invariant affected:**
The durable product rule at stake, or `none`.

**MVP:**
The smallest useful product slice.

**Non-goals:**
What this decision explicitly excludes.

**Authority boundary:**
Agent proposal, deterministic authority, human decision, and the sole truth.

**Acceptance criteria:**
Observable Given / When / Then results.

**Metrics:**
Three to five measures at most.

**Risks:**
Product, compatibility, misleading-claim, failure, and complexity risks.

**Next handoff:**
Who does what next, with what stop condition.
```

## Engineering or Review Handoff

```text
Problem:
Product invariant:
Decision and version:
Scope:
Non-goals:
Current authority:
Required behavior:
Forbidden behavior:
Compatibility and migration:
Acceptance matrix:
Success evidence:
Public claim boundary:
Next owner:
Stop condition:
```

## PRD Coverage

When the user requests a PRD, cover: background and user problem; users and
scenarios; current workflow; goals; non-goals; product solution; user flow;
agent, deterministic, and human boundaries; authoritative records and
artifacts; failure and recovery; compatibility and migration; metrics;
acceptance criteria; Pilot design; release and public-claim boundaries; and
open questions.

Do not prescribe code structure unless the choice directly affects user
experience, safety, auditability, cost, compatibility, or release capability.
