# Report-value evaluation candidate (#757)

This is an experimental branch. Ordinary report scoring and the existing single-revision rule keep the main-branch behavior. The failed first candidate (blanket analysis ≤ 2 repair and paragraph-ratio anchors) is withdrawn. `chapter-v1` is opt-in and is not a validated new default.

## Two independent checks

- **Benchmark integrity:** freeze Markdown, requirements, sentence labels, paraphrase checks and chapter duties before dispatch. Perturb only workspace copies. Record the exact edits; a missing or unchanged edit is `not_applicable`, not a paid run or a success.
- **Product candidate:** first ask what a chapter owes this reader, then cite the requirement and an actual judgment in that chapter. Exact-location validation does not establish semantic correctness. Unknown duties and invalid quotations remain uncertain. A confirmed missing required judgment can request the existing single revision; an arbitrary analysis score cannot.

`assessment.analysis_checks` has five fields: `chapter_quote`, `requirement_quote`, `expectation`, `judgment_quote`, `rationale`. The candidate revision view validates these references without replacing the saved score, findings, original draft or independent-review status. An empty list is **not checked**. Only `quality_checklist_candidate=chapter-v1` in an experiment job opts in; ordinary jobs do not use the appendix or this revision view.

## Frozen V2 materials

`experiment_value.py` requires an explicit `--materials` manifest for the V2 variants. It will not silently use old single-model labels. The manifest has `schema_version: 1`, `rules_version: r2` and a `slices` array. Each slice supplies:

- `name`, `version`, `markdown_sha256`, `reader_contract_sha256`;
- relative `labels_file` and `paraphrases_file` paths;
- `chapter: {expectation, reason, scope}` where expectation is `required`, `optional` or `not_required`.

The reader hash is SHA-256 of the saved requirements JSON serialized as UTF-8 with `ensure_ascii=False`, `sort_keys=True`, `separators=(',', ':')`. Each material cache repeats `slice`, `version`, both hashes and `rules: r2`. Labels must cover every parsed body sentence at its exact block/index/text address, with `fact`, `implication` or `unresolved`. A paraphrase cache provides `pairs` and matching `checks`; only exact, nonempty, changed wording with a `same` judgment is accepted. Path traversal, stale contracts, incomplete caches and uncertain paraphrases fail before model dispatch.

Three-annotator agreement is not accuracy. Only agreed implication sentences are deletion targets; unresolved sentences are explicitly excluded. Chapter duties adjudicated by an AI are not human labels. Source material and private annotations stay outside Git.

## Bounded execution

After approving the selected payload and provider, a development-only example is:

```sh
python native-engine/experiment_value.py \
  --slices /path/to/frozen-slices \
  --materials /path/to/materials/manifest.json \
  --set dev --names case-a,case-b \
  --kinds control,implications_removed,implications_paraphrased \
  --repeat 2 --concurrency 2 \
  --model provider/model --variant high \
  --quality-candidate --out /path/to/new-candidate-results
```

Run the baseline without `--quality-candidate`, with the same selected cases, model and effort. Choose a new output directory; never overwrite a run. Preflight checks all selected materials before workers start. Both arms use the ordinary Evaluator. `evaluator-route.json` records the actual prompt hash and route, and the runner requires the saved assessment plus the Native packet receipt. An incomplete assessment, wrong route, failed leg or missing score is excluded from score summaries but retained in the records. Empty candidate checklists are counted separately.

Label/paraphrase scripts reject a cache whose identity is absent or differs; use a new output directory rather than refreshing frozen labels in place. A paraphrase-check cache binds both the original and replacement text plus the checker identity. Keep raw annotator outputs and any human export separate from derived consensus materials.

## Interpretation limits

- “All confirmed implication targets removed” does not prove a chapter contains no other judgments. Review the actual remaining text.
- A removed sentence may contain both facts and implications; lost fact coverage is a confound, not pure analysis sensitivity.
- An original may already have a real defect. A finding on it is not automatically a false positive.
- Reordering stays inside heading scopes and is a control for analysis presence only; coherence and expression may change.
- Report family, repeated chapter and repeated call are different sample units. Report family counts, valid/invalid/NA denominators and missing human labels explicitly.
- Cache agreement, schema admission and artifact saving do not establish quality gains. Token totals are not a bill; legacy same-table cost fields are estimates, never actual charges.
- Tune only on development cases, freeze once, then evaluate held-out report families. Do not repeatedly inspect held scores to select rules.

No candidate default, Issue closure, release or automatic expansion is implied by this tooling.
