# First Measured Reward — Agent Defect Detection (Auditor Role)

Date: 2026-09-03 · Status: measured, gating NOT enabled (variance)

## What was measured

The agent-rollout evaluation stack (`multi_agent_brief.evaluation_v2`) drove
the packaged Codex auditor role — the instructions generated from
`configs/agent_roles.yaml`, unchanged — over the synthetic seeded-defect
val corpus (40 cases, 24 blocking, 48 clean claims, four detection finding
types) through real `codex exec` rollouts. Scoring reads the agent-written
`audit_report.json` under the harness-owned reporting contract;
R = defect_recall × true_negative_rate. Three independent runs of the same
split measured the retest variance.

## Run identity

| Pin | Value |
|---|---|
| corpus_sha256 | `fa690062c330f012…` (manifest + 80 case files) |
| roles_sha256 | `477c0b7d81dd0f28…` (`configs/agent_roles.yaml`) |
| envelope_sha256 | `83a170120312f6bb…` (auditor reporting contract) |

Per-run records with full digests: `docs/evaluation-results/reward_ledger.jsonl`.

## Results

| run | R | defect_recall | true_negative_rate | block_agreement | format_compliance |
|---|---|---|---|---|---|
| 1 | 0.9583 | 1.000 (24/24) | 0.9583 (46/48) | 0.900 | 1.000 |
| 2 | 0.9375 | 1.000 (24/24) | 0.9375 (45/48) | 0.900 | 1.000 |
| 3 | 0.8958 | 1.000 (24/24) | 0.8958 (43/48) | 0.875 | 1.000 |

Mean R 0.9306 · stdev 0.0318 · **spread 0.0625 (6.25 points)**.

## Variance verdict: no gating

The pre-registered rule (DECISIONS.md D5): do not gate when
`spread > 0.025` (a single val case flips 2.5 points) or when
`spread == 0` (measurement insensitive to the rollout). The measured
spread is 0.0625 — two and a half times the per-case granularity — so the
reward gate (`scripts/check_reward_gate.py`) stays unrationalized until
the corpus grows or per-case judgment stabilizes. **Consequence recorded,
not hidden: any future skill-patch candidate must not be accepted on a
reward delta smaller than this variance.**

## Interpretation

- Detection is at ceiling on this corpus: recall 1.000 in every run, all
  four finding types, both anchor forms. The unmodified role instructions
  find every seeded defect.
- Reporting discipline is perfect: format_compliance 1.000 in every run —
  the envelope's vocabulary/anchor constraint is fully honored.
- All measured variation lives in the false-flag rate (2–5 of 48 clean
  claims) plus small block-disagreement drift (0.875–0.900). The realistic
  evolution surface for a future skill patch is precision of clean-claim
  judgment, not detection.

## Boundaries

- Synthetic corpus, one role (auditor), envelope-constrained reporting;
  this measures defect detection on seeded material, not brief utility,
  generation quality, or end-to-end performance (still NOT MEASURED).
- Baseline roles: unchanged `agent_roles.yaml`; no skill patch has been
  proposed or compared yet.
