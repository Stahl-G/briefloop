# BriefLoop Product Governance Playbook

Read only the sections needed for the current product decision.

## 1. State the user problem

Begin the analysis with one sentence:

```text
The user is trying to ____, but ____ causes ____.
```

If that sentence cannot be completed, do not approve development. Clarify the
job or recommend a bounded evidence-gathering step.

## 2. Grade the evidence

Classify the strongest available evidence:

1. repeated real-user behavior or feedback;
2. measured usage data;
3. observed Pilot behavior;
4. internal dogfood;
5. technical incident or defect;
6. plausible but unverified hypothesis;
7. technology interest only.

Do not let a lower evidence level masquerade as a higher one. Tests prove an
implementation property; they do not prove that users need the feature.

## 3. Classify the proposed change

Place the change in exactly one primary layer before discussing implementation.

### Product invariant

A durable BriefLoop rule that should survive a technology-stack change. Typical
examples include:

- agents propose content but do not own authoritative control state;
- file existence is not delivery truth;
- frozen artifacts are not silently rewritten;
- human approval is not replaceable by agent narration;
- material claims remain traceable to sources;
- a contaminated run cannot be represented as a clean reference run.

### Product contract

A behavior on which users or external systems can depend, such as a supported
CLI entry, workspace format, report type, Agent Skill, or delivery result.
Changing it requires compatibility, migration, and public-claim review.

### Implementation choice

A replaceable mechanism such as JSON versus SQLite, argparse versus Typer, or
string rendering versus Jinja. Evaluate it on user impact, delivery risk,
maintenance cost, and migration cost; do not market it as product value.

### Experiment

An exploration without sufficient Pilot, evaluation, or support evidence. Keep
it visibly experimental and outside stable product promises.

## 4. Make the priority decision

Choose one:

- `DO NOW`: an evidenced current-version blocker or a bounded correction to a
  current product promise;
- `DO BEFORE PILOT`: required for a safe, comprehensible Pilot path;
- `DO AFTER V1`: valuable but not necessary to prove the current product;
- `EXPERIMENT`: a bounded hypothesis test with no stable commitment;
- `REJECT`: insufficient value, redundant mechanism, or unacceptable cost;
- `NEED EVIDENCE`: the decision materially depends on missing user evidence.

For roadmap work, sort all items into:

```text
current-version blocker
Pilot blocker
post-version engineering debt
experiment
reject or archive
```

Do not raise priority because a technology is fashionable, broadly compatible,
“enterprise-grade,” impressive in interviews, or potentially useful someday.

## 5. Minimize the product surface

Prefer, in order:

```text
delete a concept
> merge concepts
> reuse an existing mechanism
> add a bounded rule
> add a module
> add a service or technology stack
```

Before adding a concept, explain why the existing vocabulary cannot carry the
user outcome. Distinguish internal role decomposition from concepts that users
must understand. Define the MVP and explicit non-goals.

## 6. Preserve authority boundaries

| Actor | Suitable responsibility |
|---|---|
| Agent | Search, draft, analyze, propose, and surface candidates |
| Deterministic program | Validate, identify, hash, freeze, transition, gate, and record |
| Human | Make subjective decisions, approve effects, accept responsibility, and trigger final delivery |

Name the sole authoritative record. A consumer may consume that truth; it may
not reinterpret or infer it. Never approve a second truth merely to make a UI,
adapter, or runtime easier to implement.

## 7. Treat failure as product behavior

For important capabilities, specify behavior for missing or invalid input,
agent drift, interruption, frozen-artifact mutation, duplicate execution, human
rejection, legacy data, authorized recovery, and post-recovery trust.

Use Given / When / Then acceptance criteria. For cross-cutting state rules, add
only relevant State × Path rows; do not add rows merely for apparent coverage.

## 8. Select product metrics

Choose at most three to five metrics that can change the decision, such as Time
to First Auditable Brief, installation-to-first-brief time, traceability
coverage, useful gate finding rate, human editing time, per-brief cost, recovery
success, unauthorized-delivery block rate, or repeated real use.

Test count, code volume, runtime breadth, and green CI are engineering evidence,
not user-value metrics.

## 9. Review technical proposals as product choices

For any stack proposal, answer:

1. Which current problem does it solve?
2. Is it a user, product-semantics, or engineering-efficiency problem?
3. What user harm occurs if it is not introduced now?
4. Can an existing mechanism solve it?
5. What installation, operating, migration, and comprehension costs are added?
6. Does it create a second authority or public product contract?
7. Can it wait until after the current release or Pilot?
8. What smaller alternative and evidence test exist?

Do not approve technology solely because other products use it.

## 10. Apply BriefLoop red lines

Stop or reject proposals that would treat narration as runtime truth, infer
delivery from file presence, let agents bypass control duties, create a second
truth, promote Experimental capability to Supported, claim unmeasured quality,
auto-publish, break a global invariant for one runtime, leak developer
infrastructure into the user path, expand a freeze, duplicate rules across
consumers, or substitute test counts and “industrial-grade” language for value.

## 11. Review PRs at the product layer

Do not duplicate code review. Determine whether the PR still solves the user
problem, expands claims or concepts, changes compatibility, leaks machinery,
needs Pilot evidence, or can be recut into a smaller product slice.

If a defect remains inside an approved invariant, route it to Architect,
Reviewer, or Merge Governor. Do not turn every defect into a new requirement.

## 12. Design Pilots and launch gates

A Pilot must name the target user/task, baseline, scenario, observation metrics,
success and stop conditions, public-safe evidence, and still-forbidden claims.

A launch recommendation verifies that the promise is understandable, the core
path executes, installation is verifiable, limitations are visible, support
labels agree, and required human evidence exists. Green CI is not a Pilot.

## 13. Diagnose rework and complexity

Distinguish wrong product definition, missing domain rule, implementation
defect, incomplete tests/consumer sweep, instruction drift, compatibility
failure, over-design, and process-governance failure. Recommend the smallest
correction that prevents recurrence.

## 14. Communicate like a product owner

Lead with the decision, then user problem, product boundary, and implementation
implications. Avoid jargon piles, generic encouragement, unbounded “it
depends,” and balanced lists without a recommendation.

Northstar's success is unnecessary work avoided, clearer release boundaries,
less rework, faster time to a first auditable brief, honest claims, and repeated
real-user use.

## 15. Write PRDs as product contracts

Use the PRD coverage in `product-decision-card.md`. Keep implementation choices
only when they directly affect user experience, safety, auditability, cost,
compatibility, or release capability.
