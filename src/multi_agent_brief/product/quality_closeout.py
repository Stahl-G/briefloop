from __future__ import annotations

import hashlib
import json
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Mapping, Union, cast

if TYPE_CHECKING:
    from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
        RegistryReadVerdict,
    )

QUALITY_PANEL_CLOSEOUT_COMMAND = "briefloop quality summarize --workspace <workspace>"
QUALITY_PANEL_CLOSEOUT_BOUNDARY = (
    "post_finalize_quality_projection_only_not_gate_delivery_or_release_authority"
)
QUALITY_PANEL_CLOSEOUT_ARTIFACTS = (
    "output/intermediate/quality_panel.json",
    "output/intermediate/quality_summary.md",
    "output/intermediate/quality_panel.html",
)
QUALITY_PANEL_CLOSEOUT_ARTIFACT_IDS = (
    "quality_panel",
    "quality_summary",
    "quality_panel_html",
)
QUALITY_PANEL_BROWSER_BOUNDARY = "display_only_not_runtime_or_quality_authority"


@dataclass(frozen=True)
class QualityPanelNotMaterialized:
    """A legal all-absent Quality Panel state carrying no artifact values."""

    kind: Literal["not_materialized"] = "not_materialized"
    reason_code: Literal["quality_panel_not_materialized"] = (
        "quality_panel_not_materialized"
    )


@dataclass(frozen=True)
class QualityPanelDegradation:
    """A value-free verdict for an incomplete or unbound Quality Panel."""

    reason_code: str
    kind: Literal["degradation"] = "degradation"


@dataclass(frozen=True)
class CanonicalQualityPanelView:
    """The only read result allowed to expose Quality Panel artifact values."""

    run_id: str
    artifact_paths: Mapping[str, Path]
    artifact_sha256: Mapping[str, str]
    registry_records: Mapping[str, Mapping[str, Any]]
    panel_payload: Mapping[str, Any]
    kind: Literal["canonical"] = "canonical"


QualityPanelReadVerdict = Union[
    CanonicalQualityPanelView,
    QualityPanelNotMaterialized,
    QualityPanelDegradation,
]


