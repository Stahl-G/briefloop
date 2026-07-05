# BriefLoop-090 A-Controlled Auditable-Brief Pilot

This public reference note records one public-safe synthetic experiment case run
through the shipped MABW-080 experiment harness. It is archived experimental
evidence for the v1.0 product boundary, not a current first-user path.

It is a content-level `auditable_brief` observation, not a delivery-quality
claim and not a general proof that Improvement Memory improves output quality.

## Scope

- Case type: public-safe synthetic case.
- Assessment target: `auditable_brief`.
- Assessment surface: `output/intermediate/audited_brief.md`.
- Conditions: `baseline`, `memory`, `prompt_only`.
- Assessment mode: condition-blind and hash-bound.
- Formal validity: three A-controlled arms in this case.
- ZIP SHA-256, grouped for public-safety scanning; remove spaces before
  verification:
  `fb26 f033 5c78 607d b526 276f fb16 3126 90f3 a448 0ee2 44ab 3ca7 b8f2 be08 9ac0`.

The `auditable_brief` target stops at the frozen audited brief, audit report,
auditor gate report, same frozen fact layer, treatment-isolation checks, and
imported assessment. It does not claim reader-clean delivery, management-ready
output, DOCX/PDF quality, or finalize-transform correctness.

## Observation

In this public-safe synthetic case, the memory condition showed the approved
guidance without obvious harm, while prompt-only over-applied the same
guidance.

| Condition | Manifestation score | Overapplication |
|---|---:|---|
| `baseline` | 1 | No |
| `memory` | 2 | No |
| `prompt_only` | 3 | Yes |

Interpretation:

- `baseline`: weak manifestation without overapplication.
- `memory`: clean manifestation without obvious harm.
- `prompt_only`: overapplication of the same guidance.

## Claim Boundary

This reference supports only a narrow statement:

> In this public-safe synthetic case, under a same-frozen-fact-layer,
> condition-blind, hash-bound `auditable_brief` assessment, the intended
> guidance pattern was observed: baseline weak, memory clean, prompt-only
> over-applied.

Do not use this note to claim:

- Improvement Memory improves output quality in general.
- BriefLoop proves factual correctness.
- Prompt-only is worse in general.
- The output is management-ready.
- DOCX/PDF delivery quality was validated.
- The result generalizes beyond one synthetic case.

## Relationship To Current Tooling

The shipped CLI namespace remains `briefloop experiments 080` for archived
reproduction and audit of this measurement surface. BriefLoop-090 is an
archived experiment label, not a semver release number, normal product workflow,
or separate CLI namespace.
