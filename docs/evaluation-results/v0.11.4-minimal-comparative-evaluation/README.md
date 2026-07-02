# v0.11.4 Minimal Comparative Evaluation

This directory contains a public-safe minimal comparative evaluation packet for
the v0.11 product-baseline work. It compares a direct prompt / template
baseline (`C0`) with a BriefLoop-style workflow output (`C1`) across three
synthetic tasks.

This is evaluation evidence, not a workflow feature. It does not prove output
quality improvement, semantic truth, release readiness, or publication
authority.

## What Is Included

- `protocol.yaml`: preregistered task set, arms, scoring rubric, allowed
  source set, output constraints, raw-output hashes, and reporting boundaries.
- `raw_outputs/`: frozen public-safe outputs for each task and arm.
- `raw_observations.json`: raw reviewer observations, including a second
  reviewer on one task subset.
- `scripts/check_minimal_comparative_eval.py`: repository guard that validates
  the packet shape and hash bindings.

## Tasks

| Task | Product Entry | C0 | C1 |
|---|---|---|---|
| Public widget weekly | `industry-weekly` | direct prompt / template baseline | BriefLoop-style workflow |
| Management monthly | `management-monthly` | direct prompt / template baseline | BriefLoop-style workflow |
| Document review | `document-review` | direct prompt / template baseline | BriefLoop-style workflow |

## Observation Boundary

The rubric records three observation dimensions:

- `trace_visibility`
- `failure_visibility`
- `reader_cost`

These are raw reviewer labels. They are not a quality score, semantic judge,
benchmark win, or release gate.

## Current Read

The public-safe packet shows the comparison dimensions the project intends to
watch:

- BriefLoop-style outputs make source labels and boundary language more visible
  in this fixture.
- Direct baseline outputs are shorter.
- Reader-facing overhead remains a cost to monitor.

This packet is intentionally small and synthetic. It should not be generalized
to other domains, models, source sets, or production publication decisions.

## Validation

Run:

```bash
python3 scripts/check_minimal_comparative_eval.py
python3 scripts/check_release_consistency.py --no-tag
```
