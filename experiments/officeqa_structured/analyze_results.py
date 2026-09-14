"""Paired statistics and the protocol §9.4 result template (design §3.Q4).

Reads the frozen, already-scored outputs of one run label —
``evaluator-only/scores/<label>/scores.jsonl`` plus ``summary.json``, the
freeze manifest and the run index when present — and produces:

* paired bootstrap 95% CI for Delta = Accuracy[B] - Accuracy[A] (cases are
  the sampling unit, never per-field rows; seed and sample count recorded,
  protocol §9.3);
* exact McNemar (two-sided) over the discordant pairs wrong->right vs
  right->wrong;
* the §9.4 result template rendered to ``results.md`` (machine copy in
  ``results.json``), with integer correct counts alongside percentages.

This module only reads evaluator-side artifacts; it never touches gold
beyond what ``score`` already recorded, never re-scores, and never rewrites
sealed predictions.  Dry-run inputs are reported as such — stub answers are
fixed synthetic strings, so the numbers verify the pipeline, not any model.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "officeqa.results.v1"
DEFAULT_DATA_ROOT = Path.home() / "Developer" / "briefloop-data" / "officeqa"


class AnalysisError(RuntimeError):
    """Result analysis failed closed."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percentile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    frac = position - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def paired_bootstrap(pairs: list[tuple[int, int]], *, samples: int, seed: int) -> dict[str, Any]:
    """Bootstrap the distribution of Delta = mean(B) - mean(A) over cases.

    ``pairs`` holds one (B, A) 0/1 score pair per case; resampling is by
    case with replacement, seeded and reproducible (protocol §9.3: the
    bootstrap clusters by question — repeated runs of one question are not
    new questions).
    """
    if not pairs:
        raise AnalysisError("no paired scores to bootstrap")
    if samples < 100:
        raise AnalysisError("bootstrap needs at least 100 resamples for a 95% interval")
    b_scores = [pair[0] for pair in pairs]
    a_scores = [pair[1] for pair in pairs]
    n = len(pairs)
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(samples):
        delta = 0
        for _ in range(n):
            index = rng.randrange(n)
            delta += b_scores[index] - a_scores[index]
        deltas.append(delta / n)
    deltas.sort()
    return {
        "n_pairs": n,
        "samples": samples,
        "seed": seed,
        "delta": round(sum(b_scores) / n - sum(a_scores) / n, 4),
        "delta_correct_counts": {"b": sum(b_scores), "a": sum(a_scores)},
        "ci95": [round(_percentile(deltas, 0.025), 4), round(_percentile(deltas, 0.975), 4)],
        "ci95_includes_zero": _percentile(deltas, 0.025) <= 0.0 <= _percentile(deltas, 0.975),
    }


