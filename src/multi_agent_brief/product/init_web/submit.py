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

from dataclasses import asdict
from pathlib import Path
import re
from threading import Lock, RLock
from typing import Any, Callable

from pydantic import ValidationError

from multi_agent_brief.cli.init_wizard import create_workspace
from multi_agent_brief.cli.secrets_commands import (
    SecretImportError,
    store_workspace_secret,
)
from multi_agent_brief.core.env import get_known_env_value
from multi_agent_brief.contracts.v2 import (
    ExecutionSourceManifest,
    RunExecutionAuthorizationBootstrap,
    RunSourceDiscoveryAuthorizationBootstrap,
)
from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.control_store.serialization import (
    canonical_fingerprint,
    canonical_json_bytes,
    sha256_hex,
)
from multi_agent_brief.core_run_v2.policy import derived_id
from multi_agent_brief.runtime_host_v2.codex import load_codex_adapter_binding
from multi_agent_brief.runtime_host_v2.initialization import (
    RuntimeHostError,
    WorkspaceBootstrap,
)
from multi_agent_brief.core_run_v2.output_contract import resolve_output_extent
from multi_agent_brief.product.workspace_hygiene import (
    NestedWorkspaceTargetError,
    canonical_workspace_target,
)
from multi_agent_brief.workspace.init_profile import InitProfile

from .staging import InitWebStaging, InitWebStagingError

SUBMISSION_SCHEMA = "briefloop.init_web.submission.v1"
_REQUIRED_SELECTION_KEYS = ("company", "industry_or_theme", "task_objective")
_MAX_SEARCH_DOMAINS = 20
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


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
    elif error_code == "runtime_adapter_binding_mismatch":
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


def _canonical_search_domains(value: Any) -> list[str]:
    if type(value) is not list or len(value) > _MAX_SEARCH_DOMAINS:
        raise SubmissionError("submission_search_domains_invalid", 422)
    domains: list[str] = []
    for item in value:
        if type(item) is not str:
            raise SubmissionError("submission_search_domains_invalid", 422)
        domain = item.strip().lower()
        try:
            domain.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            raise SubmissionError("submission_search_domains_invalid", 422) from None
        labels = domain.split(".")
        if (
            not domain
            or len(domain) > 253
            or len(labels) < 2
            or re.fullmatch(r"[0-9.]+", domain) is not None
            or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
        ):
            raise SubmissionError("submission_search_domains_invalid", 422)
        domains.append(domain)
    if len(domains) != len(set(domains)):
        raise SubmissionError("submission_search_domains_invalid", 422)
    return sorted(domains)


def _profile_from_payload(payload: dict[str, Any]) -> InitProfile:
    selections = payload.get("selections")
    if not isinstance(selections, dict):
        raise SubmissionError("submission_payload_invalid", 422)
    for key in _REQUIRED_SELECTION_KEYS:
        _require_text(selections.get(key), f"submission_{key}_required")
    formats = selections.get("output_formats") or ["markdown"]
    company = _require_text(selections["company"], "submission_company_required")
    report_type = _require_text(
        selections.get("report_type"), "submission_report_type_required"
    )
    output_language = selections.get("output_language") or "zh"
    output_extent = selections.get("output_extent")
    if output_extent not in {"compact", "balanced", "detailed"}:
        raise SubmissionError("submission_output_extent_invalid", 422)
    try:
        resolve_output_extent(output_extent, str(output_language))
    except ValueError as exc:
        raise SubmissionError("submission_output_extent_invalid", 422) from exc
    web_search_mode = selections.get("web_search_mode") or "disabled"
    if web_search_mode not in {"disabled", "external_api"}:
        raise SubmissionError("submission_web_search_mode_invalid", 422)
    search_backend = selections.get("search_backend") or ""
    if web_search_mode == "external_api" and search_backend != "tavily":
        raise SubmissionError("submission_search_backend_invalid", 422)
    if web_search_mode == "disabled" and search_backend:
        raise SubmissionError("submission_search_backend_invalid", 422)
    search_domains = _canonical_search_domains(selections.get("search_domains", []))
    if web_search_mode != "external_api" and search_domains:
        raise SubmissionError("submission_search_domains_invalid", 422)
    max_source_age_days = selections.get("max_source_age_days")
    if max_source_age_days is None:
        if web_search_mode == "external_api":
            raise SubmissionError("submission_report_window_invalid", 422)
        max_source_age_days = 14
    elif type(max_source_age_days) is not int or max_source_age_days not in {7, 30}:
        raise SubmissionError("submission_report_window_invalid", 422)
    source_profile = selections.get("source_profile") or (
        "llm_decide" if web_search_mode == "external_api" else "conservative"
    )
    if source_profile not in {
        "conservative",
        "research",
        "aggressive_signal",
        "custom",
        "llm_decide",
    }:
        raise SubmissionError("submission_source_profile_invalid", 422)
    profile = InitProfile(
        interface_language=selections.get("interface_language") or "zh",
        output_language=output_language,
        company=company,
        industry=_require_text(
            selections["industry_or_theme"], "submission_industry_or_theme_required"
        ),
        brief_title=selections.get("brief_title") or f"{company} brief",
        report_type=report_type,
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
        max_source_age_days=max_source_age_days,
        output_formats=_require_text_list(formats, "submission_output_formats_invalid"),
        source_profile=source_profile,
        web_search_mode=web_search_mode,
        web_search_enabled=web_search_mode != "disabled",
        search_backend=search_backend,
        tavily_enabled=search_backend == "tavily",
        preferred_news_domains=search_domains,
        output_extent=output_extent,
    )
    return profile


