"""Evaluation-case contracts and public-safe fixture scanning."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

import yaml


EVALUATION_CASES_SCHEMA = "multi-agent-brief-evaluation-cases/v1"

WORKSPACE_CASE = "workspace"
STATIC_CONTRACT_CASE = "static_contract"
CASE_TYPES = {WORKSPACE_CASE, STATIC_CONTRACT_CASE}

ALLOWED_ACTIONS = {
    "completion.project",
    "controls.build_switchboard",
    "controls.select",
    "controls.show",
    "controls.validate",
    "feedback.ingest",
    "feedback.plan",
    "feedback.validate",
    "finalize",
    "gates.check",
    "gates.show",
    "gates.validate",
    "packs.bundle",
    "provenance.build",
    "provenance.show",
    "provenance.validate",
    "quality.summarize",
    "runtime.run_handoff",
    "release.check",
    "status.show",
    "state.check",
    "state.decide",
    "state.finalize_complete",
    "state.stage_complete",
    "static.hermes_no_skip_finalize",
    "synthetic.seed_claim_support_case",
    "workbuddy.diagnose",
}

ALLOWED_EXPECTED_KEYS = {
    "artifacts_absent",
    "artifacts_exist",
    "artifact_statuses",
    "contains_text",
    "absent_text",
    "exit_code",
    "expected_actions",
    "findings_absent",
    "findings_any",
    "graph_absent_text",
    "graph_edges_any",
    "graph_nodes_any",
    "issues_any",
    "manifest_improvement",
    "workflow_state",
}

URL_RE = re.compile(r"\bhttps?://[^\s\]\)\"']+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
WINDOWS_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\")
SECRET_RE = re.compile(
    r"(-----BEGIN (?:OPENSSH|RSA|DSA|EC|PRIVATE) KEY-----|"
    r"\bsk-[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}|\bxox[baprs]-[A-Za-z0-9-]{20,}|\bAKIA[A-Z0-9]{16})"
)
PROMPT_LABEL_RE = re.compile(
    r"\b(system prompt|developer prompt|private feedback|raw prompt)\b",
    re.IGNORECASE,
)


class EvaluationCaseContractError(Exception):
    """Raised when evaluation cases violate the public-safe contract."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "details": self.details,
        }


def load_manifest(root: str | Path) -> dict[str, Any]:
    path = Path(root).expanduser().resolve() / "manifest.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise EvaluationCaseContractError(
            f"Invalid evaluation case manifest: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise EvaluationCaseContractError(
            f"Failed to read evaluation case manifest: {path}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise EvaluationCaseContractError(
            f"Evaluation case manifest must contain a mapping: {path}",
            details={"path": str(path)},
        )
    return data


def case_definitions(root: str | Path) -> list[dict[str, Any]]:
    manifest = load_manifest(root)
    cases = manifest.get("cases") or []
    return [case for case in cases if isinstance(case, dict)]


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


def validate_case_contract(root: str | Path) -> dict[str, Any]:
    resolved = Path(root).expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = load_manifest(resolved)
    except EvaluationCaseContractError as exc:
        return {"ok": False, "errors": [str(exc)], "case_count": 0}

    if manifest.get("schema_version") != EVALUATION_CASES_SCHEMA:
        errors.append("manifest.yaml has an unsupported schema_version.")
    if manifest.get("synthetic") is not True:
        errors.append("manifest.yaml must declare synthetic: true.")

    cases = manifest.get("cases")
    if not isinstance(cases, list):
        errors.append("manifest.yaml cases must be a list.")
        cases = []

    seen: set[str] = set()
    for idx, case in enumerate(cases):
        prefix = f"cases[{idx}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        case_id = str(case.get("case_id") or "")
        case_type = str(case.get("case_type") or WORKSPACE_CASE)
        if not case_id:
            errors.append(f"{prefix}.case_id is required.")
        elif case_id in seen:
            errors.append(f"{prefix}.case_id is duplicated: {case_id}.")
        seen.add(case_id)
        if case_type not in CASE_TYPES:
            errors.append(f"{prefix}.case_type must be one of {sorted(CASE_TYPES)}.")
        if case.get("synthetic") is not True:
            errors.append(f"{prefix}.synthetic must be true.")
        if case_type == WORKSPACE_CASE:
            if not str(case.get("initial_stage") or ""):
                errors.append(f"{prefix}.initial_stage is required for workspace cases.")
            workspace = resolved / "cases" / case_id / "workspace"
            if case_id and not workspace.exists():
                errors.append(f"{prefix} workspace fixture is missing: {workspace}.")
        else:
            expected = case.get("expected")
            if isinstance(expected, dict) and expected.get("artifact_statuses") is not None:
                errors.append(
                    f"{prefix}.expected.artifact_statuses requires a workspace case."
                )

        commands = case.get("commands") or []
        if not isinstance(commands, list):
            errors.append(f"{prefix}.commands must be a list.")
            commands = []
        for command_idx, command in enumerate(commands):
            command_prefix = f"{prefix}.commands[{command_idx}]"
            if isinstance(command, str):
                errors.append(f"{command_prefix} must be a structured action, not a shell string.")
                continue
            if not isinstance(command, dict):
                errors.append(f"{command_prefix} must be an object.")
                continue
            action = str(command.get("action") or "")
            if action not in ALLOWED_ACTIONS:
                errors.append(f"{command_prefix}.action is not allowlisted: {action}.")
            continue_on_failure = command.get("continue_on_failure")
            if continue_on_failure is not None and not isinstance(continue_on_failure, bool):
                errors.append(f"{command_prefix}.continue_on_failure must be a boolean.")
            args = command.get("args") or {}
            if not isinstance(args, dict):
                errors.append(f"{command_prefix}.args must be an object.")
                args = {}
            errors.extend(_validate_command_args(prefix=command_prefix, action=action, args=args))

        expected = case.get("expected") or {}
        if not isinstance(expected, dict):
            errors.append(f"{prefix}.expected must be an object.")
        else:
            unknown = sorted(set(expected) - ALLOWED_EXPECTED_KEYS)
            if unknown:
                errors.append(f"{prefix}.expected has unsupported keys: {unknown}.")
            errors.extend(_validate_expected_contract(prefix=prefix, expected=expected))

    errors.extend(scan_public_safe_fixtures(resolved, manifest=manifest))
    return {
        "ok": not errors,
        "errors": errors,
        "case_count": len([case for case in cases if isinstance(case, dict)]),
        "cases": [str(case.get("case_id")) for case in cases if isinstance(case, dict) and case.get("case_id")],
    }


