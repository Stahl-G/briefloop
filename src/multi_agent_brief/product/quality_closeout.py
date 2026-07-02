from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

QUALITY_PANEL_CLOSEOUT_COMMAND = "briefloop quality summarize --workspace <workspace>"
QUALITY_PANEL_CLOSEOUT_BOUNDARY = (
    "post_finalize_quality_projection_only_not_gate_delivery_or_release_authority"
)
QUALITY_PANEL_CLOSEOUT_ARTIFACTS = (
    "output/intermediate/quality_panel.json",
    "output/intermediate/quality_summary.md",
    "output/intermediate/quality_panel.html",
)


def quality_panel_closeout_projection(
    *,
    workspace: str | Path | None = None,
    finalize_report: Mapping[str, Any] | None = None,
    generated_by_quality_summarize: bool = False,
    artifact_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the post-finalize Quality Panel closeout surface.

    The projection is advisory. It does not create gate, delivery, or release
    authority and it does not write the Quality Panel artifacts.
    """

    finalize_ready = _finalize_report_passed(finalize_report)
    status = "recommended"
    reason = "run_briefloop_quality_summarize_after_finalize"
    if not finalize_ready:
        status = "not_ready"
        reason = "finalize_report_not_passed"
    elif generated_by_quality_summarize:
        status = "generated"
        reason = "quality_summarize_generated_projection"

    present: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    if workspace is not None:
        ws = Path(workspace).expanduser().resolve()
        for artifact in QUALITY_PANEL_CLOSEOUT_ARTIFACTS:
            if (ws / artifact).exists():
                present.append(artifact)
            else:
                missing.append(artifact)
        if status == "recommended" and not missing:
            validation_reason = _quality_artifacts_validation_reason(ws)
            registry_reason = _quality_artifact_registry_reason(artifact_registry)
            if validation_reason is None and registry_reason is None:
                status = "complete"
                reason = "quality_projection_artifacts_valid"
            else:
                status = "stale_or_invalid"
                reason = validation_reason or registry_reason or "quality_projection_artifacts_invalid"
                invalid = list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS)

    return {
        "status": status,
        "reason": reason,
        "command": QUALITY_PANEL_CLOSEOUT_COMMAND,
        "artifacts": list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS),
        "present_artifacts": present,
        "missing_artifacts": missing,
        "invalid_artifacts": invalid,
        "audit_bundle": "included_when_present_and_valid",
        "delivery_bundle": "excluded",
        "runtime_effect": "operator_followup_only",
        "boundary": QUALITY_PANEL_CLOSEOUT_BOUNDARY,
        "gate_authority": False,
        "delivery_authority": False,
        "release_authority": False,
    }


def validate_quality_panel_closeout_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "quality_panel_closeout_schema_error:not_object"
    if payload.get("boundary") != QUALITY_PANEL_CLOSEOUT_BOUNDARY:
        return "quality_panel_closeout_schema_error:boundary"
    if payload.get("runtime_effect") != "operator_followup_only":
        return "quality_panel_closeout_schema_error:runtime_effect"
    if payload.get("status") not in {"not_ready", "recommended", "complete", "generated", "stale_or_invalid"}:
        return "quality_panel_closeout_schema_error:status"
    if payload.get("command") != QUALITY_PANEL_CLOSEOUT_COMMAND:
        return "quality_panel_closeout_schema_error:command"
    if payload.get("artifacts") != list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS):
        return "quality_panel_closeout_schema_error:artifacts"
    for field in ("present_artifacts", "missing_artifacts", "invalid_artifacts"):
        values = payload.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            return f"quality_panel_closeout_schema_error:{field}"
    if payload.get("audit_bundle") != "included_when_present_and_valid":
        return "quality_panel_closeout_schema_error:audit_bundle"
    if payload.get("delivery_bundle") != "excluded":
        return "quality_panel_closeout_schema_error:delivery_bundle"
    for field in ("gate_authority", "delivery_authority", "release_authority"):
        if payload.get(field) is not False:
            return f"quality_panel_closeout_schema_error:{field}"
    return None


def _finalize_report_passed(finalize_report: Mapping[str, Any] | None) -> bool:
    if not isinstance(finalize_report, Mapping):
        return False
    if str(finalize_report.get("status") or "").strip() != "pass":
        return False
    reader_clean = finalize_report.get("reader_clean")
    if isinstance(reader_clean, Mapping):
        return str(reader_clean.get("status") or "").strip() == "pass"
    return False


def _quality_artifact_registry_reason(artifact_registry: Mapping[str, Any] | None) -> str | None:
    if not isinstance(artifact_registry, Mapping):
        return "quality_projection_artifact_registry_missing"
    artifacts = artifact_registry.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return "quality_projection_artifact_registry_missing"
    for artifact_id in ("quality_panel", "quality_summary", "quality_panel_html"):
        record = artifacts.get(artifact_id)
        if not isinstance(record, Mapping):
            return f"quality_projection_artifact_registry_missing:{artifact_id}"
        if str(record.get("status") or "").strip() != "valid":
            return f"quality_projection_artifact_registry_not_valid:{artifact_id}"
    return None


def _quality_artifacts_validation_reason(workspace: Path) -> str | None:
    panel_path = workspace / "output" / "intermediate" / "quality_panel.json"
    summary_path = workspace / "output" / "intermediate" / "quality_summary.md"
    html_path = workspace / "output" / "intermediate" / "quality_panel.html"
    try:
        panel_payload = json.loads(panel_path.read_text(encoding="utf-8"))
    except OSError:
        return "quality_panel_unreadable"
    except UnicodeDecodeError:
        return "quality_panel_unreadable"
    except json.JSONDecodeError:
        return "quality_panel_parse_error"
    if not isinstance(panel_payload, dict):
        return "quality_panel_invalid:not_object"

    try:
        from multi_agent_brief.product.quality_panel import (
            render_quality_panel_html,
            render_quality_summary,
            validate_quality_panel_payload,
            validate_quality_panel_html,
            validate_quality_summary_markdown,
        )

        panel_reason = validate_quality_panel_payload(panel_payload)
        if panel_reason:
            return f"quality_panel_invalid:{panel_reason}"
        panel_sha256 = _sha256_file(panel_path)
        summary_text = summary_path.read_text(encoding="utf-8")
        html_text = html_path.read_text(encoding="utf-8")
        summary_reason = validate_quality_summary_markdown(summary_text)
        if summary_reason:
            return f"quality_summary_invalid:{summary_reason}"
        html_reason = validate_quality_panel_html(html_text)
        if html_reason:
            return f"quality_panel_html_invalid:{html_reason}"
        if summary_text != render_quality_summary(panel_payload, quality_panel_sha256=panel_sha256):
            return "quality_summary_stale_or_hand_edited"
        if html_text != render_quality_panel_html(panel_payload, quality_panel_sha256=panel_sha256):
            return "quality_panel_html_stale_or_hand_edited"
    except OSError:
        return "quality_projection_artifact_unreadable"
    except UnicodeDecodeError:
        return "quality_projection_artifact_unreadable"
    except Exception as exc:
        return f"quality_projection_artifact_invalid:{type(exc).__name__}"
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