class QualityPanelCloseoutError(RuntimeError):
    """Raised when deterministic Quality Panel closeout cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = dict(details or {})


def materialize_quality_panel_closeout(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    actor: str = "cli",
) -> dict[str, Any]:
    """Write and register the deterministic post-finalize Quality Panel projection."""

    ws = Path(workspace).expanduser().resolve()
    try:
        from multi_agent_brief.product.quality_panel import (
            quality_panel_html_path,
            quality_panel_path,
            quality_summary_path,
            write_quality_panel,
            write_quality_panel_html,
            write_quality_summary,
        )

        panel = write_quality_panel(workspace=ws)
        write_quality_summary(workspace=ws, panel_payload=panel)
        write_quality_panel_html(workspace=ws, panel_payload=panel)
    except Exception as exc:
        raise QualityPanelCloseoutError(
            "Quality Panel projection artifacts could not be generated.",
            reason_code="quality_projection_generation_failed",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        ) from exc

    artifact_paths = {
        "quality_panel": quality_panel_path(ws),
        "quality_summary": quality_summary_path(ws),
        "quality_panel_html": quality_panel_html_path(ws),
    }
    artifact_results = {
        artifact_id: {
            "path": _workspace_relative(ws, path),
            "sha256": _sha256_file(path),
        }
        for artifact_id, path in artifact_paths.items()
    }

    try:
        refresh_result = _refresh_runtime_state(
            workspace=ws,
            repo_workdir=repo_workdir,
            actor=actor,
        )
    except Exception as exc:
        raise QualityPanelCloseoutError(
            "Quality Panel artifacts were written but Artifact Registry refresh failed.",
            reason_code="quality_projection_registry_refresh_failed",
            details={
                "artifacts": artifact_results,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc

    quality_view = interpret_quality_panel_closeout(
        workspace=ws,
        repo_workdir=repo_workdir,
    )
    if (
        not isinstance(quality_view, CanonicalQualityPanelView)
        and _quality_panel_requires_registry_reprojection(refresh_result)
    ):
        try:
            panel = write_quality_panel(workspace=ws)
            write_quality_summary(workspace=ws, panel_payload=panel)
            write_quality_panel_html(workspace=ws, panel_payload=panel)
            artifact_results = {
                artifact_id: {
                    "path": _workspace_relative(ws, path),
                    "sha256": _sha256_file(path),
                }
                for artifact_id, path in artifact_paths.items()
            }
            _refresh_runtime_state(
                workspace=ws,
                repo_workdir=repo_workdir,
                actor=actor,
            )
        except Exception as exc:
            raise QualityPanelCloseoutError(
                "Quality Panel projection could not be rebound to the refreshed Artifact Registry.",
                reason_code="quality_projection_registry_refresh_failed",
                details={
                    "artifacts": artifact_results,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            ) from exc
        quality_view = interpret_quality_panel_closeout(
            workspace=ws,
            repo_workdir=repo_workdir,
        )
    if not isinstance(quality_view, CanonicalQualityPanelView):
        raise QualityPanelCloseoutError(
            "Quality Panel artifacts were written but are not valid in Artifact Registry.",
            reason_code="quality_projection_registry_binding_invalid",
            details={
                "artifacts": artifact_results,
                "quality_panel_reason_code": quality_view.reason_code,
            },
        )

    registry_results: dict[str, dict[str, Any]] = {}
    for artifact_id in QUALITY_PANEL_CLOSEOUT_ARTIFACT_IDS:
        record = quality_view.registry_records[artifact_id]
        expected_sha256 = artifact_results[artifact_id]["sha256"]
        actual_sha256 = quality_view.artifact_sha256[artifact_id]
        if actual_sha256 != expected_sha256:
            raise QualityPanelCloseoutError(
                "Quality Panel Artifact Registry hash does not bind the generated bytes.",
                reason_code="quality_projection_registry_hash_mismatch",
                details={
                    "artifact_id": artifact_id,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                },
            )
        registry_results[artifact_id] = {
            "status": str(record.get("status") or ""),
            "sha256": actual_sha256,
            "validation_result": str(record.get("validation_result") or ""),
        }

    return {
        "status": "complete",
        "reason_code": "quality_projection_materialized",
        "workspace": str(ws),
        "artifacts": artifact_results,
        "registry_refresh": {
            "status": "complete",
            "artifacts": registry_results,
        },
        "overall_status": panel.get("overall_status"),
        "recommended_actions": list(panel.get("recommended_actions") or []),
        "repair_command": QUALITY_PANEL_CLOSEOUT_COMMAND,
        "boundary": QUALITY_PANEL_CLOSEOUT_BOUNDARY,
    }


def interpret_quality_panel_closeout(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None = None,
    registry_verdict: "RegistryReadVerdict | None" = None,
) -> QualityPanelReadVerdict:
    """Return one total, read-only verdict for the three Quality Panel artifacts."""

    try:
        return _interpret_quality_panel_closeout(
            workspace=workspace,
            repo_workdir=repo_workdir,
            registry_verdict=registry_verdict,
        )
    except Exception:
        return QualityPanelDegradation("quality_panel_interpretation_failed")


def _interpret_quality_panel_closeout(
    *,
    workspace: str | Path,
    repo_workdir: str | Path | None,
    registry_verdict: "RegistryReadVerdict | None",
) -> QualityPanelReadVerdict:
    from multi_agent_brief.orchestrator.runtime_state.artifact_registry_read import (
        CanonicalRegistryView,
        RegistryDegradation,
        RegistryNotMaterialized,
        RegistrySnapshotDrift,
        interpret_artifact_registry,
    )
    from multi_agent_brief.product.quality_panel import (
        render_quality_panel_html,
        render_quality_summary,
        validate_quality_panel_html,
        validate_quality_panel_payload,
        validate_quality_summary_markdown,
    )

    try:
        ws = Path(workspace).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return QualityPanelDegradation("quality_panel_workspace_invalid")

    expected_paths = {
        artifact_id: ws / relative_path
        for artifact_id, relative_path in zip(
            QUALITY_PANEL_CLOSEOUT_ARTIFACT_IDS,
            QUALITY_PANEL_CLOSEOUT_ARTIFACTS,
        )
    }
    existing = {artifact_id: path.exists() for artifact_id, path in expected_paths.items()}
    if registry_verdict is None:
        registry_verdict = interpret_artifact_registry(
            workspace=ws,
            repo_workdir=repo_workdir,
        )
    if isinstance(registry_verdict, RegistryNotMaterialized):
        return QualityPanelDegradation("quality_panel_registry_not_materialized")
    if isinstance(registry_verdict, RegistrySnapshotDrift):
        return QualityPanelDegradation("quality_panel_registry_snapshot_drift")
    if isinstance(registry_verdict, RegistryDegradation):
        return QualityPanelDegradation("quality_panel_registry_degradation")
    if not isinstance(registry_verdict, CanonicalRegistryView):
        return QualityPanelDegradation("quality_panel_registry_verdict_invalid")

    if not any(existing.values()):
        for artifact_id, relative_path in zip(
            QUALITY_PANEL_CLOSEOUT_ARTIFACT_IDS,
            QUALITY_PANEL_CLOSEOUT_ARTIFACTS,
        ):
            record = registry_verdict.records.get(artifact_id)
            resolved_path = registry_verdict.resolved_paths.get(artifact_id)
            if not isinstance(record, Mapping) or not isinstance(resolved_path, Path):
                return QualityPanelDegradation("quality_panel_registry_record_missing")
            if record.get("artifact_id") != artifact_id or record.get("path") != relative_path:
                return QualityPanelDegradation("quality_panel_registry_identity_mismatch")
            if resolved_path.resolve() != expected_paths[artifact_id].resolve():
                return QualityPanelDegradation("quality_panel_registry_path_mismatch")
            if (
                record.get("status") != "expected"
                or record.get("validation_result") != "not_checked"
                or record.get("sha256") is not None
            ):
                return QualityPanelDegradation("quality_panel_absence_not_registry_bound")
        return QualityPanelNotMaterialized()
    if not all(existing.values()) or not all(path.is_file() for path in expected_paths.values()):
        return QualityPanelDegradation("quality_panel_artifact_set_incomplete")

    records: dict[str, Mapping[str, Any]] = {}
    hashes: dict[str, str] = {}
    for artifact_id, relative_path in zip(
        QUALITY_PANEL_CLOSEOUT_ARTIFACT_IDS,
        QUALITY_PANEL_CLOSEOUT_ARTIFACTS,
    ):
        record = registry_verdict.records.get(artifact_id)
        resolved_path = registry_verdict.resolved_paths.get(artifact_id)
        if not isinstance(record, Mapping) or not isinstance(resolved_path, Path):
            return QualityPanelDegradation("quality_panel_registry_record_missing")
        if record.get("artifact_id") != artifact_id or record.get("path") != relative_path:
            return QualityPanelDegradation("quality_panel_registry_identity_mismatch")
        if resolved_path.resolve() != expected_paths[artifact_id].resolve():
            return QualityPanelDegradation("quality_panel_registry_path_mismatch")
        if record.get("status") != "valid":
            return QualityPanelDegradation("quality_panel_registry_record_not_valid")
        actual_sha256 = _sha256_file(expected_paths[artifact_id])
        if record.get("sha256") != actual_sha256:
            return QualityPanelDegradation("quality_panel_registry_hash_mismatch")
        records[artifact_id] = record
        hashes[artifact_id] = actual_sha256

    panel_path = expected_paths["quality_panel"]
    try:
        panel_payload = json.loads(panel_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return QualityPanelDegradation("quality_panel_unreadable")
    except json.JSONDecodeError:
        return QualityPanelDegradation("quality_panel_parse_error")
    if not isinstance(panel_payload, dict):
        return QualityPanelDegradation("quality_panel_payload_invalid")
    if validate_quality_panel_payload(panel_payload) is not None:
        return QualityPanelDegradation("quality_panel_payload_invalid")

    try:
        summary_text = expected_paths["quality_summary"].read_text(encoding="utf-8")
        html_text = expected_paths["quality_panel_html"].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return QualityPanelDegradation("quality_panel_projection_unreadable")
    if validate_quality_summary_markdown(summary_text) is not None:
        return QualityPanelDegradation("quality_summary_payload_invalid")
    if validate_quality_panel_html(html_text) is not None:
        return QualityPanelDegradation("quality_panel_html_payload_invalid")
    panel_sha256 = hashes["quality_panel"]
    if summary_text != render_quality_summary(
        panel_payload,
        quality_panel_sha256=panel_sha256,
    ):
        return QualityPanelDegradation("quality_summary_binding_invalid")
    if html_text != render_quality_panel_html(
        panel_payload,
        quality_panel_sha256=panel_sha256,
    ):
        return QualityPanelDegradation("quality_panel_html_binding_invalid")

    return CanonicalQualityPanelView(
        run_id=registry_verdict.run_id,
        artifact_paths=MappingProxyType(dict(expected_paths)),
        artifact_sha256=MappingProxyType(hashes),
        registry_records=MappingProxyType(records),
        panel_payload=cast(Mapping[str, Any], _freeze_json(panel_payload)),
    )


def display_quality_panel_closeout(
    materialization: Mapping[str, Any],
    *,
    as_json: bool,
    is_interactive: bool,
) -> dict[str, Any]:
    """Open a verified Quality Panel in the default browser for interactive CLI use."""

    workspace = Path(str(materialization.get("workspace") or "")).expanduser().resolve()
    artifacts = materialization.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, Mapping) else {}
    html_record = artifacts.get("quality_panel_html")
    html_record = html_record if isinstance(html_record, Mapping) else {}
    relative_path = str(html_record.get("path") or "")
    html_path = (workspace / relative_path).resolve() if relative_path else workspace
    payload = {
        "status": "skipped",
        "reason_code": "quality_panel_browser_not_requested",
        "path": relative_path,
        "url": html_path.as_uri(),
        "boundary": QUALITY_PANEL_BROWSER_BOUNDARY,
    }
    if as_json:
        payload["reason_code"] = "quality_panel_browser_suppressed_for_json"
        return payload
    if not is_interactive:
        payload["reason_code"] = "quality_panel_browser_suppressed_for_non_interactive_output"
        return payload
    try:
        html_path.relative_to(workspace)
    except ValueError:
        payload.update(
            status="warning",
            reason_code="quality_panel_browser_path_outside_workspace",
        )
        return payload
    if not html_path.is_file():
        payload.update(
            status="warning",
            reason_code="quality_panel_browser_artifact_missing",
        )
        return payload
    try:
        opened = webbrowser.open(html_path.as_uri(), new=2)
    except Exception as exc:
        payload.update(
            status="warning",
            reason_code="quality_panel_browser_open_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return payload
    if not opened:
        payload.update(
            status="warning",
            reason_code="quality_panel_browser_open_rejected",
        )
        return payload
    payload.update(
        status="opened",
        reason_code="quality_panel_opened_in_default_browser",
    )
    return payload


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
        verdict = interpret_quality_panel_closeout(workspace=workspace)
        if isinstance(verdict, CanonicalQualityPanelView):
            present = list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS)
            if status == "recommended":
                status = "complete"
                reason = "quality_projection_artifacts_valid"
        elif isinstance(verdict, QualityPanelNotMaterialized):
            missing = list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS)
        else:
            invalid = list(QUALITY_PANEL_CLOSEOUT_ARTIFACTS)
            if status == "recommended":
                status = "stale_or_invalid"
                reason = verdict.reason_code

    # Kept for API compatibility; raw Registry mappings are never interpreted here.
    _ = artifact_registry

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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _refresh_runtime_state(
    *,
    workspace: Path,
    repo_workdir: str | Path | None,
    actor: str,
) -> dict[str, Any]:
    from multi_agent_brief.orchestrator.runtime_state.lifecycle import check_runtime_state

    return check_runtime_state(
        workspace=workspace,
        repo_workdir=repo_workdir,
        actor=actor,
    )


def _quality_panel_requires_registry_reprojection(
    refresh_result: Mapping[str, Any],
) -> bool:
    registry = refresh_result.get("artifact_registry")
    registry = registry if isinstance(registry, Mapping) else {}
    records = registry.get("artifacts")
    records = records if isinstance(records, Mapping) else {}
    panel_record = records.get("quality_panel")
    return (
        isinstance(panel_record, Mapping)
        and panel_record.get("status") == "invalid"
        and panel_record.get("validation_result")
        == "quality_panel_validation_error:producer_replay_mismatch"
    )


def _workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()
