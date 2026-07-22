"""Submit path for the init web wizard: payload → InitProfile → one bootstrap.

The workspace is written through the SAME code path as CLI init
(``create_workspace`` → ``build_controlstore_bootstrap``) and initialized via
``initialize_or_open_runtime``; the response carries the real
TransactionReceipt.  Replay identity = request_id + canonical fingerprint of
the full request body.  Identical resubmit → ``replayed`` with the original
receipt and zero writes; same request_id with a different payload →
``submission_replay_conflict`` with zero writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import canonical_fingerprint
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.initialization import (
    WorkspaceBootstrap,
)
from multi_agent_brief.workspace.init_profile import InitProfile

SUBMISSION_SCHEMA = "briefloop.init_web.submission.v1"
_REQUIRED_SELECTION_KEYS = ("company", "industry_or_theme", "task_objective")


class SubmissionError(ValueError):
    """Typed submission rejection carrying an HTTP status and zero writes."""

    def __init__(self, error_code: str, http_status: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.http_status = http_status


def _runtime_submission_error(exc: RuntimeHostError) -> SubmissionError:
    error_code = str(exc)
    if error_code == "runtime_initialization_input_invalid":
        http_status = 422
    elif error_code in {
        "legacy_workspace_unsupported",
        "runtime_adapter_binding_mismatch",
    }:
        http_status = 409
    else:
        http_status = 500
    return SubmissionError(error_code, http_status)


def _require_text(value: Any, error_code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionError(error_code, 422)
    return value.strip()


def _require_text_list(value: Any, error_code: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise SubmissionError(error_code, 422)
    return [item.strip() for item in value]


def _profile_from_payload(payload: dict[str, Any]) -> InitProfile:
    selections = payload.get("selections")
    if not isinstance(selections, dict):
        raise SubmissionError("submission_payload_invalid", 422)
    for key in _REQUIRED_SELECTION_KEYS:
        _require_text(selections.get(key), f"submission_{key}_required")
    formats = selections.get("output_formats") or ["markdown"]
    company = _require_text(selections["company"], "submission_company_required")
    profile = InitProfile(
        interface_language=selections.get("interface_language") or "zh",
        output_language=selections.get("output_language") or "zh",
        company=company,
        industry=_require_text(
            selections["industry_or_theme"], "submission_industry_or_theme_required"
        ),
        brief_title=selections.get("brief_title") or f"{company} brief",
        task_objective=_require_text(
            selections["task_objective"], "submission_task_objective_required"
        ),
        audience=selections.get("audience") or "",
        audience_profile=selections.get("audience") or "",
        focus_areas=_require_text_list(
            selections.get("focus_areas") or ["general"],
            "submission_focus_areas_invalid",
        ),
        forbidden_sources=_require_text_list(
            selections.get("forbidden_sources") or [],
            "submission_forbidden_sources_invalid",
        ),
        cadence=selections.get("cadence") or "weekly",
        output_formats=_require_text_list(formats, "submission_output_formats_invalid"),
        web_search_mode=selections.get("web_search_mode") or "disabled",
        web_search_enabled=(selections.get("web_search_mode") or "disabled")
        != "disabled",
    )
    return profile


class InitWebSubmitter:
    """One-lifetime submission log with real replay/conflict semantics."""

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        adapter_loader: Callable[[str], Any] = load_codex_adapter_binding,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve() if base_dir else None
        self._adapter_loader = adapter_loader
        self._submissions: dict[str, tuple[str, dict[str, Any]]] = {}

    def _resolve_target(self, raw_target: str) -> Path:
        target = Path(raw_target).expanduser()
        if not target.is_absolute():
            target = (self._base_dir or Path.cwd()) / target
        resolved = target.resolve(strict=False)
        if resolved.exists() and any(resolved.iterdir()):
            raise SubmissionError("workspace_target_exists", 409)
        return resolved

    def submit(self, body: Any) -> tuple[int, dict[str, Any]]:
        if (
            not isinstance(body, dict)
            or body.get("schema_version") != SUBMISSION_SCHEMA
        ):
            raise SubmissionError("submission_payload_invalid", 422)
        request_id = _require_text(
            body.get("request_id"), "submission_request_id_invalid"
        )
        payload = body.get("payload")
        if not isinstance(payload, dict):
            raise SubmissionError("submission_payload_invalid", 422)
        fingerprint = canonical_fingerprint(body)

        prior = self._submissions.get(request_id)
        if prior is not None:
            prior_fingerprint, prior_response = prior
            if prior_fingerprint != fingerprint:
                raise SubmissionError("submission_replay_conflict", 409)
            try:
                WorkspaceBootstrap(
                    str(prior_response["workspace"])
                ).initialize_runnable_codex(
                    expected_adapter_loader=self._adapter_loader
                )
            except RuntimeHostError as exc:
                raise _runtime_submission_error(exc) from exc
            return 200, {**prior_response, "status": "replayed"}

        if payload.get("human_confirmation") is not True:
            raise SubmissionError("human_confirmation_required", 422)
        target = self._resolve_target(
            _require_text(payload.get("workspace_target"), "workspace_target_invalid")
        )
        profile = _profile_from_payload(payload)

        identity_log: list[str] = []

        def _identity() -> str:
            import uuid

            value = uuid.uuid4().hex
            identity_log.append(value)
            return value

        create_workspace(target, profile, force=False, identity_factory=_identity)
        workspace_id, run_id = f"WS-{identity_log[0]}", f"RUN-{identity_log[1]}"
        try:
            initialized = WorkspaceBootstrap(target).initialize_runnable_codex(
                expected_adapter_loader=self._adapter_loader
            )
        except RuntimeHostError as exc:
            raise _runtime_submission_error(exc) from exc
        receipt_id = derived_id("REQ-CX-INIT", workspace_id, run_id)
        with SQLiteControlStore.open(target / "briefloop.db") as store:
            receipt = store.load_transaction_receipt(run_id, receipt_id)
        if receipt is None:
            raise SubmissionError("bootstrap_receipt_unavailable", 500)
        response = {
            "ok": True,
            "status": "committed" if initialized.initialized else "replayed",
            "workspace_id": workspace_id,
            "run_id": run_id,
            "workspace": str(target),
            "transaction_id": receipt.transaction_id,
            "committed_revision": receipt.committed_revision,
            "receipt": receipt.model_dump(mode="json", exclude_unset=False),
        }
        self._submissions[request_id] = (fingerprint, response)
        return 200, response


__all__ = ["SUBMISSION_SCHEMA", "InitWebSubmitter", "SubmissionError"]
