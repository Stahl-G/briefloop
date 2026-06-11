"""Side-effect-free Improvement Ledger contract.

The v0.7 Improvement Ledger is an append-only workspace contract for
human-approved audience guidance. This module validates ledger revisions,
computes current read state, and checks append preconditions. It deliberately
does not write files, append events, read runtime state, or materialize runtime
snapshots.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


IMPROVEMENT_LEDGER_SCHEMA = "multi-agent-brief-improvement-ledger/v1"
LEDGER_RELATIVE_PATH = "improvement/ledger.jsonl"

ALLOWED_LEVEL = 2
ALLOWED_TARGET_KIND = "audience_guidance"
ALLOWED_STATUSES = {"proposed", "approved", "rejected", "reverted"}
TERMINAL_STATUSES = {"rejected", "reverted"}
ALLOWED_SOURCE_TYPES = {"human_feedback", "feedback_issue"}
AUDIENCE_GUIDANCE_CATEGORIES = {
    "audience_mismatch",
    "tone",
    "structure",
    "length",
    "source_presentation",
    "other",
}
AUDIENCE_GUIDANCE_SCOPES = {
    "brief",
    "executive_summary",
    "section",
    "source_appendix",
}
ALLOWED_ORIGIN_KEYS = {
    "control_file",
    "gate_id",
    "finding_type",
    "blocking_level",
    "source_item_id",
    "issue_category",
    "issue_source",
    "origin_runtime",
}
DIAGNOSTIC_SEVERITIES = {"info", "warning", "error"}
DIAGNOSTIC_CODES = {
    "unknown_schema_version",
    "invalid_entry_id",
    "invalid_level",
    "reserved_in_v0_7",
    "invalid_target_kind",
    "invalid_status",
    "stored_applied_state_forbidden",
    "invalid_revision_sequence",
    "invalid_transition",
    "missing_previous_revision",
    "previous_revision_hash_mismatch",
    "corrupt_trailing_line",
    "corrupt_non_trailing_line",
    "append_preflight_failed",
    "unsafe_guidance_text",
    "missing_approval_metadata",
    "missing_source_evidence",
    "invalid_source_evidence",
    "invalid_timestamp",
    "immutable_revision_field_changed",
    "invalid_change",
    "invalid_origin",
    "invalid_approval_metadata",
    "invalid_supersedes_id",
    "unknown_supersedes_reference",
    "supersession_cycle",
}

MAX_GUIDANCE_TEXT_LENGTH = 500
MAX_EVIDENCE_SUMMARY_LENGTH = 300
MAX_SOURCE_REFERENCE_LENGTH = 160
MAX_ORIGIN_VALUE_LENGTH = 120
MAX_APPROVAL_REASON_LENGTH = 300

_ENTRY_ID_RE = re.compile(r"^AG-\d{4,}$")
_OPERATOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}$")
_UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WINDOWS_ABSOLUTE_RE = re.compile(r"\b[A-Za-z]:[\\/]")
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
_INJECTION_PHRASES = [
    "system:",
    "developer:",
    "assistant:",
    "ignore previous",
    "ignore all previous",
]
_FORBIDDEN_PATH_FRAGMENTS = [
    "/Users/",
    "/home/",
    "/var/",
    "file://",
]


@dataclass(frozen=True)
class LedgerDiagnostic:
    code: str
    severity: str
    message: str
    line_number: int | None = None
    entry_id: str | None = None
    revision: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in DIAGNOSTIC_SEVERITIES:
            raise ValueError(f"Unsupported diagnostic severity: {self.severity}")
        if self.code not in DIAGNOSTIC_CODES:
            raise ValueError(f"Unsupported diagnostic code: {self.code}")


@dataclass(frozen=True)
class LedgerReadResult:
    valid_revisions: list[dict[str, Any]]
    current_entries: dict[str, dict[str, Any]]
    diagnostics: list[LedgerDiagnostic]


@dataclass(frozen=True)
class AppendPreflightResult:
    ok: bool
    diagnostics: list[LedgerDiagnostic]


def canonical_json(payload: dict[str, Any]) -> str:
    """Return the canonical JSON string used for revision hash chaining."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def revision_sha256(payload: dict[str, Any]) -> str:
    """Return SHA-256 of a revision's full canonical JSON."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def current_entries_from_revisions(revisions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return the highest valid revision for each entry_id."""
    current: dict[str, dict[str, Any]] = {}
    for revision in revisions:
        entry_id = str(revision.get("entry_id") or "")
        if not entry_id:
            continue
        existing = current.get(entry_id)
        if existing is None or int(revision.get("revision", 0)) > int(existing.get("revision", 0)):
            current[entry_id] = revision
    return current


