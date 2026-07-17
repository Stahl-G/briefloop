"""Append-only local archive authority for Semantic Evaluator shadow evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any, Mapping

from pydantic import TypeAdapter

from multi_agent_brief.contracts.v2 import ContractId
from multi_agent_brief.semantic_evaluator.adapter import FrozenProviderRequest
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
    ProviderAttemptRecord,
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


ARCHIVE_VERSION = "semantic_evaluator_shadow_archive_v1"
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


@dataclass(frozen=True)
class VerifiedShadowArchive:
    path: Path
    request: ShadowRunRequest
    execution_manifest: ShadowExecutionManifest
    archive_manifest: ShadowArchiveManifest
    receipt: ShadowRunReceipt
    witness: LajCompositionWitness
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
        value = json.loads(text, object_pairs_hook=pairs_hook)
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


def _attempt_records(
    root: Path,
    payloads: Mapping[str, bytes],
    witness: LajCompositionWitness,
    roots: Any,
    execution: ShadowExecutionManifest,
) -> tuple[tuple[str, ...], set[str]]:
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
    terminal_by_dimension: dict[str, ProviderAttemptRecord] = {}
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
            or record.expected_model_version != config.model_version
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        prefix = f"attempts/{record.dimension_id}/{record.attempt_ordinal}"
        request_path = f"{prefix}/request.json"
        response_path = f"{prefix}/response.body"
        output_path = f"{prefix}/output.txt"
        if request_path not in payloads:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        expected_paths.update({request_path, transport_path})
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
        if (response_path in payloads) != (
            record.raw_transport_response_sha256 is not None
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if (
            response_path in payloads
            and sha256_bytes(payloads[response_path])
            != record.raw_transport_response_sha256
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if response_path in payloads:
            expected_paths.add(response_path)
        if (output_path in payloads) != (record.extracted_output_sha256 is not None):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if (
            output_path in payloads
            and sha256_bytes(payloads[output_path]) != record.extracted_output_sha256
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if output_path in payloads:
            expected_paths.add(output_path)
        evidence = evidence_by_ref.get(record.attempt_ref)
        if evidence is None:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        expected_reason = (
            "provider_failed"
            if record.reason_code == "provider_identity_mismatch"
            else record.reason_code
        )
        if (
            evidence.dimension_id != record.dimension_id
            or evidence.attempt_ordinal != record.attempt_ordinal
            or evidence.prompt_request_sha256 != record.prompt_request_sha256
            or evidence.status != record.status
            or evidence.reason_code != expected_reason
        ):
            raise SemanticEvaluatorError("shadow_archive_invalid")
        if record.status == "completed":
            if (
                evidence.raw_response_sha256 != record.extracted_output_sha256
                or evidence.raw_response_bytes_hex != payloads[output_path].hex()
                or record.observed_model_version != record.expected_model_version
            ):
                raise SemanticEvaluatorError("shadow_archive_invalid")
        elif evidence.raw_response_sha256 is not None:
            raise SemanticEvaluatorError("shadow_archive_invalid")
        observed_refs.add(record.attempt_ref)
        terminal_by_dimension[record.dimension_id] = record
    if observed_refs != set(evidence_by_ref):
        raise SemanticEvaluatorError("shadow_archive_invalid")
    return (
        tuple(
            sorted(
                {
                    item.reason_code
                    for item in terminal_by_dimension.values()
                    if item.reason_code is not None
                }
            )
        ),
        expected_paths,
    )


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
        or request.expected_model_version != witness.instrument_config.model_version
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
    if "provider_identity_mismatch" in terminal_attempt_reasons:
        reasons.discard("provider_failed")
        reasons.add("provider_identity_mismatch")
    return VerifiedShadowArchive(
        path=path,
        request=request,
        execution_manifest=execution,
        archive_manifest=manifest,
        receipt=receipt,
        witness=witness,
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
    claimed = False
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
            final_path.mkdir()
            claimed = True
        except FileExistsError:
            return verify_shadow_archive(
                final_path,
                expected_request=request,
                expected_execution=execution_manifest,
            )
        except OSError:
            raise SemanticEvaluatorError("shadow_archive_publish_failed") from None
        for relative in sorted(
            set(payloads) | {"archive_manifest.json", "receipt.json"}
        ):
            _write_exclusive(final_path / relative, _read_regular(stage / relative))
        # Recompute every published byte before making the completion claim.
        staged_complete = _read_regular(stage / "COMPLETE")
        files, _directories = _all_tree_entries(final_path)
        if files != set(payloads) | {"archive_manifest.json", "receipt.json"}:
            raise SemanticEvaluatorError("shadow_archive_publish_failed")
        for relative in files:
            if _read_regular(final_path / relative) != _read_regular(stage / relative):
                raise SemanticEvaluatorError("shadow_archive_publish_failed")
        _write_exclusive(final_path / "COMPLETE", staged_complete)
        try:
            directory_fd = os.open(final_path, os.O_RDONLY)
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
    except SemanticEvaluatorError as exc:
        if claimed and exc.reason_code == "shadow_archive_invalid":
            raise SemanticEvaluatorError("shadow_archive_publish_failed") from None
        raise
    finally:
        try:
            shutil.rmtree(stage)
        except OSError:
            pass


__all__ = [
    "ARCHIVE_VERSION",
    "VerifiedShadowArchive",
    "publish_shadow_archive",
    "resolve_existing_archive",
    "trial_archive_path",
    "verify_shadow_archive",
]