def scan_public_safe_fixtures(root: Path, *, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_hosts = {"example.com"}
    for host in manifest.get("public_url_allowlist") or []:
        if isinstance(host, str) and host.strip():
            allowed_hosts.add(host.strip().lower())

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{path.relative_to(root)} is not UTF-8 text.")
            continue
        except OSError as exc:
            errors.append(f"Failed to read {path.relative_to(root)}: {exc}.")
            continue
        rel = path.relative_to(root).as_posix()
        errors.extend(_scan_text(rel=rel, text=text, allowed_hosts=allowed_hosts))
        if path.name == "claim_ledger.json":
            errors.extend(_scan_claim_ledger_ids(rel=rel, text=text))
    return errors


def _scan_text(*, rel: str, text: str, allowed_hosts: set[str]) -> list[str]:
    errors: list[str] = []
    if "file://" in text.lower():
        errors.append(f"{rel} contains a file:// reference.")
    for marker in ("/Users/", "/home/", "/private/", "/var/folders/"):
        if marker in text:
            errors.append(f"{rel} contains local path marker {marker}.")
    if WINDOWS_USER_PATH_RE.search(text):
        errors.append(f"{rel} contains a Windows user path.")
    if SECRET_RE.search(text):
        errors.append(f"{rel} contains a token or private-key shaped value.")
    if PROMPT_LABEL_RE.search(text):
        errors.append(f"{rel} contains raw prompt/private prompt labels.")

    for match in URL_RE.finditer(text):
        host = (urlparse(match.group(0)).hostname or "").lower()
        if not _url_host_allowed(host, allowed_hosts):
            errors.append(f"{rel} contains non-public-safe URL host: {host}.")
    for match in EMAIL_RE.finditer(text):
        host = match.group(1).lower()
        if not _url_host_allowed(host, allowed_hosts):
            errors.append(f"{rel} contains non-public-safe email domain: {host}.")
    return errors


def _url_host_allowed(host: str, allowed_hosts: set[str]) -> bool:
    if not host:
        return False
    if host in allowed_hosts:
        return True
    if host.endswith(".example.com"):
        return True
    if host.endswith(".invalid"):
        return True
    return False


def _validate_expected_contract(*, prefix: str, expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_actions = expected.get("expected_actions")
    if expected_actions is not None:
        if not isinstance(expected_actions, list):
            errors.append(f"{prefix}.expected.expected_actions must be a list.")
        else:
            for idx, item in enumerate(expected_actions):
                item_prefix = f"{prefix}.expected.expected_actions[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_prefix} must be an object.")
                    continue
                action = str(item.get("action") or "")
                if action not in ALLOWED_ACTIONS:
                    errors.append(f"{item_prefix}.action is not allowlisted: {action}.")
                exit_code = item.get("exit_code")
                if not isinstance(exit_code, int):
                    errors.append(f"{item_prefix}.exit_code must be an integer.")
                error_contains = item.get("error_contains")
                if error_contains is not None and not (
                    isinstance(error_contains, str)
                    or (
                        isinstance(error_contains, list)
                        and all(isinstance(value, str) for value in error_contains)
                    )
                ):
                    errors.append(
                        f"{item_prefix}.error_contains must be a string or list of strings."
                    )
                result_contains = item.get("result_contains")
                if result_contains is not None and not isinstance(result_contains, dict):
                    errors.append(f"{item_prefix}.result_contains must be an object.")

    contains_text = expected.get("contains_text")
    if contains_text is not None:
        if not isinstance(contains_text, list):
            errors.append(f"{prefix}.expected.contains_text must be a list.")
        else:
            for idx, item in enumerate(contains_text):
                item_prefix = f"{prefix}.expected.contains_text[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_prefix} must be an object.")
                    continue
                scope = item.get("scope", "cases")
                if scope not in {"cases", "repo", "workspace"}:
                    errors.append(f"{item_prefix}.scope must be cases, repo, or workspace.")
                rel_path = item.get("file")
                if not isinstance(rel_path, str) or not rel_path.strip():
                    errors.append(f"{item_prefix}.file is required.")
                    continue
                if _path_is_absolute_any_platform(rel_path):
                    errors.append(f"{item_prefix}.file must be relative, not absolute.")
                if _path_has_traversal_any_platform(rel_path):
                    errors.append(f"{item_prefix}.file must not contain path traversal.")
    absent_text = expected.get("absent_text")
    if absent_text is not None:
        if not isinstance(absent_text, list):
            errors.append(f"{prefix}.expected.absent_text must be a list.")
        else:
            for idx, item in enumerate(absent_text):
                item_prefix = f"{prefix}.expected.absent_text[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_prefix} must be an object.")
                    continue
                scope = item.get("scope", "cases")
                if scope not in {"cases", "repo", "workspace"}:
                    errors.append(f"{item_prefix}.scope must be cases, repo, or workspace.")
                rel_path = item.get("file")
                if not isinstance(rel_path, str) or not rel_path.strip():
                    errors.append(f"{item_prefix}.file is required.")
                    continue
                if _path_is_absolute_any_platform(rel_path):
                    errors.append(f"{item_prefix}.file must be relative, not absolute.")
                if _path_has_traversal_any_platform(rel_path):
                    errors.append(f"{item_prefix}.file must not contain path traversal.")
    artifact_statuses = expected.get("artifact_statuses")
    if artifact_statuses is not None:
        if not isinstance(artifact_statuses, list):
            errors.append(f"{prefix}.expected.artifact_statuses must be a list.")
        else:
            for idx, item in enumerate(artifact_statuses):
                item_prefix = f"{prefix}.expected.artifact_statuses[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{item_prefix} must be an object.")
                    continue
                artifact_id = item.get("artifact_id")
                if not isinstance(artifact_id, str) or not artifact_id.strip():
                    errors.append(f"{item_prefix}.artifact_id is required.")
                for field in ("status", "validation_result", "validation_result_contains"):
                    value = item.get(field)
                    if value is not None and not isinstance(value, str):
                        errors.append(f"{item_prefix}.{field} must be a string.")
    return errors


