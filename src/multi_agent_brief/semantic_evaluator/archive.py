"""Append-only local archive authority for Semantic Evaluator shadow evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
from typing import Any, Mapping

from pydantic import TypeAdapter

from multi_agent_brief.contracts.v2 import ContractId
from multi_agent_brief.semantic_evaluator.adapter import (
    ExternalTextObservation,
    FrozenProviderRequest,
    ProviderShadowReason,
    capture_external_text_v4,
    capture_http_status_v4,
    capture_response_envelope_v4,
    classify_provider_outcome_v4,
    make_provider_boundary_facts_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OPENAI_ADAPTER_ID,
    OPENAI_PROVIDER_ID,
    project_openai_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
    CLIPROXY_ADAPTER_ID,
    CLIPROXY_PROVIDER_ID,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SYNTHETIC_PROVIDER_ID,
    project_synthetic_response_bytes_v4,
)
from multi_agent_brief.semantic_evaluator.composition import (
    _compose_actual_verified,
    _compose_matched_with_resources,
    build_presentation,
    verify_additive_baseline,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    AssessmentPlan,
    BaselinePayload,
    BoundedContext,
    CompositionRecord,
    InputBinding,
    InstrumentConfig,
    InstrumentManifest,
    LajCompositionWitness,
    PresentationRecord,
    ReaderArtifact,
    SemanticAssessmentRun,
    ValidationReport,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.profile import LoadedProfile
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    SHADOW_TIMEOUT_SECONDS,
    ArchiveMember,
    ExternalTextFactRecordV4,
    HttpStatusFactRecordV4,
    ProviderAttemptRecord,
    ProviderBoundaryFactsRecordV4,
    ShadowArchiveManifest,
    ShadowExecutionManifest,
    ShadowExecutionPolicy,
    ShadowRunReceipt,
    ShadowRunRequest,
)
from multi_agent_brief.semantic_evaluator.validator import (
    _verify_laj_composition_witness_with_roots,
    event_stream_bytes,
)


ARCHIVE_VERSION = "semantic_evaluator_shadow_archive_v5"
_TRIAL_ID = TypeAdapter(ContractId)
_FIXED_CONTROL_FILES = frozenset({"archive_manifest.json", "receipt.json", "COMPLETE"})
_REQUIRED_PAYLOAD_FILES = frozenset(
    {
        "request.json",
        "execution_manifest.json",
        "input_binding.json",
        "reader_artifact.json",
        "bounded_context.json",
        "profile.json",
        "instrument_config.json",
        "instrument_manifest.json",
        "assessment_plan.json",
        "run.json",
        "validation_report.json",
        "events.jsonl",
        "laj_composition_witness.json",
        "baseline.json",
        "composition_matched.json",
        "composition_actual.json",
        "presentation_matched.json",
        "presentation_actual.json",
    }
)
_OPENAI_WIRE_ADAPTER_IDS = frozenset({OPENAI_ADAPTER_ID, CLIPROXY_ADAPTER_ID})


def _provider_id_for_adapter(adapter_id: str) -> str:
    if adapter_id == OPENAI_ADAPTER_ID:
        return OPENAI_PROVIDER_ID
    if adapter_id == CLIPROXY_ADAPTER_ID:
        return CLIPROXY_PROVIDER_ID
    if adapter_id == "synthetic_fixture_v4":
        return SYNTHETIC_PROVIDER_ID
    raise SemanticEvaluatorError("shadow_archive_invalid")


@dataclass(frozen=True)
class VerifiedShadowArchive:
    path: Path
    request: ShadowRunRequest
    execution_manifest: ShadowExecutionManifest
    archive_manifest: ShadowArchiveManifest
    receipt: ShadowRunReceipt
    witness: LajCompositionWitness
    presentation: PresentationRecord
    reason_codes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return (
            self.receipt.run_status == "completed"
            and self.receipt.validation_status == "accepted"
        )


def _strict_json_bytes(raw: bytes) -> Any:
    duplicates = False

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicates
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates = True
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if duplicates:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return value


def _model_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value.model_dump(mode="json", warnings="error"))


def _lstat_regular(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return metadata


def _read_regular(path: Path) -> bytes:
    expected = _lstat_regular(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read()
            observed = os.fstat(handle.fileno())
    except OSError:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if (expected.st_dev, expected.st_ino, expected.st_size) != (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return raw


def _validate_existing_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("archive_root_unsafe") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SemanticEvaluatorError("archive_root_unsafe")


def _ensure_real_directory(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise SemanticEvaluatorError("archive_root_unsafe")
    current = Path(path.anchor)
    _validate_existing_directory(current)
    for component in path.parts[1:]:
        current /= component
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError:
            raise SemanticEvaluatorError("archive_root_unsafe") from None
        _validate_existing_directory(current)


def trial_archive_path(archive_root: Path, trial_id: str) -> Path:
    try:
        strict_trial_id = _TRIAL_ID.validate_python(trial_id, strict=True)
    except Exception:
        raise SemanticEvaluatorError("shadow_request_invalid") from None
    if not archive_root.is_absolute() or ".." in archive_root.parts:
        raise SemanticEvaluatorError("archive_root_unsafe")
    leaf = f"trial-{canonical_sha256([strict_trial_id])[:32]}"
    return archive_root / "semantic-evaluator" / "v0.1" / "trials" / leaf


def _all_tree_entries(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    try:
        for candidate in root.rglob("*"):
            relative = candidate.relative_to(root).as_posix()
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise SemanticEvaluatorError("shadow_archive_invalid")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(relative)
            else:
                raise SemanticEvaluatorError("shadow_archive_invalid")
    except SemanticEvaluatorError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    return files, directories


def _expected_directories(files: set[str]) -> set[str]:
    result: set[str] = set()
    for item in files:
        parent = PurePosixPath(item).parent
        while str(parent) != ".":
            result.add(str(parent))
            parent = parent.parent
    return result


def _parse_model(raw: bytes, model: type[Any]) -> Any:
    value = _strict_json_bytes(raw)
    if type(value) is not dict:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    try:
        strict = model.model_validate(value)
    except Exception:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if _model_bytes(strict) != raw:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return strict


def _assert_exact_model_file(raw: bytes, expected: Any) -> None:
    if raw != _model_bytes(expected):
        raise SemanticEvaluatorError("shadow_archive_invalid")


def _absent_external_text():
    return capture_external_text_v4((ExternalTextObservation(False),))


def _provider_fact(*observations: ExternalTextObservation):
    return capture_external_text_v4(tuple(observations))


@dataclass(frozen=True)
class _OpenAISdkProjection:
    status: ExternalTextFactRecordV4
    response_id: ExternalTextFactRecordV4
    model_identity: ExternalTextFactRecordV4
    output: ExternalTextFactRecordV4
    transport_kind: str
    http_status: HttpStatusFactRecordV4
    body_state: str


def _parse_sdk_projection(raw: bytes) -> _OpenAISdkProjection:
    value = _strict_json_bytes(raw)
    required = {
        "body_state",
        "http_status",
        "model_identity",
        "output",
        "response_id",
        "schema_version",
        "status",
        "transport_kind",
    }
    if type(value) is not dict or set(value) != required:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    try:
        if (
            value["schema_version"]
            != "briefloop.semantic_evaluator.openai_sdk_projection.v4"
            or value["transport_kind"]
            not in {"response", "timeout", "connection", "http_error", "adapter_error"}
            or value["body_state"] not in {"absent", "present", "invalid"}
        ):
            raise ValueError
        text_facts = {
            name: ExternalTextFactRecordV4.model_validate(value[name])
            for name in ("status", "response_id", "model_identity", "output")
        }
        http_status = HttpStatusFactRecordV4.model_validate(value["http_status"])
    except Exception:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    result = _OpenAISdkProjection(
        status=text_facts["status"],
        response_id=text_facts["response_id"],
        model_identity=text_facts["model_identity"],
        output=text_facts["output"],
        transport_kind=value["transport_kind"],
        http_status=http_status,
        body_state=value["body_state"],
    )
    expected = canonical_json_bytes(
        {
            "body_state": result.body_state,
            "http_status": result.http_status.model_dump(mode="json", warnings="error"),
            "model_identity": result.model_identity.model_dump(
                mode="json", warnings="error"
            ),
            "output": result.output.model_dump(mode="json", warnings="error"),
            "response_id": result.response_id.model_dump(mode="json", warnings="error"),
            "schema_version": "briefloop.semantic_evaluator.openai_sdk_projection.v4",
            "status": result.status.model_dump(mode="json", warnings="error"),
            "transport_kind": result.transport_kind,
        }
    )
    if raw != expected:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return result


def _observation_from_fact(record: ExternalTextFactRecordV4) -> ExternalTextObservation:
    fact = record.to_runtime()
    if fact.state == "absent":
        return ExternalTextObservation(False)
    if fact.state == "present_valid":
        try:
            return ExternalTextObservation(
                True, (fact.utf8_bytes or b"").decode("utf-8", errors="strict")
            )
        except UnicodeDecodeError:
            return ExternalTextObservation(True, object())
    return ExternalTextObservation(True, object())


def _reconcile_raw_and_sdk(
    raw_fact: Any,
    sdk_fact: ExternalTextFactRecordV4,
    *,
    allowed_values: frozenset[str] | None = None,
):
    if raw_fact.state != "present_valid":
        return raw_fact
    return capture_external_text_v4(
        (
            ExternalTextObservation(
                True,
                (raw_fact.utf8_bytes or b"").decode("utf-8", errors="strict"),
            ),
            _observation_from_fact(sdk_fact),
        ),
        allowed_values=allowed_values,
    )


def _recomputed_facts(
    *,
    record: ProviderAttemptRecord,
    response_raw: bytes | None,
    sdk_projection_raw: bytes | None,
):
    """Rebuild all derivable facts from retained bytes and frozen identities."""

    absent = _absent_external_text()
    sdk_projection = (
        _parse_sdk_projection(sdk_projection_raw)
        if sdk_projection_raw is not None
        else None
    )
    if response_raw is None:
        if record.facts.envelope.state != "absent":
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if record.adapter_id in _OPENAI_WIRE_ADAPTER_IDS:
            if (
                sdk_projection is None
                or sdk_projection.body_state != "absent"
                or any(
                    item.state != "absent"
                    for item in (
                        sdk_projection.status,
                        sdk_projection.response_id,
                        sdk_projection.model_identity,
                        sdk_projection.output,
                    )
                )
            ):
                raise SemanticEvaluatorError("shadow_archive_invalid")
            transport_kind = sdk_projection.transport_kind
            http_status = sdk_projection.http_status.to_runtime()
        else:
            if sdk_projection is not None:
                raise SemanticEvaluatorError("shadow_archive_invalid")
            transport_kind = record.facts.transport_kind
            http_status = record.facts.http_status.to_runtime()
        provider = _provider_fact(
            ExternalTextObservation(True, record.provider_id),
            ExternalTextObservation(True, _provider_id_for_adapter(record.adapter_id)),
        )
        return make_provider_boundary_facts_v4(
            envelope=capture_response_envelope_v4(None, present=False),
            status=absent,
            response_id=absent,
            provider_identity=provider,
            model_identity=absent,
            output=absent,
            # A body-less transport's HTTP status and recognized transport class
            # are necessarily typed adapter evidence.  They remain hash-bound and
            # are consumed only by the sole classifier.
            http_status=http_status,
            transport_kind=transport_kind,  # type: ignore[arg-type]
        )

    if record.facts.envelope.state == "absent":
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if record.adapter_id in _OPENAI_WIRE_ADAPTER_IDS:
        projection = project_openai_response_bytes_v4(response_raw)
        if sdk_projection is None or sdk_projection.body_state not in {
            "present",
            "invalid",
        }:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        provider = _provider_fact(
            ExternalTextObservation(True, record.provider_id),
            ExternalTextObservation(True, _provider_id_for_adapter(record.adapter_id)),
        )
        sdk_is_absent = all(
            item.state == "absent"
            for item in (
                sdk_projection.status,
                sdk_projection.response_id,
                sdk_projection.model_identity,
                sdk_projection.output,
            )
        )
        if sdk_projection.body_state == "invalid":
            status = absent
            response_id = absent
            model = absent
            output = absent
            envelope_invalid_code = "envelope_projection_failed"
        elif projection.envelope_valid and not sdk_is_absent:
            status = _reconcile_raw_and_sdk(
                projection.status,
                sdk_projection.status,
                allowed_values=frozenset(
                    {
                        "completed",
                        "failed",
                        "in_progress",
                        "cancelled",
                        "queued",
                        "incomplete",
                    }
                ),
            )
            response_id = _reconcile_raw_and_sdk(
                projection.response_id, sdk_projection.response_id
            )
            model = _reconcile_raw_and_sdk(
                projection.model_identity, sdk_projection.model_identity
            )
            output = (
                _reconcile_raw_and_sdk(projection.output, sdk_projection.output)
                if projection.output.state == "present_valid"
                else projection.output
            )
            envelope_invalid_code = projection.envelope_invalid_code
        else:
            status = projection.status
            response_id = projection.response_id
            model = projection.model_identity
            output = projection.output
            envelope_invalid_code = projection.envelope_invalid_code
        transport_kind = sdk_projection.transport_kind
        http_status = capture_http_status_v4(None, present=False)
    elif record.adapter_id == "synthetic_fixture_v4":
        if sdk_projection_raw is not None:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        projection = project_synthetic_response_bytes_v4(response_raw)
        provider = _provider_fact(
            ExternalTextObservation(True, record.provider_id),
            ExternalTextObservation(True, SYNTHETIC_PROVIDER_ID),
            ExternalTextObservation(
                projection.provider_identity.state != "absent",
                (
                    (projection.provider_identity.utf8_bytes or b"").decode(
                        "utf-8", errors="strict"
                    )
                    if projection.provider_identity.state == "present_valid"
                    else object()
                ),
            ),
        )
        status = projection.status
        response_id = projection.response_id
        model = projection.model_identity
        output = projection.output
        envelope_invalid_code = projection.envelope_invalid_code
        transport_kind = "response"
        http_status = capture_http_status_v4(None, present=False)
    else:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return make_provider_boundary_facts_v4(
        envelope=capture_response_envelope_v4(
            response_raw,
            present=True,
            invalid_code=envelope_invalid_code,  # type: ignore[arg-type]
        ),
        status=status,
        response_id=response_id,
        provider_identity=provider,
        model_identity=model,
        output=output,
        # A response body precludes transport retry.  The archived typed HTTP
        # observation is still recomputed as absent on the normal response path;
        # a status-error body remains terminal through the present envelope.
        http_status=http_status,
        transport_kind=transport_kind,  # type: ignore[arg-type]
    )


def _validate_attempt_reachability(
    attempts_by_dimension: Mapping[str, list[tuple[ProviderAttemptRecord, Any]]],
    *,
    max_attempts: int,
) -> tuple[ProviderShadowReason, ...]:
    """Validate exact ordinal reachability from recomputed outcomes only."""

    terminal_reasons: set[ProviderShadowReason] = set()
    for attempts in attempts_by_dimension.values():
        ordered = sorted(attempts, key=lambda item: item[0].attempt_ordinal)
        ordinals = [item[0].attempt_ordinal for item in ordered]
        if (
            not ordered
            or ordinals != list(range(1, len(ordered) + 1))
            or len(ordered) > max_attempts
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        for _record, prior_outcome in ordered[:-1]:
            if not prior_outcome.retry_eligible:
                raise SemanticEvaluatorError("shadow_archive_invalid")
        terminal_record, terminal_outcome = ordered[-1]
        if (
            terminal_outcome.retry_eligible
            and terminal_record.attempt_ordinal < max_attempts
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        completed = [item for item in ordered if item[1].attempt_status == "completed"]
        if len(completed) > 1 or (completed and completed[0] != ordered[-1]):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if terminal_outcome.shadow_reason is not None:
            terminal_reasons.add(terminal_outcome.shadow_reason)
    return tuple(sorted(terminal_reasons))


def _attempt_records(
    root: Path,
    payloads: Mapping[str, bytes],
    witness: LajCompositionWitness,
    roots: Any,
    execution: ShadowExecutionManifest,
) -> tuple[tuple[ProviderShadowReason, ...], set[str]]:
    evidence_by_ref = {
        item.attempt_ref: item for item in witness.dimension_attempt_evidence
    }
    transport_paths = sorted(
        path
        for path in payloads
        if path.startswith("attempts/") and path.endswith("/transport.json")
    )
    if len(transport_paths) != len(evidence_by_ref):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    observed_refs: set[str] = set()
    attempts_by_dimension: dict[str, list[tuple[ProviderAttemptRecord, Any]]] = {}
    expected_paths: set[str] = set()
    prompt_by_dimension = {item.dimension_id: item for item in roots.prompts}
    config = witness.instrument_config
    for transport_path in transport_paths:
        record = _parse_model(payloads[transport_path], ProviderAttemptRecord)
        parts = PurePosixPath(transport_path).parts
        if len(parts) != 4 or parts[0] != "attempts":
            raise SemanticEvaluatorError("shadow_archive_invalid")
        try:
            ordinal = int(parts[2])
        except ValueError:
            raise SemanticEvaluatorError("shadow_archive_invalid") from None
        if parts[1] != record.dimension_id or ordinal != record.attempt_ordinal:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if (
            record.trial_id != witness.input_binding.trial_id
            or record.adapter_id != execution.adapter_id
            or record.provider_id != config.provider_id
            or record.requested_model_id != config.model_id
            or record.expected_model_version_utf8_hex
            != config.model_version.encode("utf-8", errors="strict").hex()
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        prefix = f"attempts/{record.dimension_id}/{record.attempt_ordinal}"
        request_path = f"{prefix}/request.json"
        response_path = f"{prefix}/response.body"
        output_path = f"{prefix}/output.txt"
        facts_path = f"{prefix}/boundary_facts.json"
        sdk_projection_path = f"{prefix}/sdk_projection.json"
        if request_path not in payloads or facts_path not in payloads:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        expected_paths.update({request_path, facts_path, transport_path})
        facts_record = _parse_model(payloads[facts_path], ProviderBoundaryFactsRecordV4)
        if facts_record != record.facts:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if sha256_bytes(payloads[request_path]) != record.request_projection_sha256:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        prompt = prompt_by_dimension.get(record.dimension_id)
        if prompt is None:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        try:
            expected_provider_request = FrozenProviderRequest(
                trial_id=witness.input_binding.trial_id,
                dimension_id=record.dimension_id,
                attempt_ordinal=record.attempt_ordinal,
                system_text=prompt.system_text,
                user_text=prompt.user_text,
                prompt_request_sha256=prompt.request_sha256,
                adapter_id=execution.adapter_id,
                provider_id=config.provider_id,
                model_id=config.model_id,
                expected_model_version=config.model_version,
                temperature=float(config.decoding.temperature),
                top_p=float(config.decoding.top_p),
                max_output_tokens=config.decoding.max_output_tokens,
                seed=config.decoding.seed,
                timeout_seconds=SHADOW_TIMEOUT_SECONDS,
            )
        except Exception:
            raise SemanticEvaluatorError("shadow_archive_invalid") from None
        if payloads[request_path] != expected_provider_request.projection_bytes():
            raise SemanticEvaluatorError("shadow_archive_invalid")
        has_response = response_path in payloads
        if has_response != (record.facts.envelope.state != "absent"):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if has_response != (record.raw_transport_response_sha256 is not None):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if has_response and (
            sha256_bytes(payloads[response_path])
            != record.raw_transport_response_sha256
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if has_response:
            expected_paths.add(response_path)
        has_sdk_projection = sdk_projection_path in payloads
        if has_sdk_projection != (record.adapter_id in _OPENAI_WIRE_ADAPTER_IDS):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if has_sdk_projection:
            expected_paths.add(sdk_projection_path)
        recomputed_facts = _recomputed_facts(
            record=record,
            response_raw=payloads.get(response_path),
            sdk_projection_raw=payloads.get(sdk_projection_path),
        )
        if ProviderBoundaryFactsRecordV4.from_runtime(recomputed_facts) != facts_record:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        outcome = classify_provider_outcome_v4(
            recomputed_facts,
            expected_model_version_utf8=config.model_version.encode(
                "utf-8", errors="strict"
            ),
        )
        if (
            record.attempt_status != outcome.attempt_status
            or record.shadow_reason != outcome.shadow_reason
            or record.kernel_reason != outcome.kernel_reason
            or record.retry_eligible != outcome.retry_eligible
            or record.output_eligible != outcome.output_eligible
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if (output_path in payloads) != outcome.output_eligible:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if output_path in payloads and (
            sha256_bytes(payloads[output_path]) != record.extracted_output_sha256
            or recomputed_facts.output.utf8_bytes != payloads[output_path]
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if output_path in payloads:
            expected_paths.add(output_path)
        evidence = evidence_by_ref.get(record.attempt_ref)
        if evidence is None or record.attempt_ref in observed_refs:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if (
            evidence.dimension_id != record.dimension_id
            or evidence.attempt_ordinal != record.attempt_ordinal
            or evidence.prompt_request_sha256 != record.prompt_request_sha256
            or evidence.status != outcome.attempt_status
            or evidence.reason_code != outcome.kernel_reason
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if outcome.output_eligible:
            if (
                response_path not in payloads
                or recomputed_facts.status.utf8_bytes != b"completed"
                or evidence.raw_response_sha256 != record.extracted_output_sha256
                or evidence.raw_response_bytes_hex != payloads[output_path].hex()
                or recomputed_facts.output.utf8_bytes != payloads[output_path]
            ):
                raise SemanticEvaluatorError("shadow_archive_invalid")
        elif (
            evidence.raw_response_sha256 is not None
            or evidence.raw_response_bytes_hex is not None
            or record.extracted_output_sha256 is not None
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        observed_refs.add(record.attempt_ref)
        attempts_by_dimension.setdefault(record.dimension_id, []).append(
            (record, outcome)
        )
    if observed_refs != set(evidence_by_ref):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if set(attempts_by_dimension) != set(prompt_by_dimension):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    terminal_reasons = _validate_attempt_reachability(
        attempts_by_dimension,
        max_attempts=config.retry_policy.max_attempts,
    )
    return terminal_reasons, expected_paths


def verify_shadow_archive(
    path: Path,
    *,
    expected_request: ShadowRunRequest | None = None,
    expected_execution: ShadowExecutionManifest | None = None,
) -> VerifiedShadowArchive:
    """Reopen and verify one archive; this is the only archive reader."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise SemanticEvaluatorError("shadow_archive_incomplete") from None
    except (OSError, RuntimeError):
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    complete_path = path / "COMPLETE"
    if not complete_path.exists():
        raise SemanticEvaluatorError("shadow_archive_incomplete")

    complete_raw = _read_regular(complete_path)
    receipt_raw = _read_regular(path / "receipt.json")
    if complete_raw != (sha256_bytes(receipt_raw) + "\n").encode("ascii"):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    receipt = _parse_model(receipt_raw, ShadowRunReceipt)
    manifest_raw = _read_regular(path / "archive_manifest.json")
    manifest = _parse_model(manifest_raw, ShadowArchiveManifest)
    if receipt.archive_manifest_sha256 != manifest.archive_manifest_sha256:
        raise SemanticEvaluatorError("shadow_archive_invalid")

    expected_payload_paths = {item.path for item in manifest.payload_members}
    all_expected_files = expected_payload_paths | set(_FIXED_CONTROL_FILES)
    files, directories = _all_tree_entries(path)
    if files != all_expected_files or directories != _expected_directories(files):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if not _REQUIRED_PAYLOAD_FILES.issubset(expected_payload_paths):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if (
        len([item for item in expected_payload_paths if item.startswith("prompts/")])
        != 9
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")

    payloads: dict[str, bytes] = {}
    for member in manifest.payload_members:
        raw = _read_regular(path / member.path)
        if len(raw) != member.size_bytes or sha256_bytes(raw) != member.sha256:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        payloads[member.path] = raw

    request = _parse_model(payloads["request.json"], ShadowRunRequest)
    execution = _parse_model(
        payloads["execution_manifest.json"], ShadowExecutionManifest
    )
    policy_payload: dict[str, object] = {
        "schema_version": ShadowExecutionPolicy.schema_id,
        "adapter_id": execution.adapter_id,
        "timeout_seconds": SHADOW_TIMEOUT_SECONDS,
        "sdk_max_retries": 0,
        "raw_retention_days": 30,
        "max_attempts_ceiling": 3,
        "local_filesystem_only": True,
    }
    try:
        policy = ShadowExecutionPolicy.model_validate(
            {
                **policy_payload,
                "execution_policy_sha256": canonical_sha256(policy_payload),
            }
        )
    except Exception:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    if (
        request.shadow_request_sha256 != manifest.shadow_request_sha256
        or request.instrument_sha256 != manifest.instrument_sha256
        or request.execution_sha256 != manifest.execution_sha256
        or request.trial_id != manifest.trial_id
        or receipt.shadow_request_sha256 != request.shadow_request_sha256
        or receipt.instrument_sha256 != request.instrument_sha256
        or receipt.execution_sha256 != request.execution_sha256
        or receipt.trial_id != request.trial_id
        or execution.execution_sha256 != request.execution_sha256
        or execution.instrument_sha256 != request.instrument_sha256
        or execution.execution_policy_sha256 != policy.execution_policy_sha256
        or receipt.execution_origin != execution.execution_origin
        or receipt.qualification_class != execution.qualification_class
        or receipt.qualification_eligible != execution.qualification_eligible
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if expected_request is not None and _model_bytes(request) != _model_bytes(
        expected_request
    ):
        raise SemanticEvaluatorError("shadow_request_conflict")
    if expected_execution is not None and _model_bytes(execution) != _model_bytes(
        expected_execution
    ):
        raise SemanticEvaluatorError("shadow_request_conflict")
    expected_archive_id = f"archive-{canonical_sha256([request.shadow_request_sha256, manifest.aggregate_payload_sha256])[:16]}"
    if (
        manifest.archive_id != expected_archive_id
        or receipt.archive_id != manifest.archive_id
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")

    witness = _parse_model(
        payloads["laj_composition_witness.json"], LajCompositionWitness
    )
    try:
        verified_witness, roots = _verify_laj_composition_witness_with_roots(
            witness,
            include_baseline=True,
        )
    except SemanticEvaluatorError:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    _assert_exact_model_file(payloads["input_binding.json"], witness.input_binding)
    _assert_exact_model_file(payloads["reader_artifact.json"], witness.reader_artifact)
    _assert_exact_model_file(payloads["bounded_context.json"], witness.bounded_context)
    _assert_exact_model_file(
        payloads["instrument_config.json"], witness.instrument_config
    )
    _assert_exact_model_file(
        payloads["instrument_manifest.json"], witness.instrument_manifest
    )
    _assert_exact_model_file(payloads["assessment_plan.json"], witness.assessment_plan)
    _assert_exact_model_file(payloads["run.json"], witness.run)
    _assert_exact_model_file(
        payloads["validation_report.json"], witness.validation_report
    )
    if payloads["events.jsonl"] != event_stream_bytes(witness.events):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    if (
        request.trial_id != witness.input_binding.trial_id
        or request.artifact_id != witness.report_evidence.artifact_id
        or request.report_sha256 != witness.input_binding.report_sha256
        or request.bounded_context_sha256
        != witness.input_binding.bounded_context_sha256
        or request.input_binding_sha256 != witness.input_binding.input_binding_sha256
        or request.instrument_sha256 != witness.instrument_manifest.instrument_sha256
        or request.assessment_plan_sha256
        != witness.assessment_plan.assessment_plan_sha256
        or request.ordered_prompt_request_sha256s
        != [item.request_sha256 for item in roots.prompts]
        or request.provider_id != witness.instrument_config.provider_id
        or request.model_id != witness.instrument_config.model_id
        or request.expected_model_version_utf8_hex
        != witness.instrument_config.model_version.encode(
            "utf-8", errors="strict"
        ).hex()
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")

    profile_payload = _strict_json_bytes(payloads["profile.json"])
    if type(profile_payload) is not dict or set(profile_payload) != {
        "profile",
        "profile_sha256",
    }:
        raise SemanticEvaluatorError("shadow_archive_invalid")
    try:
        loaded_profile = LoadedProfile(
            profile=roots.instrument_snapshot.resources.loaded_profile.profile,
            profile_sha256=roots.instrument_snapshot.resources.loaded_profile.profile_sha256,
        )
    except Exception:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    expected_profile = {
        "profile": loaded_profile.profile.model_dump(mode="json", warnings="error"),
        "profile_sha256": loaded_profile.profile_sha256,
    }
    if profile_payload != expected_profile:
        raise SemanticEvaluatorError("shadow_archive_invalid")

    expected_prompt_paths: set[str] = set()
    for prompt in roots.prompts:
        prompt_path = f"prompts/{prompt.dimension_id}.json"
        expected_prompt_paths.add(prompt_path)
        expected_prompt = canonical_json_bytes(
            {
                "dimension_id": prompt.dimension_id,
                "forbidden_canary_values": list(prompt.forbidden_canary_values),
                "request_sha256": prompt.request_sha256,
                "system_text": prompt.system_text,
                "user_text": prompt.user_text,
            }
        )
        if payloads.get(prompt_path) != expected_prompt:
            raise SemanticEvaluatorError("shadow_archive_invalid")

    terminal_attempt_reasons, expected_attempt_paths = _attempt_records(
        path, payloads, witness, roots, execution
    )
    if set(payloads) != (
        set(_REQUIRED_PAYLOAD_FILES) | expected_prompt_paths | expected_attempt_paths
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")

    baseline = _parse_model(payloads["baseline.json"], BaselinePayload)
    matched = _parse_model(payloads["composition_matched.json"], CompositionRecord)
    actual = _parse_model(payloads["composition_actual.json"], CompositionRecord)
    presentation_matched = _parse_model(
        payloads["presentation_matched.json"], PresentationRecord
    )
    presentation_actual = _parse_model(
        payloads["presentation_actual.json"], PresentationRecord
    )
    try:
        expected_matched = _compose_matched_with_resources(
            report_evidence=verified_witness.report_evidence,
            reader_artifact=verified_witness.reader_artifact,
            bounded_context=verified_witness.bounded_context,
            resource_snapshot=roots.instrument_snapshot.resources,
        )
        expected_actual = _compose_actual_verified(
            verified_witness,
            resource_snapshot=roots.instrument_snapshot.resources,
        )
        if baseline != expected_matched.baseline_payload:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if matched != expected_matched or actual != expected_actual:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if not verify_additive_baseline(matched, actual, witness=verified_witness):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if presentation_matched != build_presentation(
            matched,
            report_evidence=verified_witness.report_evidence,
            reader_artifact=verified_witness.reader_artifact,
            bounded_context=verified_witness.bounded_context,
        ) or presentation_actual != build_presentation(
            actual,
            witness=verified_witness,
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
    except SemanticEvaluatorError:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None

    if (
        manifest.run_status != witness.run.run_status
        or manifest.validation_status != witness.validation_report.validation_status
        or receipt.run_id != witness.run.run_id
        or receipt.run_status != witness.run.run_status
        or receipt.validation_status != witness.validation_report.validation_status
    ):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    expected_receipt = _receipt_for_manifest(
        manifest=manifest,
        request=request,
        execution=execution,
        run=witness.run,
        validation=witness.validation_report,
        created_at=receipt.created_at,
    )
    if receipt_raw != _model_bytes(expected_receipt):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    reasons = set(witness.validation_report.reason_codes)
    specific_provider_reasons = set(terminal_attempt_reasons).intersection(
        {"provider_identity_mismatch", "provider_incomplete"}
    )
    if specific_provider_reasons:
        if "provider_failed" not in terminal_attempt_reasons:
            reasons.discard("provider_failed")
        reasons.update(specific_provider_reasons)
    return VerifiedShadowArchive(
        path=path,
        request=request,
        execution_manifest=execution,
        archive_manifest=manifest,
        receipt=receipt,
        witness=witness,
        presentation=presentation_actual,
        reason_codes=tuple(sorted(reasons)),
    )


def resolve_existing_archive(
    *,
    archive_root: Path,
    request: ShadowRunRequest,
    execution_manifest: ShadowExecutionManifest,
) -> VerifiedShadowArchive | None:
    path = trial_archive_path(archive_root, request.trial_id)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise SemanticEvaluatorError("shadow_archive_invalid") from None
    return verify_shadow_archive(
        path,
        expected_request=request,
        expected_execution=execution_manifest,
    )


def _write_exclusive(path: Path, raw: bytes) -> None:
    if type(raw) is not bytes:
        raise SemanticEvaluatorError("shadow_archive_publish_failed")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise SemanticEvaluatorError("shadow_archive_publish_failed") from None


def prepare_archive_root(*, archive_root: Path, trial_id: str) -> Path:
    """Prove local publication capability before optional provider execution."""

    final_path = trial_archive_path(archive_root, trial_id)
    parent = final_path.parent
    probe: Path | None = None
    try:
        _ensure_real_directory(parent)
        probe = Path(tempfile.mkdtemp(prefix=".capability-", dir=parent))
        marker = b"briefloop-shadow-archive-capability-v1\n"
        _write_exclusive(probe / "PROBE", marker)
        if _read_regular(probe / "PROBE") != marker:
            raise SemanticEvaluatorError("archive_root_unsafe")
    except Exception:
        if probe is not None:
            try:
                shutil.rmtree(probe)
            except OSError:
                pass
        raise SemanticEvaluatorError("archive_root_unsafe") from None
    try:
        shutil.rmtree(probe)
    except OSError:
        raise SemanticEvaluatorError("archive_root_unsafe") from None
    return final_path


def _resolve_publish_winner(
    final_path: Path,
    *,
    request: ShadowRunRequest,
    execution_manifest: ShadowExecutionManifest,
) -> VerifiedShadowArchive:
    deadline = time.monotonic() + 5.0
    last_error: SemanticEvaluatorError | None = None
    while True:
        try:
            return verify_shadow_archive(
                final_path,
                expected_request=request,
                expected_execution=execution_manifest,
            )
        except SemanticEvaluatorError as exc:
            if exc.reason_code == "shadow_request_conflict":
                raise
            last_error = exc
        if time.monotonic() >= deadline:
            if (final_path / "COMPLETE").exists() and last_error is not None:
                raise last_error
            raise SemanticEvaluatorError("shadow_request_conflict") from None
        time.sleep(0.01)


def _manifest_for_payloads(
    *,
    stage: Path,
    request: ShadowRunRequest,
    execution: ShadowExecutionManifest,
    run: SemanticAssessmentRun,
    validation: ValidationReport,
    payload_paths: list[str],
) -> ShadowArchiveManifest:
    members = []
    for relative in sorted(payload_paths):
        raw = _read_regular(stage / relative)
        members.append(
            ArchiveMember(path=relative, size_bytes=len(raw), sha256=sha256_bytes(raw))
        )
    aggregate = canonical_sha256(
        [item.model_dump(mode="json", warnings="error") for item in members]
    )
    payload: dict[str, Any] = {
        "schema_version": ShadowArchiveManifest.schema_id,
        "archive_id": f"archive-{canonical_sha256([request.shadow_request_sha256, aggregate])[:16]}",
        "shadow_request_sha256": request.shadow_request_sha256,
        "instrument_sha256": request.instrument_sha256,
        "execution_sha256": execution.execution_sha256,
        "trial_id": request.trial_id,
        "run_status": run.run_status,
        "validation_status": validation.validation_status,
        "payload_members": [
            item.model_dump(mode="json", warnings="error") for item in members
        ],
        "payload_file_count": len(members),
        "aggregate_payload_sha256": aggregate,
    }
    return ShadowArchiveManifest.model_validate(
        {**payload, "archive_manifest_sha256": canonical_sha256(payload)}
    )


def _receipt_for_manifest(
    *,
    manifest: ShadowArchiveManifest,
    request: ShadowRunRequest,
    execution: ShadowExecutionManifest,
    run: SemanticAssessmentRun,
    validation: ValidationReport,
    created_at: str,
) -> ShadowRunReceipt:
    payload: dict[str, Any] = {
        "schema_version": ShadowRunReceipt.schema_id,
        "receipt_id": f"receipt-{canonical_sha256([manifest.archive_manifest_sha256, run.run_id])[:16]}",
        "archive_id": manifest.archive_id,
        "shadow_request_sha256": request.shadow_request_sha256,
        "instrument_sha256": request.instrument_sha256,
        "execution_sha256": execution.execution_sha256,
        "run_id": run.run_id,
        "trial_id": request.trial_id,
        "run_status": run.run_status,
        "validation_status": validation.validation_status,
        "archive_status": "complete",
        "archive_manifest_sha256": manifest.archive_manifest_sha256,
        "execution_origin": execution.execution_origin,
        "qualification_class": execution.qualification_class,
        "qualification_eligible": execution.qualification_eligible,
        "created_at": created_at,
    }
    return ShadowRunReceipt.model_validate(
        {**payload, "receipt_sha256": canonical_sha256(payload)}
    )


def publish_shadow_archive(
    *,
    archive_root: Path,
    request: ShadowRunRequest,
    execution_manifest: ShadowExecutionManifest,
    payloads: Mapping[str, bytes],
    run: SemanticAssessmentRun,
    validation_report: ValidationReport,
    created_at: str,
) -> VerifiedShadowArchive:
    """Publish one complete archive without overwrite, then reopen it."""

    if not _REQUIRED_PAYLOAD_FILES.issubset(payloads):
        raise SemanticEvaluatorError("shadow_archive_publish_failed")
    if any(path in _FIXED_CONTROL_FILES for path in payloads):
        raise SemanticEvaluatorError("shadow_archive_publish_failed")
    final_path = trial_archive_path(archive_root, request.trial_id)
    parent = final_path.parent
    _ensure_real_directory(parent)
    existing = resolve_existing_archive(
        archive_root=archive_root,
        request=request,
        execution_manifest=execution_manifest,
    )
    if existing is not None:
        return existing

    try:
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    except OSError:
        raise SemanticEvaluatorError("shadow_archive_publish_failed") from None
    try:
        for relative, raw in sorted(payloads.items()):
            candidate = PurePosixPath(relative)
            if (
                candidate.is_absolute()
                or str(candidate) != relative
                or any(part in {"", ".", ".."} for part in candidate.parts)
            ):
                raise SemanticEvaluatorError("shadow_archive_publish_failed")
            _write_exclusive(stage / relative, raw)
        manifest = _manifest_for_payloads(
            stage=stage,
            request=request,
            execution=execution_manifest,
            run=run,
            validation=validation_report,
            payload_paths=list(payloads),
        )
        _write_exclusive(stage / "archive_manifest.json", _model_bytes(manifest))
        receipt = _receipt_for_manifest(
            manifest=manifest,
            request=request,
            execution=execution_manifest,
            run=run,
            validation=validation_report,
            created_at=created_at,
        )
        receipt_bytes = _model_bytes(receipt)
        _write_exclusive(stage / "receipt.json", receipt_bytes)
        _write_exclusive(
            stage / "COMPLETE",
            (sha256_bytes(receipt_bytes) + "\n").encode("ascii"),
        )
        verify_shadow_archive(
            stage,
            expected_request=request,
            expected_execution=execution_manifest,
        )
        try:
            os.rename(stage, final_path)
        except OSError:
            try:
                final_path.lstat()
            except FileNotFoundError:
                raise SemanticEvaluatorError("shadow_archive_publish_failed") from None
            except OSError:
                raise SemanticEvaluatorError("shadow_archive_publish_failed") from None
            return _resolve_publish_winner(
                final_path,
                request=request,
                execution_manifest=execution_manifest,
            )
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return verify_shadow_archive(
            final_path,
            expected_request=request,
            expected_execution=execution_manifest,
        )
    except SemanticEvaluatorError:
        raise
    finally:
        try:
            shutil.rmtree(stage)
        except OSError:
            pass


__all__ = [
    "ARCHIVE_VERSION",
    "VerifiedShadowArchive",
    "prepare_archive_root",
    "publish_shadow_archive",
    "resolve_existing_archive",
    "trial_archive_path",
    "verify_shadow_archive",
]