def preview_output_contract(body: Any) -> dict[str, object]:
    """Resolve only an init-web semantic extent; this path never writes state."""

    if not isinstance(body, dict) or set(body) != {"output_extent", "output_language"}:
        raise SubmissionError("submission_output_extent_invalid", 422)
    output_extent = body.get("output_extent")
    output_language = body.get("output_language")
    if not isinstance(output_extent, str) or not isinstance(output_language, str):
        raise SubmissionError("submission_output_extent_invalid", 422)
    try:
        resolved = resolve_output_extent(output_extent, output_language)
    except ValueError as exc:
        raise SubmissionError("submission_output_extent_invalid", 422) from exc
    return {
        "ok": True,
        "output_extent": resolved.output_extent,
        "extent_catalog_id": resolved.extent_catalog_id,
        "body_length_basis": resolved.body_length_basis,
        "body_length_unit": resolved.body_length_unit,
        "resolved_minimum": resolved.resolved_minimum,
        "resolved_maximum": resolved.resolved_maximum,
    }


class InitWebSubmitter:
    """Submit one strict init request through durable Store replay semantics."""

    _target_locks_guard = Lock()
    _target_locks: dict[str, RLock] = {}

    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        adapter_loader: Callable[[str], Any] = load_codex_adapter_binding,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve() if base_dir else None
        self._adapter_loader = adapter_loader
        self._staging = InitWebStaging()
        self._search_secret_lock = Lock()
        self._pending_search_secrets: dict[str, str] = {}

    @classmethod
    def _target_lock(cls, target: Path) -> RLock:
        key = str(target)
        with cls._target_locks_guard:
            return cls._target_locks.setdefault(key, RLock())

    def close(self) -> None:
        """Remove only inert host-private staging bytes."""

        with self._search_secret_lock:
            self._pending_search_secrets.clear()
        self._staging.close()

    def configure_search_secret(
        self,
        *,
        session_id: str,
        body: Any,
    ) -> dict[str, object]:
        """Retain one Tavily key in memory until its workspace is created."""

        if (
            not isinstance(body, dict)
            or set(body) != {"provider", "api_key"}
            or body.get("provider") != "tavily"
        ):
            raise SubmissionError("submission_search_secret_invalid", 422)
        api_key = body.get("api_key")
        if (
            not isinstance(api_key, str)
            or not 8 <= len(api_key) <= 512
            or any(character.isspace() for character in api_key)
        ):
            raise SubmissionError("submission_search_secret_invalid", 422)
        with self._search_secret_lock:
            self._pending_search_secrets[session_id] = api_key
        return {
            "ok": True,
            "provider": "tavily",
            "api_key_env": "TAVILY_API_KEY",
            "configured": True,
        }

    def _search_secret(
        self,
        *,
        session_id: str,
        target: Path,
        profile: InitProfile,
    ) -> str | None:
        if profile.web_search_mode != "external_api":
            return None
        with self._search_secret_lock:
            pending = self._pending_search_secrets.get(session_id, "")
        value = pending or get_known_env_value("TAVILY_API_KEY", target)
        if not value:
            raise SubmissionError("submission_search_api_key_required", 422)
        return value

    def _apply_search_secret_effect(
        self,
        *,
        session_id: str,
        target: Path,
        replayed: bool,
    ) -> str:
        """Persist only after Store replay/initialization has already verified."""

        with self._search_secret_lock:
            pending = self._pending_search_secrets.get(session_id, "")
        if get_known_env_value("TAVILY_API_KEY", target):
            return "ready"
        if not pending:
            return "required"
        try:
            store_workspace_secret(
                workspace=target,
                key="TAVILY_API_KEY",
                value=pending,
            )
        except SecretImportError:
            return "pending"
        self._forget_search_secret(session_id)
        return "recovered" if replayed else "ready"

    def _forget_search_secret(self, session_id: str) -> None:
        with self._search_secret_lock:
            self._pending_search_secrets.pop(session_id, None)

    @staticmethod
    def _with_search_discovery(
        response: dict[str, Any],
        *,
        profile: InitProfile,
    ) -> dict[str, Any]:
        if (
            profile.web_search_mode == "external_api"
            and profile.search_backend == "tavily"
            and response.get("source_discovery_authorized") is True
        ):
            response["source_discovery"] = {
                "mode": "pre_provider_authorization",
                "profile": "llm_decide",
                "backend": "tavily",
                "api_key_env": "TAVILY_API_KEY",
            }
        return response

    def stage_upload(
        self,
        *,
        session_id: str,
        filename: str,
        stream,
        declared_length: int,
    ) -> dict[str, object]:
        try:
            staged = self._staging.stage(
                session_id=session_id,
                filename=filename,
                stream=stream,
                declared_length=declared_length,
            )
        except InitWebStagingError as exc:
            raise SubmissionError(str(exc), 422) from exc
        return {
            "ok": True,
            "upload_handle": staged.handle,
            "filename": staged.filename,
            "byte_count": staged.byte_count,
            "sha256": staged.sha256,
        }

    def preview_source_manifest(
        self,
        *,
        session_id: str,
        body: Any,
    ) -> dict[str, object]:
        """Strictly canonicalize and reverify a Human-reviewable source set."""

        if not isinstance(body, dict) or set(body) != {
            "source_manifest_mode",
            "source_metadata",
            "upload_bindings",
        }:
            raise SubmissionError("submission_source_manifest_invalid", 422)
        try:
            confirmed, ordered_uploads, ordered_metadata = (
                self._staging.canonical_manifest_details(
                    session_id=session_id,
                    mode=body.get("source_manifest_mode"),
                    source_metadata=body.get("source_metadata"),
                    upload_bindings=body.get("upload_bindings"),
                )
            )
        except InitWebStagingError as exc:
            raise SubmissionError(str(exc), 422) from exc
        canonical = confirmed.model_dump(mode="json", exclude_unset=False)
        return {
            "ok": True,
            "source_manifest": canonical,
            "source_manifest_sha256": sha256_hex(canonical_json_bytes(canonical)),
            "member_count": len(confirmed.members),
            "source_metadata": list(ordered_metadata),
            "routing_bindings": [
                {
                    "metadata_index": index,
                    "upload_handle": staged.handle,
                }
                for index, staged in enumerate(ordered_uploads)
            ],
            "source_preview": [
                {
                    "source_id": member.source_id,
                    "title": member.title,
                    "publisher": member.publisher,
                    "published_at": member.published_at,
                    "original_url": (
                        str(member.locator.url)
                        if member.locator.kind == "web"
                        else None
                    ),
                    "document_kind": member.document_kind,
                    "opened_at": member.opened_at,
                    "resolved_at": member.resolved_at,
                    "content_media_type": member.content_media_type,
                    "observed_filename": staged.filename,
                    "observed_sha256": staged.sha256,
                    "byte_count": staged.byte_count,
                }
                for member, staged in zip(
                    confirmed.members, ordered_uploads, strict=True
                )
            ],
        }

    def _resolve_target(self, raw_target: str) -> Path:
        target = Path(raw_target).expanduser()
        if not target.is_absolute():
            target = (self._base_dir or Path.cwd()) / target
        try:
            return canonical_workspace_target(target.absolute())
        except NestedWorkspaceTargetError as exc:
            raise SubmissionError("workspace_target_nested", 409) from exc

    @staticmethod
    def _submission_identities(
        request_id: str,
        request_fingerprint: str,
    ) -> tuple[str, str, str]:
        request_namespace = canonical_fingerprint(
            {
                "schema_version": SUBMISSION_SCHEMA,
                "request_id": request_id,
            }
        )
        identity_suffix = f"INITWEB-{request_namespace}-{request_fingerprint}"
        return (
            f"WS-{identity_suffix}",
            f"RUN-{identity_suffix}",
            f"WS-INITWEB-{request_namespace}-",
        )

    @staticmethod
    def _target_has_content(target: Path) -> bool:
        if not target.exists():
            return False
        if not target.is_dir():
            return True
        try:
            next(target.iterdir())
        except StopIteration:
            return False
        except OSError:
            return True
        return True

    def _receipt_response(
        self,
        *,
        target: Path,
        workspace_id: str,
        run_id: str,
        status: str,
        search_secret_status: str | None = None,
    ) -> dict[str, Any]:
        receipt_id = derived_id("REQ-CX-INIT", workspace_id, run_id)
        with SQLiteControlStore.open(target / "briefloop.db") as store:
            receipt = store.load_transaction_receipt(run_id, receipt_id)
        if receipt is None:
            raise SubmissionError("bootstrap_receipt_unavailable", 500)
        initialized = WorkspaceBootstrap(target).initialize_runnable_codex(
            expected_adapter_loader=self._adapter_loader
        )
        authorizations = initialized.verified.snapshot.run_execution_authorizations
        discovery_authorizations = (
            initialized.verified.snapshot.run_source_discovery_authorizations
        )
        if len(authorizations) > 1:
            raise SubmissionError("control_store_integrity_invalid", 500)
        if len(discovery_authorizations) > 1:
            raise SubmissionError("control_store_integrity_invalid", 500)
        authorization = authorizations[0] if authorizations else None
        discovery_authorization = (
            discovery_authorizations[0] if discovery_authorizations else None
        )
        completion_authorization = authorization or discovery_authorization
        response = {
            "ok": True,
            "status": status,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "workspace": str(target),
            "transaction_id": receipt.transaction_id,
            "committed_revision": receipt.committed_revision,
            "receipt": receipt.model_dump(mode="json", exclude_unset=False),
            "next_action": initialized.action.model_dump(
                mode="json", exclude_unset=False
            ),
            "progress": {
                "store_revision": initialized.verified.snapshot.store_revision,
                "current_stage": initialized.action.stage_id,
                "current_role": initialized.action.role_id,
                "reason_code": initialized.action.reason_code,
            },
            "next_command": f"briefloop runtime continue --workspace {target}",
            "execution_authorized": authorization is not None,
            "source_discovery_authorized": discovery_authorization is not None,
            "completion_target": (
                completion_authorization.completion_target
                if completion_authorization is not None
                else None
            ),
            "repair_budget": (
                completion_authorization.repair_budget
                if completion_authorization is not None
                else None
            ),
        }
        if search_secret_status is not None:
            response["search_secret_status"] = search_secret_status
        return response

    @staticmethod
    def _semantic_submission(
        *,
        request_id: str,
        profile: InitProfile,
        payload: dict[str, Any],
    ) -> tuple[
        str,
        ExecutionSourceManifest | None,
        RunExecutionAuthorizationBootstrap | None,
        RunSourceDiscoveryAuthorizationBootstrap | None,
    ]:
        raw_manifest = payload.get("source_manifest")
        if raw_manifest is None:
            if any(
                key in payload
                for key in (
                    "source_manifest_mode",
                    "source_metadata",
                    "upload_bindings",
                )
            ):
                raise SubmissionError("submission_source_manifest_required", 422)
            discovery_authorization = None
            if profile.web_search_mode == "external_api":
                if payload.get("completion_target") != "finalized_local":
                    raise SubmissionError("submission_completion_target_invalid", 422)
                if payload.get("repair_budget") != 1:
                    raise SubmissionError("submission_repair_budget_invalid", 422)
                discovery_authorization = (
                    RunSourceDiscoveryAuthorizationBootstrap.model_validate(
                        {
                            "schema_version": (
                                RunSourceDiscoveryAuthorizationBootstrap.schema_id
                            ),
                            "route_id": "web-search",
                            "provider_id": "tavily",
                            "execution_owner": "deterministic",
                            "credential_env": "TAVILY_API_KEY",
                            "completion_target": "finalized_local",
                            "repair_budget": 1,
                        },
                        strict=True,
                    )
                )
            elif "completion_target" in payload or "repair_budget" in payload:
                raise SubmissionError("submission_source_manifest_required", 422)
            semantic = {
                "schema_version": SUBMISSION_SCHEMA,
                "request_id_namespace": canonical_fingerprint(
                    {"schema_version": SUBMISSION_SCHEMA, "request_id": request_id}
                ),
                "selections": asdict(profile),
                "execution_authorization": None,
                "source_discovery_authorization": (
                    None
                    if discovery_authorization is None
                    else discovery_authorization.model_dump(
                        mode="json", exclude_unset=False
                    )
                ),
            }
            return (
                canonical_fingerprint(semantic),
                None,
                None,
                discovery_authorization,
            )
        try:
            manifest = ExecutionSourceManifest.model_validate(raw_manifest, strict=True)
        except ValidationError as exc:
            raise SubmissionError("submission_source_manifest_invalid", 422) from exc
        if any(
            not member.input_path.startswith("input/sources/")
            for member in manifest.members
        ):
            raise SubmissionError("submission_source_manifest_invalid", 422)
        if payload.get("source_manifest_mode") not in {"imported", "generated"}:
            raise SubmissionError("submission_source_manifest_invalid", 422)
        if payload.get("completion_target") != "finalized_local":
            raise SubmissionError("submission_completion_target_invalid", 422)
        if payload.get("repair_budget") != 1:
            raise SubmissionError("submission_repair_budget_invalid", 422)
        canonical_manifest = canonical_json_bytes(
            manifest.model_dump(mode="json", exclude_unset=False)
        )
        authorization = RunExecutionAuthorizationBootstrap.model_validate(
            {
                "schema_version": RunExecutionAuthorizationBootstrap.schema_id,
                "completion_target": "finalized_local",
                "source_manifest_path": "input/execution-source-manifest.json",
                "source_manifest_sha256": sha256_hex(canonical_manifest),
                "source_manifest_member_count": len(manifest.members),
                "repair_budget": 1,
            },
            strict=True,
        )
        semantic = {
            "schema_version": SUBMISSION_SCHEMA,
            "request_id_namespace": canonical_fingerprint(
                {"schema_version": SUBMISSION_SCHEMA, "request_id": request_id}
            ),
            "selections": asdict(profile),
            "completion_target": "finalized_local",
            "repair_budget": 1,
            "source_manifest": manifest.model_dump(mode="json", exclude_unset=False),
        }
        return canonical_fingerprint(semantic), manifest, authorization, None

    def _replay_existing_store(
        self,
        *,
        target: Path,
        expected_workspace_id: str,
        expected_run_id: str,
        request_workspace_prefix: str,
    ) -> dict[str, Any]:
        try:
            initialized = WorkspaceBootstrap(target).initialize_runnable_codex(
                expected_adapter_loader=self._adapter_loader
            )
        except RuntimeHostError as exc:
            raise _runtime_submission_error(exc) from exc
        actual_workspace_id = initialized.verified.snapshot.workspace_id
        if actual_workspace_id != expected_workspace_id:
            if actual_workspace_id.startswith(request_workspace_prefix):
                raise SubmissionError("submission_replay_conflict", 409)
            raise SubmissionError("workspace_target_exists", 409)
        return self._receipt_response(
            target=target,
            workspace_id=expected_workspace_id,
            run_id=expected_run_id,
            status="replayed",
        )

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
        if payload.get("human_confirmation") is not True:
            raise SubmissionError("human_confirmation_required", 422)
        target = self._resolve_target(
            _require_text(payload.get("workspace_target"), "workspace_target_invalid")
        )
        profile = _profile_from_payload(payload)
        search_secret_session_id = ""
        if profile.web_search_mode == "external_api":
            search_secret_session_id = _require_text(
                payload.get("search_secret_session_id"),
                "submission_search_secret_invalid",
            )
        (
            fingerprint,
            manifest,
            execution_authorization,
            source_discovery_authorization,
        ) = self._semantic_submission(
            request_id=request_id,
            profile=profile,
            payload=payload,
        )
        workspace_id, run_id, request_workspace_prefix = self._submission_identities(
            request_id, fingerprint
        )
        with self._target_lock(target):
            bootstrap = WorkspaceBootstrap(target)
            authority_kind = bootstrap.classify_target()
            if authority_kind == "sqlite":
                response = self._replay_existing_store(
                    target=target,
                    expected_workspace_id=workspace_id,
                    expected_run_id=run_id,
                    request_workspace_prefix=request_workspace_prefix,
                )
                if profile.web_search_mode == "external_api":
                    secret_status = self._apply_search_secret_effect(
                        session_id=search_secret_session_id,
                        target=target,
                        replayed=True,
                    )
                    response["search_secret_status"] = secret_status
                    if secret_status == "required":
                        return 422, {
                            "ok": False,
                            "reason_code": "submission_search_api_key_required",
                            "search_secret_status": secret_status,
                        }
                    if secret_status == "pending":
                        return 500, {
                            "ok": False,
                            "reason_code": "submission_search_secret_store_failed",
                            "initialization_status": "committed",
                            "search_secret_status": secret_status,
                        }
                return 200, self._with_search_discovery(response, profile=profile)
            if authority_kind == "invalid_sqlite":
                raise SubmissionError("control_store_integrity_invalid", 500)
            if self._target_has_content(target):
                raise SubmissionError("workspace_target_exists", 409)

            if manifest is not None:
                session_id = _require_text(
                    payload.get("upload_session_id"),
                    "submission_upload_session_invalid",
                )
                mode = payload.get("source_manifest_mode")
                if mode not in {"imported", "generated"}:
                    raise SubmissionError("submission_source_manifest_invalid", 422)
                metadata = payload.get("source_metadata")
                try:
                    regenerated = self._staging.canonical_manifest(
                        session_id=session_id,
                        mode=mode,
                        source_metadata=metadata,
                        upload_bindings=payload.get("upload_bindings"),
                    )
                except InitWebStagingError as exc:
                    raise SubmissionError(str(exc), 422) from exc
                if regenerated != manifest:
                    raise SubmissionError("submission_source_manifest_invalid", 422)
                try:
                    self._staging.materialize_canonical(
                        session_id=session_id,
                        mode=mode,
                        source_metadata=metadata,
                        upload_bindings=payload.get("upload_bindings"),
                        manifest=manifest,
                        target=target,
                    )
                    manifest_path = target / "input" / "execution-source-manifest.json"
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    with manifest_path.open("xb") as manifest_stream:
                        manifest_stream.write(
                            canonical_json_bytes(
                                manifest.model_dump(mode="json", exclude_unset=False)
                            )
                        )
                except (InitWebStagingError, OSError) as exc:
                    code = (
                        str(exc)
                        if isinstance(exc, InitWebStagingError)
                        else "init_web_source_materialization_failed"
                    )
                    raise SubmissionError(code, 422) from exc

            identity_suffix = workspace_id.removeprefix("WS-")
            identities = iter((identity_suffix, identity_suffix))
            create_workspace(
                target,
                profile,
                force=False,
                identity_factory=lambda: next(identities),
                execution_authorization=execution_authorization,
                source_discovery_authorization=source_discovery_authorization,
                post_finalize_html=execution_authorization is not None,
            )
            try:
                initialized = bootstrap.initialize_runnable_codex(
                    expected_adapter_loader=self._adapter_loader
                )
            except RuntimeHostError as exc:
                raise _runtime_submission_error(exc) from exc
            response = self._receipt_response(
                target=target,
                workspace_id=workspace_id,
                run_id=run_id,
                status="committed" if initialized.initialized else "replayed",
            )
            if profile.web_search_mode == "external_api":
                secret_status = self._apply_search_secret_effect(
                    session_id=search_secret_session_id,
                    target=target,
                    replayed=not initialized.initialized,
                )
                response["search_secret_status"] = secret_status
                if secret_status == "required":
                    return 422, {
                        "ok": False,
                        "reason_code": "submission_search_api_key_required",
                        "initialization_status": "committed",
                        "search_secret_status": secret_status,
                    }
                if secret_status == "pending":
                    return 500, {
                        "ok": False,
                        "reason_code": "submission_search_secret_store_failed",
                        "initialization_status": "committed",
                        "search_secret_status": secret_status,
                    }
            return 200, self._with_search_discovery(response, profile=profile)


__all__ = [
    "SUBMISSION_SCHEMA",
    "InitWebSubmitter",
    "SubmissionError",
    "preview_output_contract",
]
