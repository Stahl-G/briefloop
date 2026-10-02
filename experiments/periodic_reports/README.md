# Periodic-report preflight v1 (#866)

A small **offline, read-only** prerequisite for a new historical evaluation. It does
not run models, score quality, assign labels or rewrite `jev_semantic` / #763 splits.
A passing manifest is not evidence of better reports or reduced human rework.

Run from the repository root:

```sh
python experiments/periodic_reports/preflight.py path/to/manifest.json
```

Exit 0 means the declared dataset passed these mechanical checks; exit 1 prints
structured errors. All artifact paths are relative to the manifest directory and
must remain inside it after resolving symlinks. Files are checked against SHA-256.
Do not commit private report artifacts or credentials.

## Manifest contract

- `protocol`: `briefloop-periodic-reports-v1`
- `missing_data_policy`: `exclude_and_capture_prospectively`
- `comparison.baseline` and `.candidate`: each records `code_commit`,
  `prompt_sha256`, `skills_sha256`, `rules_sha256`, `model`, `effort`,
  `tool_conditions`, and positive integer `attempts`. Freeze before any run; keep
  every failed/incomplete attempt, not just the best outcome. Hash fields contain
  64 lowercase hexadecimal characters; code commits require a full 40- or 64-character lowercase Git SHA, not a mutable branch/tag. Other settings describe the actual intended run.
- `cases`: nonempty array of cases below

Each case has unique `id`, `series_id`, a descriptive `period`, `period_end` and
`cutoff` (timezone-aware ISO timestamps), `requirements`, `missing_data` (use
`none` when complete), `origin` (`real` or `synthetic`), `status`, and `split`.

Splits are `development`, `same_series_future` and `cross_series`. A same-series
future case follows **all** development cutoffs and period ends in its series.
Cross-series holdouts must have no development series in common. At least one
eligible development and one eligible holdout are required. The manifest must
keep the two holdout categories separate in eventual result reporting.

`status: not_backtestable` excludes a case and preserves its missing-data reason.
Capture the next period prospectively instead. Do not manufacture original inputs
from a final report. `status: eligible` requires `artifacts`, containing both
original inputs (`role: input`) and separately held `role: reference_final` files.
Each artifact records `path`, `sha256`, and `disclosure_family`. Inputs also require
`available_at` and `availability_evidence` (e.g. release timestamp and its retained
source). Input availability must be at/before cutoff. A file hash alone does not
prove when it was available. Final-reference availability may be later; the final
is an evaluation target and **never input** for that case.

The same hash or disclosure family cannot cross split boundaries or cross the
input/reference boundary. Assign the same disclosure family to a release and its
copies, revisions, translated excerpts, and highly correlated variants even when
bytes differ. Do not relabel families merely to make the validator pass. Repeated
sources across development and a holdout are conservatively rejected by this
protocol; select disjoint cases or explicitly design a different version rather
than quietly relaxing it. Reuse within one split and role is allowed.

## Limits and remaining acceptance

The validator checks declarations and local bytes, not the truth of provenance,
completeness of historical research, semantic correlation, or authorization to use
the supplied data. Those require review before running the evaluation. Synthetic
cases prove only mechanism behavior. Reference artifacts are not automatically
loaded into prompts because this tool does not execute any models at all.

This implements only the manifest/preflight portion of #866. Still required:
actual authorized baseline/candidate paired runs with all failures retained;
per-case facts/coverage/decision-use evaluation; disagreement and sample counts;
AI annotator/model/prompt provenance; and separately observed human editing time.
AI editing duration must not be called human time. No benefit percentage or
promotion decision is preset. Keep #866 open until those results exist.

Tests: `python -m pytest tests/test_periodic_preflight.py -q`.