def supersedes_id(entry: dict[str, Any]) -> str | None:
    """Return normalized supersedes_id; missing and null are semantically equal."""
    value = entry.get("supersedes_id")
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def read_ledger_text(text: str) -> LedgerReadResult:
    """Parse ledger JSONL text into valid revisions and diagnostics.

    A corrupt trailing line is reported as a warning and valid prior revisions
    remain readable. A corrupt middle line is fatal for the remaining file:
    later lines are ignored because the append-only chain is no longer trusted.
    """
    diagnostics: list[LedgerDiagnostic] = []
    valid_revisions: list[dict[str, Any]] = []
    previous_by_entry: dict[str, dict[str, Any]] = {}
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        is_trailing = idx == len(lines)
        if not line.strip():
            code = "corrupt_trailing_line" if is_trailing else "corrupt_non_trailing_line"
            severity = "warning" if is_trailing else "error"
            diagnostics.append(_diag(
                code,
                severity,
                "Ledger JSONL line is blank.",
                line_number=idx,
            ))
            if not is_trailing:
                break
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            code = "corrupt_trailing_line" if is_trailing else "corrupt_non_trailing_line"
            severity = "warning" if is_trailing else "error"
            diagnostics.append(_diag(
                code,
                severity,
                f"Ledger JSONL line is not valid JSON: {exc}",
                line_number=idx,
            ))
            break
        if not isinstance(payload, dict):
            diagnostics.append(_diag(
                "corrupt_trailing_line" if is_trailing else "corrupt_non_trailing_line",
                "warning" if is_trailing else "error",
                "Ledger JSONL line must contain an object.",
                line_number=idx,
            ))
            if not is_trailing:
                break
            continue

        entry_id = str(payload.get("entry_id") or "")
        previous = previous_by_entry.get(entry_id)
        errors = validate_revision_payload(payload, previous_revision=previous, line_number=idx)
        diagnostics.extend(errors)
        if any(item.severity == "error" for item in errors):
            if not is_trailing:
                break
            continue
        valid_revisions.append(payload)
        previous_by_entry[entry_id] = payload

    current_entries = current_entries_from_revisions(valid_revisions)
    diagnostics.extend(_validate_supersession_graph(current_entries))
    return LedgerReadResult(
        valid_revisions=valid_revisions,
        current_entries=current_entries,
        diagnostics=diagnostics,
    )


def validate_append_preflight(text: str) -> AppendPreflightResult:
    """Validate whether an existing ledger text is safe to append to."""
    if text == "":
        return AppendPreflightResult(ok=True, diagnostics=[])
    diagnostics: list[LedgerDiagnostic] = []
    if not text.endswith("\n"):
        diagnostics.append(_diag(
            "append_preflight_failed",
            "error",
            "Existing ledger must end with a newline before appending.",
        ))
    read_result = read_ledger_text(text)
    diagnostics.extend(read_result.diagnostics)
    if read_result.valid_revisions and len(read_result.valid_revisions) != _non_empty_line_count(text):
        diagnostics.append(_diag(
            "append_preflight_failed",
            "error",
            "Existing ledger contains invalid or untrusted JSONL lines.",
        ))
    if not read_result.valid_revisions and _non_empty_line_count(text) > 0:
        diagnostics.append(_diag(
            "append_preflight_failed",
            "error",
            "Existing ledger contains no valid append base.",
        ))
    ok = not any(item.severity in {"warning", "error"} for item in diagnostics)
    return AppendPreflightResult(ok=ok, diagnostics=_dedupe_diagnostics(diagnostics))


