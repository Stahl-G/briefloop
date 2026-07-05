# v1.0 Pilot Evidence Gate

Status: not_satisfied

This document is the v1.0 pilot evidence record. It is a release-readiness
checklist, not a capability claim. It records whether BriefLoop has public-safe
first-user evidence that a non-maintainer can inspect, try, or follow before
the v1.0 release is called ready.

Current status: no qualifying v1.0 pilot evidence is recorded yet.

## Required Evidence Types

Before v1.0 release, record at least one public-safe evidence item from this
set:

- external fresh-clone smoke
- WorkBuddy Skill first-user smoke
- pilot user checklist
- recurring weekly-loop dogfood

The evidence may be synthetic or public-safe dogfood, but it must describe the
actual path a first user would follow. Private customer, employer, investor,
legal, disclosure, or confidential business facts must not appear here.

## Required Record Fields

Each real evidence record must capture:

- Evidence type
- Date
- Runner
- Environment
- Artifact or log path
- what succeeded
- where the user got confused
- what failed
- what was fixed
- what remains known limitation
- Boundary statement

`Artifact or log path` must be one of:

- an existing repo-relative or local file path;
- an `https://` URL;
- an `external:` reference with an `External verification note` field explaining
  why the release evidence cannot be checked from this repository.

## Boundaries

This evidence gate is:

- traceability, not semantic proof
- measurement infrastructure, not a benchmark claim
- not output-quality proof
- not delivery approval
- not release authority
- not legal, compliance, investment, disclosure, or publication approval

Passing this evidence gate means the release operator recorded bounded
first-user evidence. It does not prove factual correctness, writing quality,
market readiness, user adoption, or safety for regulated use.

## Recorded Evidence

No qualifying v1.0 pilot evidence is recorded yet.

## Evidence Template

Copy this template into `Recorded Evidence` only after the evidence actually
exists. Do not pre-fill it with plans or intended checks.

```text
### Evidence Record: <short public-safe name>

- Evidence type: <external fresh-clone smoke | WorkBuddy Skill first-user smoke | pilot user checklist | recurring weekly-loop dogfood>
- Date: <YYYY-MM-DD>
- Runner: <person or role>
- Environment: <source clone, packaged install, WorkBuddy, etc.>
- Artifact or log path: <public-safe path or link>
- What succeeded: <concrete observed result>
- Where the user got confused: <concrete observation, or "none observed">
- What failed: <concrete failure, or "none observed">
- What was fixed: <fix or "not fixed in this release">
- What remains known limitation: <bounded limitation>
- Boundary statement: traceability, not semantic proof; not output-quality proof; not delivery approval; not release authority.
- External verification note: <required only for external: artifact references>
```
