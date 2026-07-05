#!/usr/bin/env python3
"""Validate the v1.0 pilot evidence record shape.

The default mode is advisory: it verifies that the evidence gate document exists
and clearly records whether v1.0 evidence is satisfied. Use
``--require-satisfied`` for the actual v1.0 release gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "docs" / "v1-pilot-evidence.md"

VALID_STATUSES = {"not_satisfied", "satisfied"}
STATUS_RE = re.compile(r"^Status:\s*`?(?P<status>[a-z_]+)`?\s*$", re.MULTILINE)
EVIDENCE_RECORD_RE = re.compile(r"^### Evidence Record:\s+.+$", re.MULTILINE)

REQUIRED_EVIDENCE_TYPES = (
    "external fresh-clone smoke",
    "WorkBuddy Skill first-user smoke",
    "pilot user checklist",
    "recurring weekly-loop dogfood",
)

REQUIRED_RECORD_FIELD_LABELS = (
    "Evidence type",
    "Date",
    "Runner",
    "Environment",
    "Artifact or log path",
    "What succeeded",
    "Where the user got confused",
    "What failed",
    "What was fixed",
    "What remains known limitation",
    "Boundary statement",
)

OPTIONAL_RECORD_FIELD_LABELS = (
    "External verification note",
)

REQUIRED_RECORD_FIELD_PHRASES = tuple(label.lower() for label in REQUIRED_RECORD_FIELD_LABELS)

REQUIRED_RECORD_BOUNDARY_PHRASES = (
    "traceability, not semantic proof",
    "not output-quality proof",
    "not delivery approval",
    "not release authority",
)

REQUIRED_BOUNDARY_PHRASES = (
    "traceability, not semantic proof",
    "measurement infrastructure, not a benchmark claim",
    "not output-quality proof",
    "not delivery approval",
    "not release authority",
    "not legal, compliance, investment, disclosure, or publication approval",
)

FORBIDDEN_OVERCLAIMS = (
    "semantic proof achieved",
    "output-quality proof achieved",
    "delivery approved",
    "release approved",
    "ready for publication",
)


def _contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str) -> bool:
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
    })
    return ok


def _warn(checks: list[dict[str, str]], check_id: str, detail: str) -> None:
    checks.append({"id": check_id, "status": "warn", "detail": detail})


def _extract_status(text: str) -> str | None:
    match = STATUS_RE.search(text)
    return match.group("status") if match else None


def _markdown_outside_fences(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return "\n".join(lines)


def _evidence_record_blocks(text: str) -> list[tuple[str, str]]:
    markdown = _markdown_outside_fences(text)
    matches = list(EVIDENCE_RECORD_RE.finditer(markdown))
    records: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = match.group(0).replace("### Evidence Record:", "", 1).strip()
        records.append((title, markdown[match.end():next_start].strip()))
    return records


def _record_field_value(block: str, label: str) -> str | None:
    pattern = re.compile(rf"^\s*-\s+{re.escape(label)}:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    match = pattern.search(block)
    if not match:
        return None
    return match.group("value").strip()


def _is_placeholder_value(value: str) -> bool:
    normalized = value.strip().strip("`").lower()
    if not normalized:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return normalized in {"tbd", "todo", "placeholder", "unknown", "n/a"}


def _artifact_path_error(value: str, *, evidence_doc_path: Path, field_values: dict[str, str]) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme == "https" and parsed.netloc:
            return None
        if parsed.scheme == "external":
            note = field_values.get("External verification note", "")
            if _is_placeholder_value(note):
                return "external Artifact or log path requires External verification note"
            return None
        return "Artifact or log path must be an existing local file, https URL, or external: reference"

    artifact_path = Path(value)
    if artifact_path.is_absolute():
        resolved = artifact_path
    else:
        try:
            evidence_doc_path.resolve().relative_to(ROOT.resolve())
            resolved = ROOT / artifact_path
        except ValueError:
            resolved = evidence_doc_path.parent / artifact_path

    if not resolved.exists():
        return f"Artifact or log path does not exist: {value}"
    if not resolved.is_file():
        return f"Artifact or log path is not a file: {value}"
    return None


def _evidence_record_errors(title: str, block: str, *, evidence_doc_path: Path) -> list[str]:
    errors: list[str] = []
    if _is_placeholder_value(title):
        errors.append("placeholder title")

    field_values: dict[str, str] = {}
    for label in (*REQUIRED_RECORD_FIELD_LABELS, *OPTIONAL_RECORD_FIELD_LABELS):
        value = _record_field_value(block, label)
        if value is None:
            if label in REQUIRED_RECORD_FIELD_LABELS:
                errors.append(f"missing {label}")
            continue
        if _is_placeholder_value(value):
            if label in REQUIRED_RECORD_FIELD_LABELS:
                errors.append(f"placeholder {label}")
            continue
        field_values[label] = value

    evidence_type = field_values.get("Evidence type")
    if evidence_type and evidence_type not in REQUIRED_EVIDENCE_TYPES:
        errors.append(f"invalid Evidence type: {evidence_type}")

    boundary = field_values.get("Boundary statement", "")
    missing_boundary = [phrase for phrase in REQUIRED_RECORD_BOUNDARY_PHRASES if not _contains(boundary, phrase)]
    if missing_boundary:
        errors.append(f"Boundary statement missing: {', '.join(missing_boundary)}")

    artifact_path = field_values.get("Artifact or log path")
    if artifact_path:
        artifact_error = _artifact_path_error(
            artifact_path,
            evidence_doc_path=evidence_doc_path,
            field_values=field_values,
        )
        if artifact_error:
            errors.append(artifact_error)

    return errors


def evaluate(path: Path, *, require_satisfied: bool = False) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    payload: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "status": "missing",
        "satisfied": False,
        "require_satisfied": require_satisfied,
        "runtime_effect": "readiness_check_only",
        "non_goals": [
            "semantic_proof",
            "output_quality_proof",
            "delivery_approval",
            "release_authority",
        ],
        "checks": checks,
    }

    if not _check(checks, "v1_pilot_evidence.file_exists", path.exists(), "evidence document exists"):
        return payload

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _check(checks, "v1_pilot_evidence.readable", False, f"document is readable: {exc}")
        return payload

    _check(checks, "v1_pilot_evidence.readable", True, "document is readable")

    status = _extract_status(text)
    status_ok = status in VALID_STATUSES
    _check(
        checks,
        "v1_pilot_evidence.status",
        status_ok,
        "status is one of not_satisfied or satisfied",
    )
    payload["status"] = status or "unknown"

    missing_types = [phrase for phrase in REQUIRED_EVIDENCE_TYPES if not _contains(text, phrase)]
    _check(
        checks,
        "v1_pilot_evidence.required_evidence_types",
        not missing_types,
        "required evidence type options are documented"
        if not missing_types
        else f"missing: {', '.join(missing_types)}",
    )

    missing_fields = [phrase for phrase in REQUIRED_RECORD_FIELD_PHRASES if not _contains(text, phrase)]
    _check(
        checks,
        "v1_pilot_evidence.required_record_fields",
        not missing_fields,
        "required evidence record fields are documented"
        if not missing_fields
        else f"missing: {', '.join(missing_fields)}",
    )

    missing_boundaries = [phrase for phrase in REQUIRED_BOUNDARY_PHRASES if not _contains(text, phrase)]
    _check(
        checks,
        "v1_pilot_evidence.boundaries",
        not missing_boundaries,
        "release-boundary phrases are present"
        if not missing_boundaries
        else f"missing: {', '.join(missing_boundaries)}",
    )

    overclaims = [phrase for phrase in FORBIDDEN_OVERCLAIMS if _contains(text, phrase)]
    _check(
        checks,
        "v1_pilot_evidence.no_overclaims",
        not overclaims,
        "no forbidden v1 readiness overclaims found"
        if not overclaims
        else f"forbidden phrases: {', '.join(overclaims)}",
    )

    records = _evidence_record_blocks(text)
    record_count = len(records)
    payload["evidence_record_count"] = record_count
    record_errors = [
        f"{title}: {'; '.join(errors)}"
        for title, block in records
        if (errors := _evidence_record_errors(title, block, evidence_doc_path=path))
    ]
    payload["valid_evidence_record_count"] = record_count - len(record_errors)

    if status == "satisfied" and not records:
        _check(
            checks,
            "v1_pilot_evidence.recorded_evidence",
            False,
            "status satisfied requires at least one Evidence Record",
        )
    elif status == "satisfied" and record_errors:
        _check(
            checks,
            "v1_pilot_evidence.recorded_evidence",
            False,
            "invalid Evidence Record block(s): " + " | ".join(record_errors),
        )
    elif status == "satisfied":
        _check(
            checks,
            "v1_pilot_evidence.recorded_evidence",
            True,
            f"{record_count} valid evidence record(s) found",
        )
    else:
        _warn(
            checks,
            "v1_pilot_evidence.recorded_evidence",
            "v1.0 pilot evidence is not satisfied yet",
        )

    payload["satisfied"] = bool(status == "satisfied" and records and not record_errors)
    if require_satisfied:
        _check(
            checks,
            "v1_pilot_evidence.require_satisfied",
            payload["satisfied"],
            "required satisfied v1.0 pilot evidence exists",
        )

    payload["ok"] = not any(check["status"] == "fail" for check in checks)
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    print("v1.0 Pilot Evidence Check")
    print("=" * 32)
    for check in payload["checks"]:
        label = {
            "pass": "OK",
            "warn": "WARN",
            "fail": "FAIL",
        }.get(check["status"], check["status"].upper())
        print(f"  [{label}] {check['id']} - {check['detail']}")
    print()
    if payload["ok"]:
        print("CHECK PASSED.")
    else:
        print("CHECK FAILED.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Evidence document path.")
    parser.add_argument(
        "--require-satisfied",
        action="store_true",
        help="Fail unless the evidence record status is satisfied and at least one evidence record exists.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    payload = evaluate(args.path, require_satisfied=args.require_satisfied)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
