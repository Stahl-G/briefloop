# BriefLoop-090 Experiment Closeout

This public closeout records the current status of the MABW-080 /
BriefLoop-090 experiment line for the v1.0 product boundary.

## Scope

- MABW-080 is the shipped experiment harness and CLI namespace.
- BriefLoop-090 is an experiment/readiness label, not a semver release number
  and not a separate CLI namespace.
- The completed public-safe pilot used the shipped
  `briefloop experiments 080` command loop.
- This closeout is about measurement infrastructure and a synthetic pilot
  observation, not normal product onboarding or delivery.

## Evidence Recorded

The current public-safe pilot record is:

- [BriefLoop-090 A-controlled auditable-brief pilot](briefloop-090-a-controlled-pilot.md)

That pilot records one synthetic `auditable_brief` case with:

- three conditions: `baseline`, `memory`, and `prompt_only`;
- the same frozen fact layer across conditions;
- condition-blind, hash-bound assessment import;
- three A-controlled arms for that case;
- a narrow observation: baseline weak, memory clean, prompt-only over-applied.

## Supported Interpretation

This evidence supports only a pilot-level statement:

> In one public-safe synthetic case, under same-frozen-fact-layer,
> condition-blind, hash-bound `auditable_brief` assessment, the intended
> guidance pattern was observed.

This evidence does not support:

- Improvement Memory improves output quality in general.
- BriefLoop proves factual correctness.
- Prompt-only is worse in general.
- The output is management-ready.
- DOCX/PDF delivery quality was validated.
- The result generalizes beyond the synthetic case.

## v1.0 Product Boundary

For the v1.0 product line, MABW-080 / BriefLoop-090 are archived experimental
measurement surfaces:

- keep them in experiment docs, reference runs, scorecard audits, support
  matrix compatibility notes, and explicit research/evaluation tasks;
- do not use them in README first screens, WorkBuddy first-user paths,
  onboarding, golden-path docs, launch claims, or ordinary workspace guidance;
- do not present `briefloop experiments 080` as a normal user workflow.

## Retirement Criteria

Do not delete or rename the current harness while archived scorecards,
workspace target-complete logic, or reference-run reproduction still depend on
the `experiments 080` namespace.

Future removal or extraction is acceptable only after:

- archived reference runs remain auditable without rewriting scorecards or
  schema IDs;
- a migration or archive reader exists for existing public evidence;
- no planned experiment line reuses the harness;
- public docs no longer need active reproduction commands.

Until then, the correct posture is archived compatibility, not deletion.