def validate_next_revision(existing_text: str, revision: dict[str, Any]) -> AppendPreflightResult:
    """Validate append preflight plus the next revision payload."""
    preflight = validate_append_preflight(existing_text)
    if not preflight.ok:
        return preflight
    read_result = read_ledger_text(existing_text)
    entry_id = str(revision.get("entry_id") or "")
    previous = read_result.current_entries.get(entry_id)
    diagnostics = validate_revision_payload(revision, previous_revision=previous)
    if not any(item.severity == "error" for item in diagnostics):
        current_entries = current_entries_from_revisions([*read_result.valid_revisions, revision])
        diagnostics.extend(_validate_supersession_graph(current_entries))
    return AppendPreflightResult(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
    )


def validate_revision_payload(
    payload: dict[str, Any],
    *,
    previous_revision: dict[str, Any] | None = None,
    line_number: int | None = None,
) -> list[LedgerDiagnostic]:
    """Validate one complete ledger revision payload."""
    diagnostics: list[LedgerDiagnostic] = []
    entry_id = payload.get("entry_id")
    revision = payload.get("revision")
    entry_id_text = str(entry_id) if entry_id is not None else None
    revision_number = revision if isinstance(revision, int) else None

    if payload.get("schema_version") != IMPROVEMENT_LEDGER_SCHEMA:
        diagnostics.append(_diag(
            "unknown_schema_version",
            "error",
            "Ledger revision has an unsupported schema_version.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    if not isinstance(entry_id, str) or not _ENTRY_ID_RE.match(entry_id):
        diagnostics.append(_diag(
            "invalid_entry_id",
            "error",
            "entry_id must match AG-0001 style.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    diagnostics.extend(_validate_supersedes_id(
        payload,
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))

    if not isinstance(revision, int) or revision < 1:
        diagnostics.append(_diag(
            "invalid_revision_sequence",
            "error",
            "revision must be a positive integer.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))
        revision_number = None

    if not _is_utc_timestamp(payload.get("created_at")):
        diagnostics.append(_diag(
            "invalid_timestamp",
            "error",
            "created_at must be a UTC ISO-like timestamp such as 2026-06-10T00:00:00Z.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    status = payload.get("status")
    if status == "applied":
        diagnostics.append(_diag(
            "stored_applied_state_forbidden",
            "error",
            "applied is a per-run manifest fact, not a stored ledger state.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))
    elif status not in ALLOWED_STATUSES:
        diagnostics.append(_diag(
            "invalid_status",
            "error",
            f"status must be one of {sorted(ALLOWED_STATUSES)}.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    level = payload.get("level")
    if level != ALLOWED_LEVEL:
        code = "reserved_in_v0_7" if level in {0, 1, 3, 4} else "invalid_level"
        diagnostics.append(_diag(
            code,
            "error",
            "Only level=2 Improvement Ledger entries are active in v0.7.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    target_kind = payload.get("target_kind")
    if target_kind == "workspace_memory":
        diagnostics.append(_diag(
            "reserved_in_v0_7",
            "error",
            "workspace_memory is rejected in v0.7.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))
    elif target_kind != ALLOWED_TARGET_KIND:
        diagnostics.append(_diag(
            "invalid_target_kind",
            "error",
            "Only target_kind=audience_guidance is active in v0.7.",
            line_number=line_number,
            entry_id=entry_id_text,
            revision=revision_number,
        ))

    diagnostics.extend(_validate_revision_chain(
        payload,
        previous_revision=previous_revision,
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    diagnostics.extend(_validate_transition(
        payload,
        previous_revision=previous_revision,
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    diagnostics.extend(_validate_immutable_status_revision(
        payload,
        previous_revision=previous_revision,
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    diagnostics.extend(_validate_change(
        payload.get("change"),
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    diagnostics.extend(_validate_source_evidence(
        payload.get("source_evidence"),
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    diagnostics.extend(_validate_status_metadata(
        payload,
        line_number=line_number,
        entry_id=entry_id_text,
        revision=revision_number,
    ))
    return diagnostics


def validate_guidance_text(text: Any) -> list[LedgerDiagnostic]:
    diagnostics: list[LedgerDiagnostic] = []
    if not isinstance(text, str) or not text.strip():
        return [_diag("unsafe_guidance_text", "error", "guidance_text must be a non-empty string.")]
    if len(text) > MAX_GUIDANCE_TEXT_LENGTH:
        diagnostics.append(_diag("unsafe_guidance_text", "error", "guidance_text is too long."))
    diagnostics.extend(_text_hygiene_diagnostics(text, code="unsafe_guidance_text", label="guidance_text"))
    return diagnostics


def _validate_revision_chain(
    payload: dict[str, Any],
    *,
    previous_revision: dict[str, Any] | None,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    diagnostics: list[LedgerDiagnostic] = []
    if revision is None:
        return diagnostics

    if "previous_revision_sha256" not in payload:
        diagnostics.append(_diag(
            "missing_previous_revision",
            "error",
            "previous_revision_sha256 is required and must be null for revision 1.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
        return diagnostics

    previous_hash = payload.get("previous_revision_sha256")
    if revision == 1:
        if previous_revision is not None:
            diagnostics.append(_diag(
                "invalid_revision_sequence",
                "error",
                "revision 1 cannot follow an existing revision for the same entry.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        if previous_hash is not None:
            diagnostics.append(_diag(
                "missing_previous_revision",
                "error",
                "revision 1 must explicitly set previous_revision_sha256 to null.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        return diagnostics

    if previous_revision is None:
        diagnostics.append(_diag(
            "missing_previous_revision",
            "error",
            "revision > 1 requires a previous revision for the same entry_id.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
        return diagnostics

    expected_revision = int(previous_revision.get("revision", 0)) + 1
    if revision != expected_revision:
        diagnostics.append(_diag(
            "invalid_revision_sequence",
            "error",
            f"revision must increment monotonically to {expected_revision}.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    expected_hash = revision_sha256(previous_revision)
    if previous_hash != expected_hash:
        diagnostics.append(_diag(
            "previous_revision_hash_mismatch",
            "error",
            "previous_revision_sha256 must match the immediately previous revision canonical hash.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    return diagnostics


def _validate_transition(
    payload: dict[str, Any],
    *,
    previous_revision: dict[str, Any] | None,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    status = payload.get("status")
    if status not in ALLOWED_STATUSES:
        return []
    if previous_revision is None:
        if status != "proposed":
            return [_diag(
                "invalid_transition",
                "error",
                "revision 1 must start in proposed state.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            )]
        return []

    previous_status = previous_revision.get("status")
    allowed = {
        "proposed": {"approved", "rejected"},
        "approved": {"reverted"},
        "rejected": set(),
        "reverted": set(),
    }
    if status not in allowed.get(str(previous_status), set()):
        return [_diag(
            "invalid_transition",
            "error",
            f"Invalid ledger transition: {previous_status} -> {status}.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        )]
    return []


def _validate_immutable_status_revision(
    payload: dict[str, Any],
    *,
    previous_revision: dict[str, Any] | None,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    """Status-only transitions must not rewrite approved guidance content."""
    if previous_revision is None:
        return []
    diagnostics: list[LedgerDiagnostic] = []
    for field in ("level", "target_kind", "change", "source_evidence"):
        if payload.get(field) != previous_revision.get(field):
            diagnostics.append(_diag(
                "immutable_revision_field_changed",
                "error",
                f"{field} must remain unchanged across status transitions.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
    if supersedes_id(payload) != supersedes_id(previous_revision):
        diagnostics.append(_diag(
            "immutable_revision_field_changed",
            "error",
            "supersedes_id must remain unchanged across status transitions.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    return diagnostics


def _validate_supersedes_id(
    payload: dict[str, Any],
    *,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    if "supersedes_id" not in payload:
        return []
    value = payload.get("supersedes_id")
    if value is None:
        return []
    if not isinstance(value, str) or not _ENTRY_ID_RE.match(value):
        return [_diag(
            "invalid_supersedes_id",
            "error",
            "supersedes_id must be null or match AG-0001 style.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        )]
    if entry_id and value == entry_id:
        return [_diag(
            "invalid_supersedes_id",
            "error",
            "supersedes_id must not reference the same entry.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        )]
    return []


def _validate_change(
    change: Any,
    *,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    if not isinstance(change, dict):
        return [_diag(
            "invalid_change",
            "error",
            "change must be an object containing guidance_text.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        )]
    diagnostics: list[LedgerDiagnostic] = []
    category = change.get("category")
    if category not in AUDIENCE_GUIDANCE_CATEGORIES:
        diagnostics.append(_diag(
            "invalid_change",
            "error",
            f"change.category must be one of {sorted(AUDIENCE_GUIDANCE_CATEGORIES)}.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    scope = change.get("scope")
    if scope not in AUDIENCE_GUIDANCE_SCOPES:
        diagnostics.append(_diag(
            "invalid_change",
            "error",
            f"change.scope must be one of {sorted(AUDIENCE_GUIDANCE_SCOPES)}.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    for diagnostic in validate_guidance_text(change.get("guidance_text")):
        diagnostics.append(_diag(
            diagnostic.code,
            diagnostic.severity,
            diagnostic.message,
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        ))
    return diagnostics


def _validate_source_evidence(
    evidence: Any,
    *,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    if not isinstance(evidence, list) or not evidence:
        return [_diag(
            "missing_source_evidence",
            "error",
            "source_evidence must be a non-empty list.",
            line_number=line_number,
            entry_id=entry_id,
            revision=revision,
        )]
    diagnostics: list[LedgerDiagnostic] = []
    for idx, item in enumerate(evidence):
        if not isinstance(item, dict):
            diagnostics.append(_diag(
                "invalid_source_evidence",
                "error",
                f"source_evidence[{idx}] must be an object.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
            continue
        source_type = item.get("source_type")
        if source_type not in ALLOWED_SOURCE_TYPES:
            diagnostics.append(_diag(
                "invalid_source_evidence",
                "error",
                f"source_evidence[{idx}].source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        elif source_type == "feedback_issue":
            for ref_key in ("issue_id", "run_id"):
                ref_value = item.get(ref_key)
                if not isinstance(ref_value, str) or not ref_value.strip():
                    diagnostics.append(_diag(
                        "invalid_source_evidence",
                        "error",
                        f"source_evidence[{idx}].{ref_key} is required for feedback_issue evidence.",
                        line_number=line_number,
                        entry_id=entry_id,
                        revision=revision,
                    ))
                    continue
                if len(ref_value) > MAX_SOURCE_REFERENCE_LENGTH:
                    diagnostics.append(_diag(
                        "invalid_source_evidence",
                        "error",
                        f"source_evidence[{idx}].{ref_key} is too long.",
                        line_number=line_number,
                        entry_id=entry_id,
                        revision=revision,
                    ))
                if "/" in ref_value or "\\" in ref_value:
                    diagnostics.append(_diag(
                        "invalid_source_evidence",
                        "error",
                        f"source_evidence[{idx}].{ref_key} must not contain path separators.",
                        line_number=line_number,
                        entry_id=entry_id,
                        revision=revision,
                    ))
                for diagnostic in _text_hygiene_diagnostics(
                    ref_value,
                    code="invalid_source_evidence",
                    label=f"source_evidence[{idx}].{ref_key}",
                    allow_single_sentence=True,
                ):
                    diagnostics.append(_diag(
                        diagnostic.code,
                        diagnostic.severity,
                        diagnostic.message,
                        line_number=line_number,
                        entry_id=entry_id,
                        revision=revision,
                    ))
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            diagnostics.append(_diag(
                "invalid_source_evidence",
                "error",
                f"source_evidence[{idx}].summary is required.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        elif len(summary) > MAX_EVIDENCE_SUMMARY_LENGTH:
            diagnostics.append(_diag(
                "invalid_source_evidence",
                "error",
                f"source_evidence[{idx}].summary is too long.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        else:
            for diagnostic in _text_hygiene_diagnostics(
                summary,
                code="invalid_source_evidence",
                label=f"source_evidence[{idx}].summary",
                allow_single_sentence=True,
            ):
                diagnostics.append(_diag(
                    diagnostic.code,
                    diagnostic.severity,
                    diagnostic.message,
                    line_number=line_number,
                    entry_id=entry_id,
                    revision=revision,
                ))
        origin = item.get("origin")
        if origin is not None and not isinstance(origin, dict):
            diagnostics.append(_diag(
                "invalid_origin",
                "error",
                f"source_evidence[{idx}].origin must be an object when present.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        elif isinstance(origin, dict):
            diagnostics.extend(_validate_origin(
                origin,
                prefix=f"source_evidence[{idx}].origin",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
    return diagnostics


def _validate_status_metadata(
    payload: dict[str, Any],
    *,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    status = payload.get("status")
    requirements = {
        "approved": [("approved_by", "text"), ("approved_at", "time")],
        "rejected": [("rejected_by", "text"), ("rejected_at", "time"), ("rejection_reason", "text")],
        "reverted": [("reverted_by", "text"), ("reverted_at", "time"), ("revert_reason", "text")],
    }
    diagnostics: list[LedgerDiagnostic] = []
    for field, field_type in requirements.get(str(status), []):
        value = payload.get(field)
        if field_type == "time":
            if not _is_utc_timestamp(value):
                diagnostics.append(_diag(
                    "missing_approval_metadata",
                    "error",
                    f"{field} is required and must be UTC ISO-like timestamp.",
                    line_number=line_number,
                    entry_id=entry_id,
                    revision=revision,
                ))
        elif not isinstance(value, str) or not value.strip():
            diagnostics.append(_diag(
                "missing_approval_metadata",
                "error",
                f"{field} is required.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        elif field.endswith("_by") and not _OPERATOR_ID_RE.match(value):
            diagnostics.append(_diag(
                "invalid_approval_metadata",
                "error",
                f"{field} must be a compact operator id.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        elif field.endswith("_reason"):
            if len(value) > MAX_APPROVAL_REASON_LENGTH:
                diagnostics.append(_diag(
                    "invalid_approval_metadata",
                    "error",
                    f"{field} is too long.",
                    line_number=line_number,
                    entry_id=entry_id,
                    revision=revision,
                ))
            for diagnostic in _text_hygiene_diagnostics(
                value,
                code="invalid_approval_metadata",
                label=field,
                allow_single_sentence=True,
            ):
                diagnostics.append(_diag(
                    diagnostic.code,
                    diagnostic.severity,
                    diagnostic.message,
                    line_number=line_number,
                    entry_id=entry_id,
                    revision=revision,
                ))
    return diagnostics


def _validate_origin(
    origin: dict[str, Any],
    *,
    prefix: str,
    line_number: int | None,
    entry_id: str | None,
    revision: int | None,
) -> list[LedgerDiagnostic]:
    diagnostics: list[LedgerDiagnostic] = []
    for key, value in origin.items():
        if key not in ALLOWED_ORIGIN_KEYS:
            diagnostics.append(_diag(
                "invalid_origin",
                "error",
                f"{prefix}.{key} is not an allowed origin field.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
            continue
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(_diag(
                "invalid_origin",
                "error",
                f"{prefix}.{key} must be a non-empty string.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
            continue
        if len(value) > MAX_ORIGIN_VALUE_LENGTH:
            diagnostics.append(_diag(
                "invalid_origin",
                "error",
                f"{prefix}.{key} is too long.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        if "/" in value or "\\" in value:
            diagnostics.append(_diag(
                "invalid_origin",
                "error",
                f"{prefix}.{key} must not contain path separators.",
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
        for diagnostic in _text_hygiene_diagnostics(
            value,
            code="invalid_origin",
            label=f"{prefix}.{key}",
            allow_single_sentence=True,
        ):
            diagnostics.append(_diag(
                diagnostic.code,
                diagnostic.severity,
                diagnostic.message,
                line_number=line_number,
                entry_id=entry_id,
                revision=revision,
            ))
    return diagnostics


def _validate_supersession_graph(current_entries: dict[str, dict[str, Any]]) -> list[LedgerDiagnostic]:
    diagnostics: list[LedgerDiagnostic] = []
    graph: dict[str, str] = {}
    for entry_id, entry in current_entries.items():
        target_id = supersedes_id(entry)
        if target_id is None:
            continue
        graph[entry_id] = target_id
        if target_id not in current_entries:
            diagnostics.append(_diag(
                "unknown_supersedes_reference",
                "error",
                f"supersedes_id references unknown entry {target_id}.",
                entry_id=entry_id,
                revision=entry.get("revision") if isinstance(entry.get("revision"), int) else None,
            ))

    for entry_id in sorted(graph):
        seen: set[str] = set()
        cursor = entry_id
        while cursor in graph:
            if cursor in seen:
                diagnostics.append(_diag(
                    "supersession_cycle",
                    "error",
                    f"supersession cycle detected at {cursor}.",
                    entry_id=entry_id,
                    revision=(
                        current_entries.get(entry_id, {}).get("revision")
                        if isinstance(current_entries.get(entry_id, {}).get("revision"), int)
                        else None
                    ),
                ))
                break
            seen.add(cursor)
            cursor = graph[cursor]
    return diagnostics


def _text_hygiene_diagnostics(
    text: str,
    *,
    code: str,
    label: str,
    allow_single_sentence: bool = False,
) -> list[LedgerDiagnostic]:
    diagnostics: list[LedgerDiagnostic] = []
    lower = text.lower()
    if "\n" in text or "\r" in text:
        diagnostics.append(_diag(code, "error", f"{label} must be a single paragraph."))
    if text.lstrip().startswith("#") or any(line.lstrip().startswith("#") for line in text.splitlines()):
        diagnostics.append(_diag(code, "error", f"{label} must not contain Markdown headings."))
    if "```" in text or "~~~" in text:
        diagnostics.append(_diag(code, "error", f"{label} must not contain code fences."))
    if "<!--" in text or "-->" in text:
        diagnostics.append(_diag(code, "error", f"{label} must not contain HTML comments."))
    if _CONTROL_CHAR_RE.search(text):
        diagnostics.append(_diag(code, "error", f"{label} must not contain control characters."))
    if any(fragment.lower() in lower for fragment in _FORBIDDEN_PATH_FRAGMENTS) or _WINDOWS_ABSOLUTE_RE.search(text):
        diagnostics.append(_diag(code, "error", f"{label} must not contain local absolute paths."))
    if _path_is_absolute_any_platform(text.strip()):
        diagnostics.append(_diag(code, "error", f"{label} must not be a local absolute path."))
    if any(pattern.search(text) for pattern in _TOKEN_PATTERNS):
        diagnostics.append(_diag(code, "error", f"{label} must not contain token-like strings."))
    if any(phrase in lower for phrase in _INJECTION_PHRASES):
        diagnostics.append(_diag(code, "error", f"{label} must not contain role or prompt-injection phrases."))
    if not allow_single_sentence and not text.strip():
        diagnostics.append(_diag(code, "error", f"{label} must not be empty."))
    return diagnostics


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _path_is_absolute_any_platform(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _non_empty_line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _dedupe_diagnostics(diagnostics: list[LedgerDiagnostic]) -> list[LedgerDiagnostic]:
    seen: set[tuple[Any, ...]] = set()
    result: list[LedgerDiagnostic] = []
    for diagnostic in diagnostics:
        key = (
            diagnostic.code,
            diagnostic.severity,
            diagnostic.message,
            diagnostic.line_number,
            diagnostic.entry_id,
            diagnostic.revision,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
    return result


def _diag(
    code: str,
    severity: str,
    message: str,
    *,
    line_number: int | None = None,
    entry_id: str | None = None,
    revision: int | None = None,
) -> LedgerDiagnostic:
    return LedgerDiagnostic(
        code=code,
        severity=severity,
        message=message,
        line_number=line_number,
        entry_id=entry_id,
        revision=revision,
    )
