#!/usr/bin/env python3
"""Check the v0.11.4 minimal comparative evaluation packet.

This is a release/readiness guard for a public-safe evaluation packet. It
validates preregistered tasks, frozen raw-output hashes, raw observations, and
boundary language. It does not score outputs, pick a winner, or run a workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL_ROOT = (
    ROOT / "docs" / "evaluation-results" / "v0.11.4-minimal-comparative-evaluation"
)
SCHEMA_VERSION = "briefloop.minimal_comparative_evaluation.v1"
OBSERVATIONS_SCHEMA_VERSION = "briefloop.minimal_comparative_observations.v1"
EVALUATION_ID = "v0.11.4-minimal-comparative-evaluation"
REQUIRED_ARMS = {
    "C0_direct_prompt_template_baseline",
    "C1_briefloop_workflow",
}
REQUIRED_PRODUCT_ENTRIES = {
    "industry-weekly",
    "management-monthly",
    "document-review",
}
REQUIRED_DIMENSIONS = {
    "trace_visibility",
    "failure_visibility",
    "reader_cost",
}
REQUIRED_NON_GOALS = {
    "output_quality_proof",
    "semantic_truth_proof",
    "speed_claim",
    "cross_domain_benchmark",
    "delivery_or_release_approval",
    "automatic_publication",
}
FORBIDDEN_PACKET_KEYS = {
    "winner",
    "winning_arm",
    "quality_score",
    "semantic_score",
    "release_approved",
    "publication_ready",
}


def _path_is_absolute_any_platform(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _path_has_traversal_any_platform(value: str) -> bool:
    return (
        ".." in Path(value).parts
        or ".." in PurePosixPath(value).parts
        or ".." in PureWindowsPath(value).parts
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        errors.append(f"failed to read {path.name}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path.name} must contain a mapping")
        return {}
    return payload


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"failed to read {path.name}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path.name} must contain an object")
        return {}
    return payload


def _validate_output_path(root: Path, rel_path: Any, *, prefix: str, errors: list[str]) -> Path | None:
    if not isinstance(rel_path, str) or not rel_path.strip():
        errors.append(f"{prefix}.path is required")
        return None
    if _path_is_absolute_any_platform(rel_path) or _path_has_traversal_any_platform(rel_path):
        errors.append(f"{prefix}.path must be a safe relative path")
        return None
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        errors.append(f"{prefix}.path must stay inside the evaluation directory")
        return None
    if not path.exists():
        errors.append(f"{prefix}.path does not exist: {rel_path}")
        return None
    return path


def _validate_allowed_sources(task: dict[str, Any], *, prefix: str, errors: list[str]) -> None:
    sources = task.get("allowed_sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{prefix}.allowed_sources must be a non-empty list")
        return
    for index, source in enumerate(sources):
        source_prefix = f"{prefix}.allowed_sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{source_prefix} must be an object")
            continue
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.startswith("SYN_SRC_"):
            errors.append(f"{source_prefix}.source_id must use SYN_SRC_ synthetic ids")
        url = source.get("url")
        if not isinstance(url, str):
            errors.append(f"{source_prefix}.url is required")
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{source_prefix}.url must be an HTTP(S) URL")
        host = (parsed.hostname or "").lower()
        if host != "example.com" and not host.endswith(".example.com"):
            errors.append(f"{source_prefix}.url must use example.com public-safe hosts")


def _validate_tasks(root: Path, protocol: dict[str, Any], *, errors: list[str]) -> set[str]:
    tasks = protocol.get("tasks")
    task_ids: set[str] = set()
    product_entries: set[str] = set()
    if not isinstance(tasks, list) or len(tasks) < 3:
        errors.append("protocol.tasks must contain at least three tasks")
        return task_ids
    for task_index, task in enumerate(tasks):
        prefix = f"tasks[{task_index}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object")
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            errors.append(f"{prefix}.task_id is required")
        elif task_id in task_ids:
            errors.append(f"{prefix}.task_id is duplicated: {task_id}")
        else:
            task_ids.add(task_id)
        product_entry = task.get("product_entry")
        if isinstance(product_entry, str):
            product_entries.add(product_entry)
        if task.get("public_safe") is not True:
            errors.append(f"{prefix}.public_safe must be true")
        _validate_allowed_sources(task, prefix=prefix, errors=errors)
        constraints = task.get("output_constraints")
        if not isinstance(constraints, list) or not constraints:
            errors.append(f"{prefix}.output_constraints must be a non-empty list")
        outputs = task.get("outputs")
        if not isinstance(outputs, dict):
            errors.append(f"{prefix}.outputs must be an object")
            continue
        missing_arms = sorted(REQUIRED_ARMS - set(outputs))
        if missing_arms:
            errors.append(f"{prefix}.outputs missing required arms: {missing_arms}")
        for arm_id, output in outputs.items():
            output_prefix = f"{prefix}.outputs.{arm_id}"
            if arm_id not in REQUIRED_ARMS:
                errors.append(f"{output_prefix} uses an unknown arm")
            if not isinstance(output, dict):
                errors.append(f"{output_prefix} must be an object")
                continue
            path = _validate_output_path(root, output.get("path"), prefix=output_prefix, errors=errors)
            expected_sha = output.get("sha256")
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                errors.append(f"{output_prefix}.sha256 must be a 64-character hex digest")
            elif path is not None:
                actual_sha = _sha256(path)
                if actual_sha != expected_sha:
                    errors.append(
                        f"{output_prefix}.sha256 mismatch: expected={expected_sha} actual={actual_sha}"
                    )
    missing_entries = sorted(REQUIRED_PRODUCT_ENTRIES - product_entries)
    if missing_entries:
        errors.append(f"protocol.tasks missing required product entries: {missing_entries}")
    return task_ids


def _find_forbidden_keys(value: Any, *, path: str, findings: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_PACKET_KEYS:
                findings.append(child_path)
            _find_forbidden_keys(child, path=child_path, findings=findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_forbidden_keys(child, path=f"{path}[{index}]", findings=findings)


def _validate_observations(
    observations_payload: dict[str, Any],
    *,
    task_ids: set[str],
    protocol: dict[str, Any],
    errors: list[str],
) -> None:
    if observations_payload.get("schema_version") != OBSERVATIONS_SCHEMA_VERSION:
        errors.append("raw_observations.json schema_version mismatch")
    if observations_payload.get("protocol_id") != protocol.get("evaluation_id"):
        errors.append("raw_observations.json protocol_id must match protocol evaluation_id")
    if observations_payload.get("synthetic") is not True:
        errors.append("raw_observations.json synthetic must be true")
    observations = observations_payload.get("observations")
    if not isinstance(observations, list) or not observations:
        errors.append("raw_observations.json observations must be a non-empty list")
        return

    seen_observation_ids: set[str] = set()
    reviewers: set[str] = set()
    coverage: set[tuple[str, str]] = set()
    reviewers_by_task_arm: dict[tuple[str, str], set[str]] = {}
    for index, observation in enumerate(observations):
        prefix = f"observations[{index}]"
        if not isinstance(observation, dict):
            errors.append(f"{prefix} must be an object")
            continue
        observation_id = observation.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            errors.append(f"{prefix}.observation_id is required")
        elif observation_id in seen_observation_ids:
            errors.append(f"{prefix}.observation_id is duplicated: {observation_id}")
        else:
            seen_observation_ids.add(observation_id)
        task_id = observation.get("task_id")
        arm_id = observation.get("arm_id")
        reviewer_id = observation.get("reviewer_id")
        if not isinstance(task_id, str) or task_id not in task_ids:
            errors.append(f"{prefix}.task_id must reference a protocol task")
            continue
        if not isinstance(arm_id, str) or arm_id not in REQUIRED_ARMS:
            errors.append(f"{prefix}.arm_id must reference a required arm")
            continue
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            errors.append(f"{prefix}.reviewer_id is required")
            continue
        reviewers.add(reviewer_id)
        coverage.add((task_id, arm_id))
        reviewers_by_task_arm.setdefault((task_id, arm_id), set()).add(reviewer_id)
        scores = observation.get("scores")
        if not isinstance(scores, dict):
            errors.append(f"{prefix}.scores must be an object")
        else:
            if set(scores) != REQUIRED_DIMENSIONS:
                errors.append(f"{prefix}.scores must contain exactly {sorted(REQUIRED_DIMENSIONS)}")
            for dimension, value in scores.items():
                if dimension in REQUIRED_DIMENSIONS and value not in {0, 1, 2}:
                    errors.append(f"{prefix}.scores.{dimension} must be 0, 1, or 2")
        notes = observation.get("notes")
        if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
            errors.append(f"{prefix}.notes must be a list of strings")

    missing_pairs = sorted((task_id, arm_id) for task_id in task_ids for arm_id in REQUIRED_ARMS if (task_id, arm_id) not in coverage)
    if missing_pairs:
        errors.append(f"raw_observations.json missing task/arm observations: {missing_pairs}")
    minimum_reviewers = int(((protocol.get("observations") or {}).get("min_distinct_reviewers") or 2))
    if len(reviewers) < minimum_reviewers:
        errors.append(f"raw_observations.json needs at least {minimum_reviewers} distinct reviewers")
    second_review_tasks = {
        task_id
        for task_id in task_ids
        if all(len(reviewers_by_task_arm.get((task_id, arm_id), set())) >= 2 for arm_id in REQUIRED_ARMS)
    }
    min_second_review_tasks = int(((protocol.get("observations") or {}).get("min_tasks_with_second_reviewer") or 1))
    if len(second_review_tasks) < min_second_review_tasks:
        errors.append(
            f"raw_observations.json needs at least {min_second_review_tasks} task(s) with second-reviewer coverage on both arms"
        )


def check_minimal_comparative_eval(root: str | Path = DEFAULT_EVAL_ROOT) -> dict[str, Any]:
    eval_root = Path(root).expanduser().resolve()
    errors: list[str] = []
    protocol_path = eval_root / "protocol.yaml"
    protocol = _load_yaml(protocol_path, errors)
    if not protocol:
        return {"ok": False, "errors": errors, "root": str(eval_root)}

    if protocol.get("schema_version") != SCHEMA_VERSION:
        errors.append("protocol.yaml schema_version mismatch")
    if protocol.get("evaluation_id") != EVALUATION_ID:
        errors.append("protocol.yaml evaluation_id mismatch")
    if protocol.get("synthetic") is not True:
        errors.append("protocol.yaml synthetic must be true")
    if protocol.get("runtime_effect") != "none":
        errors.append("protocol.yaml runtime_effect must be none")

    comparison = protocol.get("comparison")
    if not isinstance(comparison, dict):
        errors.append("protocol.comparison must be an object")
    else:
        arms = comparison.get("arms")
        arm_ids = {arm.get("arm_id") for arm in arms if isinstance(arm, dict)} if isinstance(arms, list) else set()
        if arm_ids != REQUIRED_ARMS:
            errors.append(f"protocol.comparison.arms must be exactly {sorted(REQUIRED_ARMS)}")
        questions = comparison.get("primary_questions")
        if not isinstance(questions, list) or len(questions) < 3:
            errors.append("protocol.comparison.primary_questions must contain at least three questions")

    rubric = protocol.get("rubric")
    if not isinstance(rubric, dict):
        errors.append("protocol.rubric must be an object")
    else:
        dimensions = rubric.get("dimensions")
        dimension_ids = set(dimensions) if isinstance(dimensions, dict) else set()
        if dimension_ids != REQUIRED_DIMENSIONS:
            errors.append(f"protocol.rubric.dimensions must be exactly {sorted(REQUIRED_DIMENSIONS)}")

    non_goals = set(protocol.get("non_goals") or [])
    missing_non_goals = sorted(REQUIRED_NON_GOALS - non_goals)
    if missing_non_goals:
        errors.append(f"protocol.non_goals missing required boundaries: {missing_non_goals}")

    forbidden_paths: list[str] = []
    _find_forbidden_keys(protocol, path="", findings=forbidden_paths)
    if forbidden_paths:
        errors.append(f"protocol.yaml contains authority-looking keys: {forbidden_paths}")

    task_ids = _validate_tasks(eval_root, protocol, errors=errors)
    observations_path_value = ((protocol.get("observations") or {}).get("path"))
    observations_path = _validate_output_path(
        eval_root,
        observations_path_value,
        prefix="protocol.observations",
        errors=errors,
    )
    observations_payload: dict[str, Any] = {}
    if observations_path is not None:
        observations_payload = _load_json(observations_path, errors)
        _validate_observations(observations_payload, task_ids=task_ids, protocol=protocol, errors=errors)
        observation_forbidden_paths: list[str] = []
        _find_forbidden_keys(observations_payload, path="", findings=observation_forbidden_paths)
        if observation_forbidden_paths:
            errors.append(f"raw_observations.json contains authority-looking keys: {observation_forbidden_paths}")

    return {
        "ok": not errors,
        "errors": errors,
        "root": str(eval_root),
        "evaluation_id": protocol.get("evaluation_id"),
        "task_count": len(task_ids),
        "arm_count": len(REQUIRED_ARMS),
        "observation_count": len(observations_payload.get("observations") or []),
        "runtime_effect": "readiness_check_only",
        "non_goals": sorted(REQUIRED_NON_GOALS),
    }


def _print_human(payload: dict[str, Any]) -> None:
    print("Minimal Comparative Evaluation Check")
    print("=" * 44)
    if payload.get("ok"):
        print(f"  [OK] {payload.get('evaluation_id')}")
        print(f"  [OK] tasks={payload.get('task_count')} arms={payload.get('arm_count')} observations={payload.get('observation_count')}")
        print("ALL CHECKS PASSED.")
        return
    for error in payload.get("errors") or []:
        print(f"  [FAIL] {error}")
    print(f"FAILED: {len(payload.get('errors') or [])} issue(s).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the v0.11.4 minimal comparative evaluation packet.")
    parser.add_argument("--root", default=str(DEFAULT_EVAL_ROOT), help="Path to the evaluation packet directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    payload = check_minimal_comparative_eval(args.root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