def _validate_command_args(*, prefix: str, action: str, args: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if action == "feedback.ingest":
        feedback = args.get("feedback")
        if not isinstance(feedback, str) or not feedback.strip():
            errors.append(f"{prefix}.args.feedback is required.")
        elif _path_is_absolute_any_platform(feedback):
            errors.append(f"{prefix}.args.feedback must be relative, not absolute.")
        elif _path_has_traversal_any_platform(feedback):
            errors.append(f"{prefix}.args.feedback must not contain path traversal.")
    if action == "release.check":
        mode = args.get("mode")
        if not isinstance(mode, str) or not mode.strip():
            errors.append(f"{prefix}.args.mode is required.")
    if action == "synthetic.seed_claim_support_case":
        scenario = args.get("scenario")
        if not isinstance(scenario, str) or not scenario.strip():
            errors.append(f"{prefix}.args.scenario is required.")
    return errors


def _scan_claim_ledger_ids(*, rel: str, text: str) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return errors
    _scan_json_ids(data, path=rel, errors=errors)
    return errors


def _scan_json_ids(data: Any, *, path: str, errors: list[str]) -> None:
    if isinstance(data, dict):
        claim_id = data.get("claim_id")
        source_id = data.get("source_id")
        if isinstance(claim_id, str) and claim_id and not claim_id.startswith("SYN_CLAIM_"):
            errors.append(f"{path}.claim_id must use SYN_CLAIM_ synthetic IDs.")
        if isinstance(source_id, str) and source_id and not source_id.startswith("SYN_SRC_"):
            errors.append(f"{path}.source_id must use SYN_SRC_ synthetic IDs.")
        for key, value in data.items():
            _scan_json_ids(value, path=f"{path}.{key}", errors=errors)
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            _scan_json_ids(value, path=f"{path}[{idx}]", errors=errors)
