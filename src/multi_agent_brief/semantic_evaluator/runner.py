"""Replayable, isolated Semantic Evaluator offline-shadow runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextObservation,
    FrozenProviderRequest,
    RawProviderAttempt,
    SemanticEvaluatorAdapter,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.admission import admit_inputs
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OPENAI_ADAPTER_ID,
    OPENAI_ADAPTER_VERSION,
    OPENAI_PROVIDER_ID,
)
from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
    CLIPROXY_ADAPTER_ID,
    CLIPROXY_ADAPTER_VERSION,
    CLIPROXY_BASE_URL,
    CLIPROXY_PROVIDER_ID,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SYNTHETIC_ADAPTER_ID,
    SYNTHETIC_PROVIDER_ID,
    _load_fixture_manifest,
)
from multi_agent_brief.semantic_evaluator.archive import (
    ARCHIVE_VERSION,
    VerifiedShadowArchive,
    prepare_archive_root,
    publish_shadow_archive,
    resolve_existing_archive,
)
from multi_agent_brief.semantic_evaluator.composition import (
    build_presentation,
    compose_actual_laj,
    compose_matched_non_llm,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    BoundedContext,
    InstrumentConfig,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    CLIProxyUtf8BytePromptSizerV1,
    OpenAITiktokenPromptSizerV1,
    SyntheticFixturePromptSizerV1,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
    source_sha256_for_module,
)
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    SHADOW_CONTRACT_MODELS,
    SHADOW_TIMEOUT_SECONDS,
    ProviderAttemptRecord,
    ProviderBoundaryFactsRecordV4,
    ShadowExecutionManifest,
    ShadowExecutionPolicy,
    ShadowRunRequest,
)
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    event_stream_bytes,
    make_dimension_attempt_evidence,
)


RUNNER_VERSION = "semantic_evaluator_shadow_runner_v5"
DEFAULT_TIMEOUT_SECONDS = SHADOW_TIMEOUT_SECONDS
PROFILE_ID = "research_design_report_zh_v1"
_MAX_JSON_INPUT_BYTES = 8 * 1024 * 1024
_RUNNER_SOURCE_MODULES = (
    "multi_agent_brief.semantic_evaluator.admission",
    "multi_agent_brief.semantic_evaluator.baseline",
    "multi_agent_brief.semantic_evaluator.composition",
    "multi_agent_brief.semantic_evaluator.contracts",
    "multi_agent_brief.semantic_evaluator.errors",
    "multi_agent_brief.semantic_evaluator.instrument",
    "multi_agent_brief.semantic_evaluator.normalization",
    "multi_agent_brief.semantic_evaluator.parser",
    "multi_agent_brief.semantic_evaluator.profile",
    "multi_agent_brief.semantic_evaluator.prompt_sizer",
    "multi_agent_brief.semantic_evaluator.prompts",
    "multi_agent_brief.semantic_evaluator.resources",
    "multi_agent_brief.semantic_evaluator.runner",
    "multi_agent_brief.semantic_evaluator.serialization",
    "multi_agent_brief.semantic_evaluator.shadow_contracts",
    "multi_agent_brief.semantic_evaluator.snapshot",
    "multi_agent_brief.semantic_evaluator.unit_planner",
    "multi_agent_brief.semantic_evaluator.validator",
)


@dataclass(frozen=True)
class ShadowRunResult:
    ok: bool
    replayed: bool
    archive_complete: bool
    archive_path: str | None
    receipt_id: str | None
    run_status: str | None
    validation_status: str | None
    reason_codes: tuple[str, ...]
    execution_origin: str | None
    qualification_class: str | None
    qualification_eligible: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "replayed": self.replayed,
            "archive_complete": self.archive_complete,
            "archive_path": self.archive_path,
            "receipt_id": self.receipt_id,
            "run_status": self.run_status,
            "validation_status": self.validation_status,
            "reason_codes": list(self.reason_codes),
            "execution_origin": self.execution_origin,
            "qualification_class": self.qualification_class,
            "qualification_eligible": self.qualification_eligible,
        }


@dataclass(frozen=True)
class PreparedShadowRun:
    """One immutable admitted prompt set, before any archive or adapter effect."""

    admission: Any
    archive_root: Path
    trial_id: str
    prompt_sizer: Any
    policy: ShadowExecutionPolicy


@dataclass(frozen=True)
class _ArchivedAttempt:
    request: FrozenProviderRequest
    raw: RawProviderAttempt
    record: ProviderAttemptRecord


def _failure(*reason_codes: str) -> ShadowRunResult:
    return ShadowRunResult(
        ok=False,
        replayed=False,
        archive_complete=False,
        archive_path=None,
        receipt_id=None,
        run_status=None,
        validation_status=None,
        reason_codes=tuple(sorted(set(reason_codes))),
        execution_origin=None,
        qualification_class=None,
        qualification_eligible=False,
    )


def _from_archive(
    archive: VerifiedShadowArchive,
    *,
    replayed: bool,
) -> ShadowRunResult:
    return ShadowRunResult(
        ok=archive.ok,
        replayed=replayed,
        archive_complete=True,
        archive_path=str(archive.path),
        receipt_id=archive.receipt.receipt_id,
        run_status=archive.receipt.run_status,
        validation_status=archive.receipt.validation_status,
        reason_codes=archive.reason_codes,
        execution_origin=archive.receipt.execution_origin,
        qualification_class=archive.receipt.qualification_class,
        qualification_eligible=archive.receipt.qualification_eligible,
    )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _input_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise SemanticEvaluatorError("shadow_request_invalid")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        metadata_value = candidate.lstat()
        parent = candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    if stat.S_ISLNK(metadata_value.st_mode) or not stat.S_ISREG(metadata_value.st_mode):
        raise SemanticEvaluatorError("shadow_request_invalid")
    return parent / candidate.name


def _read_input(path: Path, *, bounded: bool) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_JSON_INPUT_BYTES + 1 if bounded else -1)
            metadata_value = os.fstat(handle.fileno())
        current = path.lstat()
    except OSError:
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    if (
        stat.S_ISLNK(current.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino)
        != (metadata_value.st_dev, metadata_value.st_ino)
        or current.st_size != metadata_value.st_size
        or current.st_size != len(raw)
        or (bounded and len(raw) > _MAX_JSON_INPUT_BYTES)
    ):
        raise SemanticEvaluatorError("shadow_request_invalid")
    return raw


def _strict_json(raw: bytes) -> dict[str, Any]:
    duplicate = False

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate = True
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    if duplicate or type(value) is not dict:
        raise SemanticEvaluatorError("shadow_request_invalid")
    return value


def _strict_inputs(
    *,
    report: str | Path,
    bounded_context: str | Path,
    profile: str,
    instrument: str | Path,
    trial_id: str,
    archive_root: str | Path,
) -> tuple[bytes, BoundedContext, InstrumentConfig, str, Path, Path]:
    if profile != PROFILE_ID or type(trial_id) is not str:
        raise SemanticEvaluatorError("shadow_request_invalid")
    report_path = _input_path(report)
    context_path = _input_path(bounded_context)
    instrument_path = _input_path(instrument)
    report_bytes = _read_input(report_path, bounded=False)
    context_payload = _strict_json(_read_input(context_path, bounded=True))
    instrument_payload = _strict_json(_read_input(instrument_path, bounded=True))
    try:
        context = BoundedContext.model_validate(context_payload)
        config = InstrumentConfig.model_validate(instrument_payload)
    except ValidationError:
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    root = Path(archive_root).expanduser()
    if not root.is_absolute() or ".." in root.parts:
        raise SemanticEvaluatorError("shadow_request_invalid")
    try:
        common = Path(
            os.path.commonpath(
                [
                    str(report_path.parent),
                    str(context_path.parent),
                    str(instrument_path.parent),
                ]
            )
        )
    except (OSError, ValueError):
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    return report_bytes, context, config, trial_id, root, common


def _prompt_sizer_for(config: InstrumentConfig) -> tuple[str, Any]:
    if config.provider_id == SYNTHETIC_PROVIDER_ID:
        return SYNTHETIC_ADAPTER_ID, SyntheticFixturePromptSizerV1()
    if config.provider_id == OPENAI_PROVIDER_ID:
        return OPENAI_ADAPTER_ID, OpenAITiktokenPromptSizerV1(model_id=config.model_id)
    if config.provider_id == CLIPROXY_PROVIDER_ID:
        return CLIPROXY_ADAPTER_ID, CLIProxyUtf8BytePromptSizerV1()
    raise SemanticEvaluatorError("shadow_adapter_unavailable")


def _policy(adapter_id: str) -> ShadowExecutionPolicy:
    payload: dict[str, object] = {
        "schema_version": ShadowExecutionPolicy.schema_id,
        "adapter_id": adapter_id,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "sdk_max_retries": 0,
        "raw_retention_days": 30,
        "max_attempts_ceiling": 3,
        "local_filesystem_only": True,
    }
    return ShadowExecutionPolicy.model_validate(
        {**payload, "execution_policy_sha256": canonical_sha256(payload)}
    )


def _source_bundle(*module_names: str) -> str:
    try:
        sources = [
            source_sha256_for_module(module_name) for module_name in module_names
        ]
    except EvaluatorResourceError:
        raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
    return canonical_sha256(sources)


def _execution_manifest(
    *,
    instrument_sha256: str,
    policy: ShadowExecutionPolicy,
    prompt_sizer: Any,
) -> ShadowExecutionManifest:
    if policy.adapter_id == SYNTHETIC_ADAPTER_ID:
        adapter_version = _load_fixture_manifest()
        adapter_modules = (
            "multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture",
        )
        provider_sdk_name = "synthetic"
        provider_sdk_version = "synthetic-v4"
        execution_origin = "synthetic_fixture"
        qualification_class = "synthetic_only"
        provider_endpoint_sha256 = canonical_sha256(["synthetic-no-network"])
        qualification_eligible = False
    elif policy.adapter_id == OPENAI_ADAPTER_ID:
        adapter_version = OPENAI_ADAPTER_VERSION
        adapter_modules = (
            "multi_agent_brief.semantic_evaluator.adapters.openai_responses",
        )
        try:
            provider_sdk_version = metadata.version("openai")
        except Exception:
            raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
        provider_sdk_name = "openai"
        execution_origin = "direct_openai"
        qualification_class = "direct_openai"
        provider_endpoint_sha256 = canonical_sha256(["openai-sdk-default"])
        qualification_eligible = True
    elif policy.adapter_id == CLIPROXY_ADAPTER_ID:
        adapter_version = CLIPROXY_ADAPTER_VERSION
        adapter_modules = (
            "multi_agent_brief.semantic_evaluator.adapters.openai_responses",
            "multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses",
        )
        try:
            provider_sdk_version = metadata.version("openai")
        except Exception:
            raise SemanticEvaluatorError("shadow_adapter_unavailable") from None
        provider_sdk_name = "openai"
        execution_origin = "local_cliproxy"
        qualification_class = "local_proxy_experimental"
        provider_endpoint_sha256 = canonical_sha256([CLIPROXY_BASE_URL])
        qualification_eligible = False
    else:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    if type(provider_sdk_version) is not str or not provider_sdk_version:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    schema_hashes = {
        model.schema_id: canonical_sha256(model.model_json_schema())
        for model in sorted(SHADOW_CONTRACT_MODELS, key=lambda item: item.schema_id)
    }
    runner_source = _source_bundle(*_RUNNER_SOURCE_MODULES)
    adapter_source = _source_bundle(
        "multi_agent_brief.semantic_evaluator.adapter",
        *adapter_modules,
    )
    archive_source = _source_bundle("multi_agent_brief.semantic_evaluator.archive")
    component_identity = {
        "instrument_sha256": instrument_sha256,
        "execution_policy_sha256": policy.execution_policy_sha256,
        "adapter_id": policy.adapter_id,
        "adapter_version": adapter_version,
        "adapter_source_sha256": adapter_source,
        "runner_version": RUNNER_VERSION,
        "runner_source_sha256": runner_source,
        "archive_version": ARCHIVE_VERSION,
        "archive_source_sha256": archive_source,
        "shadow_schema_sha256s": schema_hashes,
        "provider_sdk_name": provider_sdk_name,
        "provider_sdk_version": provider_sdk_version,
        "execution_origin": execution_origin,
        "qualification_class": qualification_class,
        "provider_endpoint_sha256": provider_endpoint_sha256,
        "prompt_sizer_id": prompt_sizer.sizer_id,
        "prompt_sizer_version": prompt_sizer.sizer_version,
        "tokenizer_package": prompt_sizer.package_name,
        "tokenizer_version": prompt_sizer.package_version,
        "tokenizer_encoding": prompt_sizer.encoding_name,
        "python_major_minor": f"python-{sys.version_info.major}.{sys.version_info.minor}",
        "qualification_eligible": qualification_eligible,
    }
    payload: dict[str, object] = {
        "schema_version": ShadowExecutionManifest.schema_id,
        "execution_manifest_id": f"execution-{canonical_sha256(component_identity)[:16]}",
        **component_identity,
    }
    return ShadowExecutionManifest.model_validate(
        {**payload, "execution_sha256": canonical_sha256(payload)}
    )


def _shadow_request(
    admission: Any, execution: ShadowExecutionManifest
) -> ShadowRunRequest:
    payload: dict[str, object] = {
        "schema_version": ShadowRunRequest.schema_id,
        "trial_id": admission.input_binding.trial_id,
        "artifact_id": admission.report_evidence.artifact_id,
        "report_sha256": admission.input_binding.report_sha256,
        "bounded_context_sha256": admission.input_binding.bounded_context_sha256,
        "input_binding_sha256": admission.input_binding.input_binding_sha256,
        "instrument_sha256": admission.instrument_manifest.instrument_sha256,
        "assessment_plan_sha256": admission.assessment_plan.assessment_plan_sha256,
        "ordered_prompt_request_sha256s": list(admission.prompt_request_sha256s),
        "execution_sha256": execution.execution_sha256,
        "provider_id": admission.instrument_config.provider_id,
        "model_id": admission.instrument_config.model_id,
        "expected_model_version_utf8_hex": admission.instrument_config.model_version.encode(
            "utf-8", errors="strict"
        ).hex(),
    }
    return ShadowRunRequest.model_validate(
        {**payload, "shadow_request_sha256": canonical_sha256(payload)}
    )


def _adapter_for(execution: ShadowExecutionManifest) -> SemanticEvaluatorAdapter:
    if execution.adapter_id == SYNTHETIC_ADAPTER_ID:
        from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
            SyntheticFixtureAdapterV4,
        )

        adapter: Any = SyntheticFixtureAdapterV4()
    elif execution.adapter_id == OPENAI_ADAPTER_ID:
        from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
            OpenAIResponsesAdapterV4,
        )

        api_key = os.environ.get("OPENAI_API_KEY")
        if type(api_key) is not str or not api_key:
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        adapter = OpenAIResponsesAdapterV4(api_key=api_key)
    elif execution.adapter_id == CLIPROXY_ADAPTER_ID:
        from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
            CLIProxyResponsesAdapterV1,
        )

        api_key = os.environ.get("CLIPROXY_API_KEY")
        if type(api_key) is not str or not api_key:
            raise SemanticEvaluatorError("shadow_adapter_unavailable")
        adapter = CLIProxyResponsesAdapterV1(api_key=api_key)
    else:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    if (
        adapter.adapter_id != execution.adapter_id
        or adapter.adapter_version != execution.adapter_version
        or adapter.provider_sdk_name != execution.provider_sdk_name
        or adapter.provider_sdk_version != execution.provider_sdk_version
        or adapter.qualification_eligible != execution.qualification_eligible
    ):
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return adapter


def _validate_adapter(
    adapter: SemanticEvaluatorAdapter,
    execution: ShadowExecutionManifest,
) -> SemanticEvaluatorAdapter:
    try:
        valid = (
            adapter.adapter_id == execution.adapter_id
            and adapter.adapter_version == execution.adapter_version
            and adapter.provider_sdk_name == execution.provider_sdk_name
            and adapter.provider_sdk_version == execution.provider_sdk_version
            and adapter.qualification_eligible == execution.qualification_eligible
            and callable(adapter.invoke)
        )
    except Exception:
        valid = False
    if not valid:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return adapter


def _normalize_adapter_attempt(
    raw: RawProviderAttempt,
    *,
    request: FrozenProviderRequest,
) -> RawProviderAttempt:
    if type(raw) is not RawProviderAttempt:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    if raw.request_projection_bytes != request.projection_bytes():
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    outcome = classify_provider_outcome_v4(
        raw.facts,
        expected_model_version_utf8=request.expected_model_version.encode(
            "utf-8", errors="strict"
        ),
    )
    if raw.outcome != outcome:
        raise SemanticEvaluatorError("shadow_adapter_unavailable")
    return raw


def _attempt_record(
    *,
    provider_request: FrozenProviderRequest,
    attempt_ref: str,
    raw: RawProviderAttempt,
    started_at: str,
    completed_at: str,
) -> ProviderAttemptRecord:
    payload: dict[str, object] = {
        "schema_version": ProviderAttemptRecord.schema_id,
        "attempt_ref": attempt_ref,
        "trial_id": provider_request.trial_id,
        "dimension_id": provider_request.dimension_id,
        "attempt_ordinal": provider_request.attempt_ordinal,
        "prompt_request_sha256": provider_request.prompt_request_sha256,
        "adapter_id": provider_request.adapter_id,
        "provider_id": provider_request.provider_id,
        "requested_model_id": provider_request.model_id,
        "expected_model_version_utf8_hex": provider_request.expected_model_version.encode(
            "utf-8", errors="strict"
        ).hex(),
        "facts": ProviderBoundaryFactsRecordV4.from_runtime(raw.facts).model_dump(
            mode="json", warnings="error"
        ),
        "attempt_status": raw.outcome.attempt_status,
        "shadow_reason": raw.outcome.shadow_reason,
        "kernel_reason": raw.outcome.kernel_reason,
        "retry_eligible": raw.outcome.retry_eligible,
        "output_eligible": raw.outcome.output_eligible,
        "request_projection_sha256": sha256_bytes(raw.request_projection_bytes),
        "raw_transport_response_sha256": (
            sha256_bytes(raw.raw_transport_response)
            if raw.raw_transport_response is not None
            else None
        ),
        "extracted_output_sha256": (
            sha256_bytes(raw.extracted_output)
            if raw.extracted_output is not None
            else None
        ),
        "input_tokens": raw.input_tokens,
        "output_tokens": raw.output_tokens,
        "total_tokens": raw.total_tokens,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    return ProviderAttemptRecord.model_validate(
        {**payload, "attempt_record_sha256": canonical_sha256(payload)}
    )


def _execute_dimensions(
    *,
    admission: Any,
    policy: ShadowExecutionPolicy,
    adapter: SemanticEvaluatorAdapter,
    clock: Callable[[], str],
    sleep: Callable[[float], None],
) -> tuple[list[Any], list[_ArchivedAttempt]]:
    evidence: list[Any] = []
    archived: list[_ArchivedAttempt] = []
    config = admission.instrument_config
    for prompt in admission.prompts:
        for ordinal in range(1, config.retry_policy.max_attempts + 1):
            provider_request = FrozenProviderRequest(
                trial_id=admission.input_binding.trial_id,
                dimension_id=prompt.dimension_id,
                attempt_ordinal=ordinal,
                system_text=prompt.system_text,
                user_text=prompt.user_text,
                prompt_request_sha256=prompt.request_sha256,
                adapter_id=policy.adapter_id,
                provider_id=config.provider_id,
                model_id=config.model_id,
                expected_model_version=config.model_version,
                temperature=float(config.decoding.temperature),
                top_p=float(config.decoding.top_p),
                max_output_tokens=config.decoding.max_output_tokens,
                seed=config.decoding.seed,
                timeout_seconds=policy.timeout_seconds,
            )
            started_at = clock()
            try:
                raw = _normalize_adapter_attempt(
                    adapter.invoke(provider_request),
                    request=provider_request,
                )
            except Exception:
                absent = capture_external_text_v4((ExternalTextObservation(False),))
                provider = capture_external_text_v4(
                    (
                        ExternalTextObservation(True, provider_request.provider_id),
                        ExternalTextObservation(True, provider_request.provider_id),
                    )
                )
                facts = make_provider_boundary_facts_v4(
                    envelope=capture_response_envelope_v4(None, present=False),
                    status=absent,
                    response_id=absent,
                    provider_identity=provider,
                    model_identity=absent,
                    output=absent,
                    http_status=capture_http_status_v4(None, present=False),
                    transport_kind="adapter_error",
                )
                outcome = classify_provider_outcome_v4(
                    facts,
                    expected_model_version_utf8=provider_request.expected_model_version.encode(
                        "utf-8", errors="strict"
                    ),
                )
                raw = RawProviderAttempt(
                    facts=facts,
                    outcome=outcome,
                    request_projection_bytes=provider_request.projection_bytes(),
                    raw_transport_response=None,
                    extracted_output=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                )
            completed_at = clock()
            evidence_reason = raw.outcome.kernel_reason
            item = make_dimension_attempt_evidence(
                trial_id=provider_request.trial_id,
                prompt=prompt,
                attempt_ordinal=ordinal,
                status=raw.outcome.attempt_status,
                raw_response_bytes=raw.extracted_output,
                reason_code=evidence_reason,
            )
            record = _attempt_record(
                provider_request=provider_request,
                attempt_ref=item.attempt_ref,
                raw=raw,
                started_at=started_at,
                completed_at=completed_at,
            )
            evidence.append(item)
            archived.append(_ArchivedAttempt(provider_request, raw, record))
            retry = (
                raw.outcome.attempt_status == "failed"
                and raw.outcome.retry_eligible
                and ordinal < config.retry_policy.max_attempts
            )
            if not retry:
                break
            sleep(config.retry_policy.backoff_schedule_ms[ordinal - 1] / 1000.0)
    return evidence, archived


def _archive_payloads(
    *,
    admission: Any,
    request: ShadowRunRequest,
    execution: ShadowExecutionManifest,
    archived_attempts: list[_ArchivedAttempt],
    assembled: Any,
    baseline: Any,
    matched: Any,
    actual: Any,
    presentation_matched: Any,
    presentation_actual: Any,
) -> dict[str, bytes]:
    model = lambda value: canonical_json_bytes(
        value.model_dump(mode="json", warnings="error")
    )
    profile = admission._instrument_snapshot.resources.loaded_profile
    payloads: dict[str, bytes] = {
        "request.json": model(request),
        "execution_manifest.json": model(execution),
        "input_binding.json": model(admission.input_binding),
        "reader_artifact.json": model(admission.reader.artifact),
        "bounded_context.json": model(admission.bounded_context),
        "profile.json": canonical_json_bytes(
            {
                "profile": profile.profile.model_dump(mode="json", warnings="error"),
                "profile_sha256": profile.profile_sha256,
            }
        ),
        "instrument_config.json": model(admission.instrument_config),
        "instrument_manifest.json": model(admission.instrument_manifest),
        "assessment_plan.json": model(admission.assessment_plan),
        "run.json": model(assembled.run),
        "validation_report.json": model(assembled.validation_report),
        "events.jsonl": event_stream_bytes(assembled.events),
        "laj_composition_witness.json": model(assembled.witness),
        "baseline.json": model(baseline),
        "composition_matched.json": model(matched),
        "composition_actual.json": model(actual),
        "presentation_matched.json": model(presentation_matched),
        "presentation_actual.json": model(presentation_actual),
    }
    for prompt in admission.prompts:
        payloads[f"prompts/{prompt.dimension_id}.json"] = canonical_json_bytes(
            {
                "dimension_id": prompt.dimension_id,
                "forbidden_canary_values": list(prompt.forbidden_canary_values),
                "request_sha256": prompt.request_sha256,
                "system_text": prompt.system_text,
                "user_text": prompt.user_text,
            }
        )
    for archived in archived_attempts:
        record = archived.record
        prefix = f"attempts/{record.dimension_id}/{record.attempt_ordinal}"
        payloads[f"{prefix}/request.json"] = archived.raw.request_projection_bytes
        payloads[f"{prefix}/boundary_facts.json"] = canonical_json_bytes(
            ProviderBoundaryFactsRecordV4.from_runtime(archived.raw.facts).model_dump(
                mode="json", warnings="error"
            )
        )
        payloads[f"{prefix}/transport.json"] = model(record)
        if archived.raw.raw_transport_response is not None:
            payloads[f"{prefix}/response.body"] = archived.raw.raw_transport_response
        if archived.raw.extracted_output is not None:
            payloads[f"{prefix}/output.txt"] = archived.raw.extracted_output
        if archived.raw.sdk_projection_bytes is not None:
            payloads[f"{prefix}/sdk_projection.json"] = (
                archived.raw.sdk_projection_bytes
            )
    return payloads


def prepare_shadow_run(
    *,
    report: str | Path,
    bounded_context: str | Path,
    profile: str,
    instrument: str | Path,
    trial_id: str,
    archive_root: str | Path,
) -> PreparedShadowRun | ShadowRunResult:
    """Freeze one admitted prompt set without archive, credentials, or provider."""

    try:
        report_bytes, context, config, trial_id, root, common_input_root = (
            _strict_inputs(
                report=report,
                bounded_context=bounded_context,
                profile=profile,
                instrument=instrument,
                trial_id=trial_id,
                archive_root=archive_root,
            )
        )
    except SemanticEvaluatorError as exc:
        return _failure(exc.reason_code)
    if (
        config.decoding.seed is not None
        or not 1 <= config.retry_policy.max_attempts <= 3
        or config.retry_policy.retryable_reason_codes
        != (
            []
            if config.retry_policy.max_attempts == 1
            else ["provider_retryable_failure"]
        )
        or config.prompt_sizer.reserved_output_tokens
        < config.decoding.max_output_tokens
    ):
        return _failure("shadow_request_invalid")
    try:
        adapter_id, prompt_sizer = _prompt_sizer_for(config)
    except SemanticEvaluatorError as exc:
        return _failure(exc.reason_code)
    admission = admit_inputs(
        {
            "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
            "artifact_id": f"shadow-report-{sha256_bytes(report_bytes)[:16]}",
            "trial_id": trial_id,
            "report_bytes_hex": report_bytes.hex(),
            "declared_report_sha256": sha256_bytes(report_bytes),
            "bounded_context": context,
            "declared_bounded_context_sha256": context.context_sha256,
            "instrument_config": config,
            "public_data_attestation": True,
            "private_or_confidential_material": False,
            "archive_root": str(root),
            "workspace_root": str(common_input_root),
        },
        prompt_sizer=prompt_sizer,
    )
    if not admission.admitted:
        return _failure(*admission.reason_codes)
    try:
        policy = _policy(adapter_id)
    except SemanticEvaluatorError as exc:
        return _failure(exc.reason_code)
    return PreparedShadowRun(
        admission=admission,
        archive_root=root,
        trial_id=trial_id,
        prompt_sizer=prompt_sizer,
        policy=policy,
    )


def execute_prepared_shadow_run(
    prepared: PreparedShadowRun,
    *,
    adapter_factory: Callable[[ShadowExecutionManifest], SemanticEvaluatorAdapter]
    | None = None,
    replay_only: bool = False,
    clock: Callable[[], str] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> ShadowRunResult:
    """Replay or execute exactly the immutable prompt set produced by prepare."""

    admission = prepared.admission
    root = prepared.archive_root
    trial_id = prepared.trial_id
    policy = prepared.policy
    try:
        execution = _execution_manifest(
            instrument_sha256=admission.instrument_manifest.instrument_sha256,
            policy=policy,
            prompt_sizer=prepared.prompt_sizer,
        )
        request = _shadow_request(admission, execution)
        replay = resolve_existing_archive(
            archive_root=root,
            request=request,
            execution_manifest=execution,
        )
    except SemanticEvaluatorError as exc:
        return _failure(exc.reason_code)
    if replay is not None:
        return _from_archive(replay, replayed=True)
    if replay_only:
        return _failure("shadow_archive_incomplete")
    try:
        prepare_archive_root(archive_root=root, trial_id=trial_id)
    except SemanticEvaluatorError as exc:
        return _failure(exc.reason_code)
    try:
        factory = adapter_factory or _adapter_for
        adapter = _validate_adapter(factory(execution), execution)
    except Exception:
        return _failure("shadow_adapter_unavailable")
    try:
        evidence, archived_attempts = _execute_dimensions(
            admission=admission,
            policy=policy,
            adapter=adapter,
            clock=clock,
            sleep=sleep,
        )
        assembled = assemble_semantic_assessment_run(
            admission=admission,
            dimension_attempt_evidence=evidence,
        )
        matched = compose_matched_non_llm(
            report_evidence=admission.report_evidence,
            reader_artifact=admission.reader.artifact,
            bounded_context=admission.bounded_context,
        )
        baseline = matched.baseline_payload
        actual = compose_actual_laj(assembled.witness)
        presentation_matched = build_presentation(
            matched,
            report_evidence=admission.report_evidence,
            reader_artifact=admission.reader.artifact,
            bounded_context=admission.bounded_context,
        )
        presentation_actual = build_presentation(
            actual,
            witness=assembled.witness,
        )
        payloads = _archive_payloads(
            admission=admission,
            request=request,
            execution=execution,
            archived_attempts=archived_attempts,
            assembled=assembled,
            baseline=baseline,
            matched=matched,
            actual=actual,
            presentation_matched=presentation_matched,
            presentation_actual=presentation_actual,
        )
        published = publish_shadow_archive(
            archive_root=root,
            request=request,
            execution_manifest=execution,
            payloads=payloads,
            run=assembled.run,
            validation_report=assembled.validation_report,
            created_at=clock(),
        )
    except SemanticEvaluatorError as exc:
        reason = (
            exc.reason_code
            if exc.reason_code.startswith("shadow_archive_")
            else "shadow_archive_publish_failed"
        )
        return _failure(reason)
    except Exception:
        return _failure("shadow_archive_publish_failed")
    return _from_archive(published, replayed=False)


def run_shadow(
    *,
    report: str | Path,
    bounded_context: str | Path,
    profile: str,
    instrument: str | Path,
    trial_id: str,
    archive_root: str | Path,
    adapter_factory: Callable[[ShadowExecutionManifest], SemanticEvaluatorAdapter]
    | None = None,
    clock: Callable[[], str] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> ShadowRunResult:
    """Run or exactly replay one isolated public/synthetic shadow trial."""

    prepared = prepare_shadow_run(
        report=report,
        bounded_context=bounded_context,
        profile=profile,
        instrument=instrument,
        trial_id=trial_id,
        archive_root=archive_root,
    )
    if isinstance(prepared, ShadowRunResult):
        return prepared
    return execute_prepared_shadow_run(
        prepared,
        adapter_factory=adapter_factory,
        clock=clock,
        sleep=sleep,
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "PROFILE_ID",
    "PreparedShadowRun",
    "RUNNER_VERSION",
    "ShadowRunResult",
    "execute_prepared_shadow_run",
    "prepare_shadow_run",
    "run_shadow",
]