def exact_mcnemar(wrong_to_right: int, right_to_wrong: int) -> float | None:
    """Two-sided exact McNemar over discordant pairs (binomial, p=0.5).

    Returns None when there are no discordant pairs — the test carries no
    information then, and reporting a fake p=1 would hide that.
    """
    n = wrong_to_right + right_to_wrong
    if n == 0:
        return None
    k = min(wrong_to_right, right_to_wrong)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise AnalysisError(f"required file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AnalysisError(f"required file is missing: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wall_stats(seconds: list[float]) -> dict[str, Any]:
    if not seconds:
        return {"median": None, "p90": None}
    ordered = sorted(seconds)

    def percentile(q: float) -> float:
        return round(_percentile([float(value) for value in ordered], q), 1)

    return {"median": percentile(0.5), "p90": percentile(0.9)}


def analyze(data_root: Path, run_label: str, *, samples: int, seed: int) -> dict[str, Any]:
    data_root = Path(data_root).expanduser().resolve()
    scores_root = data_root / "evaluator-only" / "scores" / run_label
    scores = _load_jsonl(scores_root / "scores.jsonl")
    summary = _load_json(scores_root / "summary.json")
    if not scores:
        raise AnalysisError(f"no scored rows for run label {run_label!r}")
    frozen_manifest = _load_json(data_root / "evaluator-only" / "frozen" / run_label / "manifest.json")
    run_index: dict[str, Any] = {}
    index_path = data_root / "episodes" / run_label / "run_index.json"
    if index_path.is_file():
        run_index = json.loads(index_path.read_text(encoding="utf-8"))
    config_path = Path(__file__).resolve().parent / "config.json"
    config = _load_json(config_path)

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in scores:
        by_key[(row["arm"], row["case_key"])] = row
    case_keys = sorted({row["case_key"] for row in scores})
    pairs = [(int(by_key[("B", key)]["score"] >= 1.0), int(by_key[("A", key)]["score"] >= 1.0))
             for key in case_keys
             if ("B", key) in by_key and ("A", key) in by_key]
    paired = bool(pairs)
    bootstrap = paired_bootstrap(pairs, samples=samples, seed=seed) if paired else None
    wrong_to_right = sum(1 for b, a in pairs if b > a) if paired else None
    right_to_wrong = sum(1 for b, a in pairs if b < a) if paired else None
    mcnemar_p = exact_mcnemar(wrong_to_right or 0, right_to_wrong or 0) if paired else None

    # §9.2 process columns, straight from the episode records when present.
    episodes = run_index.get("episodes") or []
    exposure_counts: dict[str, int] = {}
    for case in run_index.get("cases") or []:
        exposure_counts[case.get("exposure", "unknown")] = exposure_counts.get(case.get("exposure", "unknown"), 0) + 1
    arms: dict[str, dict[str, Any]] = {}
    for arm in ("A", "B"):
        rows = [row for row in scores if row["arm"] == arm]
        arm_episodes = [episode for episode in episodes if episode.get("arm") == arm]
        complete = 0
        for episode in arm_episodes:
            chosen = bool(episode.get("chosen_submission"))
            if arm == "B":
                job_status = (episode.get("briefloop") or {}).get("job_status")
                complete += int(chosen and job_status == "complete")
            else:
                complete += int(chosen)
        correct = sum(1 for row in rows if row["score"] >= 1.0)
        arms[arm] = {
            "N": len(rows), "correct": correct,
            "accuracy": round(correct / len(rows), 4) if rows else None,
            "valid_answer_rate": summary.get("arms", {}).get(arm, {}).get("valid_answer_rate"),
            "abstained": sum(1 for row in rows if row["outcome"] == "abstained"),
            "format_error": sum(1 for row in rows if row["outcome"] == "invalid"),
            "missing": sum(1 for row in rows if row["outcome"] == "missing"),
            "end_to_end_complete": {"n": complete, "N": len(arm_episodes)} if arm_episodes else None,
            "usage_complete": all(episode.get("usage_complete") for episode in arm_episodes) if arm_episodes else None,
            "wall_clock_seconds": _wall_stats([episode.get("wall_clock_seconds", 0.0)
                                               for episode in arm_episodes]),
        }

    dry_run = bool(summary.get("dry_run", frozen_manifest.get("dry_run", False)))
    dataset = config.get("dataset") or {}
    web = config.get("web_search") or {}
    runtime = config.get("runtime") or {}
    results: dict[str, Any] = {
        "schema_version": SCHEMA,
        "analyzed_at": _now(),
        "protocol_id": summary.get("protocol_id"),
        "experiment_id": summary.get("experiment_id"),
        "run_label": run_label,
        "dry_run": dry_run,
        "dataset": {"benchmark": dataset.get("benchmark"),
                    "revision": dataset.get("revision"),
                    "N": len(case_keys),
                    "exposure": exposure_counts,
                    "pdf_originals": dataset.get("pdf_originals")},
        "input": {"corpus_documents": dataset.get("corpus_documents"),
                  "network": web.get("entrypoint") or "none wired (web_search.entrypoint pending freeze)"},
        "model_runtime": {"host": runtime.get("host"), "model": runtime.get("model"),
                          "reviewer_model": runtime.get("reviewer_model")},
        "arms": arms,
        "paired": {"available": paired, "n_pairs": len(pairs),
                   "bootstrap": bootstrap,
                   "b_wrong_to_right": wrong_to_right, "b_right_to_wrong": right_to_wrong,
                   "exact_mcnemar_p": mcnemar_p},
        "notes": (["dry-run stub 答案为固定合成值：全部数字只验证管线，不代表任何模型成绩"]
                  if dry_run else []) +
                 (["双组配对不完整：A/B 题集不完全重叠，配对统计仅覆盖成对题"] if not paired else []),
    }
    return results


def render_template(results: dict[str, Any]) -> str:
    """Protocol §9.4 result template — percentages AND integer counts."""
    dataset = results["dataset"]
    model = results["model_runtime"]
    arms = results["arms"]

    def arm_line(label: str, key: str) -> str:
        row = arms.get(key) or {}
        correct, n = row.get("correct"), row.get("N")
        accuracy = f"{row['accuracy'] * 100:.1f}%" if row.get("accuracy") is not None else "--"
        valid = f"{row['valid_answer_rate'] * 100:.1f}%" if row.get("valid_answer_rate") is not None else "--"
        e2e = row.get("end_to_end_complete")
        e2e_text = f"{e2e['n']}/{e2e['N']}" if e2e else "--"
        wall = row.get("wall_clock_seconds") or {}
        time_text = (f"median {wall['median']}s / P90 {wall['p90']}s"
                     if wall.get("median") is not None else "--")
        usage_value = row.get("usage_complete")
        usage = "--" if usage_value is None else ("true" if usage_value else "false")
        return (f"{label:<18} {correct} / {n:<11} {accuracy:<10} {valid:<14} {e2e_text:<14} "
                f"{time_text:<24} {usage}")

    paired = results["paired"]
    bootstrap = paired.get("bootstrap") or {}
    if bootstrap:
        delta_pp = bootstrap["delta"] * 100
        ci = bootstrap["ci95"]
        delta_line = (f"Paired Delta: {delta_pp:+.1f} percentage points "
                      f"[95% interval {ci[0] * 100:+.1f}, {ci[1] * 100:+.1f}] "
                      f"(paired bootstrap, {bootstrap['samples']} resamples, seed {bootstrap['seed']}; "
                      f"{'区间跨零，未建立稳定收益' if bootstrap['ci95_includes_zero'] else '区间不含零'})")
        mcnemar = paired.get("exact_mcnemar_p")
        mcnemar_line = (f"B wrong→right: {paired['b_wrong_to_right']} ; B right→wrong: {paired['b_right_to_wrong']}"
                        + (f" ; exact McNemar p = {mcnemar:.4f}" if mcnemar is not None
                           else " ; 无不一致配对，exact McNemar 不适用"))
    else:
        delta_line = "Paired Delta: -- (A/B 配对不完整)"
        mcnemar_line = "B wrong→right: -- ; B right→wrong: --"
    a, b = arms.get("A") or {}, arms.get("B") or {}
    no_answer = f"{a.get('missing', 0) + b.get('missing', 0)}"
    abstain = f"{a.get('abstained', 0)} / {b.get('abstained', 0)}"
    fmt = f"{a.get('format_error', 0)} / {b.get('format_error', 0)}"
    revision = dataset.get("revision") or "pending freeze"
    exposure = ", ".join(f"{key}={value}" for key, value in sorted((dataset.get("exposure") or {}).items())) or "n/a"
    pdf = "no PDF originals in this release (§6.1 visual parity moot)"
    network = results["input"]["network"]
    identity = (f"{model.get('host')} / {model.get('model')}"
                if model.get("host") and model.get("model") else "pending Q3 freeze (dry-run stub transport)")
    lines = [
        f"# OfficeQA structured results — {results['run_label']}",
        "",
        f"Dataset: {dataset.get('benchmark')} revision={revision} "
        f"N={dataset.get('N')} (exposure: {exposure})",
        f"Input: full corpus ({results['input']['corpus_documents']} docs, shared parse; {pdf}) / network: {network}",
        f"Model & runtime: {identity}",
        "",
        "```text",
        "Arm                Correct / N   Accuracy   Valid answer   Complete E2E   Time                       Usage complete",
        arm_line("Native control", "A"),
        arm_line("BriefLoop QA", "B"),
        "",
        delta_line,
        mcnemar_line,
        f"No-answer (A+B) / abstention (A / B) / format-error (A / B): {no_answer} / {abstain} / {fmt}",
        "",
        "Not established: report quality, Word formatting, long-term learning benefits.",
        "```",
        "",
    ]
    for note in results.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--samples", type=int, default=10000, help="bootstrap resamples (>=100)")
    parser.add_argument("--seed", type=int, default=20260915, help="bootstrap seed (recorded)")
    args = parser.parse_args(argv)
    try:
        results = analyze(Path(args.data_root), args.run_label, samples=args.samples, seed=args.seed)
    except AnalysisError as exc:
        print(f"analyze_results error: {exc}", file=sys.stderr)
        return 2
    out_root = Path(args.data_root).expanduser().resolve() / "evaluator-only" / "scores" / args.run_label
    markdown = render_template(results)
    _write_private(out_root / "results.md", markdown.encode("utf-8"))
    _write_private(out_root / "results.json",
                   (json.dumps(results, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"run_label": args.run_label,
                      "paired": results["paired"]["available"],
                      "results": str(out_root / "results.md")}, ensure_ascii=False))
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
