"""Fail-closed admission for the deterministic PR-SE-1 instrument surface."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Any, Optional

from pydantic import ValidationError

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.semantic_evaluator.contracts import (
    INPUT_BINDING_SCHEMA_ID,
    AdmissionRequest,
    AdmittedReportEvidence,
    AssessmentPlan,
    BoundedContext,
    EvaluatorProfile,
    InputBinding,
    InstrumentConfig,
    InstrumentManifest,
)
from multi_agent_brief.semantic_evaluator.errors import (
    SemanticEvaluatorError,
    value_free_violations,
)
from multi_agent_brief.semantic_evaluator.normalization import (
    NormalizedReader,
    build_admitted_report_evidence,
    verify_bounded_context,
)
from multi_agent_brief.semantic_evaluator.profile import (
    LoadedProfile,
    load_profile,
    validate_loaded_profile,
)
from multi_agent_brief.semantic_evaluator.prompts import (
    FrozenDimensionPrompt,
    PromptSizer,
    build_dimension_prompt,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_model_sha256,
    canonical_sha256,
    normalized_utf8_text,
    sha256_bytes,
)
from multi_agent_brief.semantic_evaluator.unit_planner import (
    build_assessment_plan,
    trial_identity_conflicts,
)


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason_codes: tuple[str, ...]
    violations: tuple[FieldViolation, ...] = ()
    report_evidence: Optional[AdmittedReportEvidence] = None
    reader: Optional[NormalizedReader] = None
    bounded_context: Optional[BoundedContext] = None
    instrument_config: Optional[InstrumentConfig] = None
    input_binding: Optional[InputBinding] = None
    instrument_manifest: Optional[InstrumentManifest] = None
    assessment_plan: Optional[AssessmentPlan] = None
    prompts: tuple[FrozenDimensionPrompt, ...] = ()
    prompt_request_sha256s: tuple[str, ...] = ()


def _blocked(
    *reason_codes: str,
    violations: tuple[FieldViolation, ...] = (),
) -> AdmissionDecision:
    return AdmissionDecision(
        False,
        tuple(sorted(set(reason_codes))),
        violations=violations,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def archive_root_is_safe(*, archive_root: Path, workspace_root: Path) -> bool:
    try:
        expanded_archive = archive_root.expanduser()
        if ".." in expanded_archive.parts:
            return False
        archive = Path(os.path.normpath(str(expanded_archive)))
        workspace_selector = Path(os.path.normpath(str(workspace_root.expanduser())))
        if not archive.is_absolute() or not workspace_selector.is_absolute():
            return False
        current = Path(archive.anchor)
        missing_component = False
        for component in archive.parts[1:]:
            current /= component
            if missing_component:
                continue
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                missing_component = True
                continue
            except (OSError, RuntimeError):
                return False
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                return False
        canonical_archive = archive.resolve(strict=False)
        canonical_workspace = workspace_selector.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return canonical_archive != canonical_workspace and not _is_relative_to(
        canonical_archive, canonical_workspace
    )


def input_binding_sha256(binding: InputBinding) -> str:
    return canonical_model_sha256(binding, exclude=("input_binding_sha256",))


def build_input_binding(
    *,
    trial_id: str,
    reader: NormalizedReader,
    context: BoundedContext,
    profile_sha256: str,
    config_sha256: str,
    public_data_attestation: bool,
    private_or_confidential_material: bool,
) -> InputBinding:
    identity = [
        trial_id,
        reader.artifact.report_sha256,
        reader.artifact.normalized_text_sha256,
        context.context_sha256,
        profile_sha256,
        config_sha256,
    ]
    payload = {
        "schema_version": INPUT_BINDING_SCHEMA_ID,
        "binding_id": f"binding-{canonical_sha256(identity)[:12]}",
        "trial_id": trial_id,
        "report_sha256": reader.artifact.report_sha256,
        "normalized_text_sha256": reader.artifact.normalized_text_sha256,
        "bounded_context_sha256": context.context_sha256,
        "profile_sha256": profile_sha256,
        "instrument_config_sha256": config_sha256,
        "language": "zh-CN",
        "data_class": context.data_class,
        "public_data_attestation": public_data_attestation,
        "private_or_confidential_material": private_or_confidential_material,
    }
    return InputBinding.model_validate(
        {**payload, "input_binding_sha256": canonical_sha256(payload)}
    )


def _strict_request(request: AdmissionRequest | Mapping[str, Any]) -> AdmissionRequest:
    payload: Any
    if isinstance(request, AdmissionRequest):
        payload = request.model_dump(mode="json")
    else:
        payload = request
    return AdmissionRequest.model_validate(payload)


def _strict_loaded_profile(
    loaded_profile: LoadedProfile | None,
) -> LoadedProfile:
    try:
        if loaded_profile is None:
            candidate = load_profile()
        elif isinstance(loaded_profile, LoadedProfile):
            candidate = loaded_profile
        else:
            raise TypeError("profile_invalid")
        strict_profile = EvaluatorProfile.model_validate(
            candidate.profile.model_dump(mode="json")
        )
        if not isinstance(candidate.profile_sha256, str):
            raise TypeError("profile_invalid")
        strict = LoadedProfile(
            profile=strict_profile,
            profile_sha256=candidate.profile_sha256,
        )
        validate_loaded_profile(strict)
        if canonical_json_bytes(strict.profile) != canonical_json_bytes(
            candidate.profile
        ):
            raise ValueError("profile_invalid")
    except (
        AttributeError,
        OSError,
        RuntimeError,
        SemanticEvaluatorError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise SemanticEvaluatorError("profile_invalid") from None
    return strict


def _strict_existing_binding(
    existing_binding: InputBinding | None,
) -> InputBinding | None:
    if existing_binding is None:
        return None
    try:
        if not isinstance(existing_binding, InputBinding):
            raise TypeError("trial_identity_conflict")
        strict = InputBinding.model_validate(existing_binding.model_dump(mode="json"))
        if canonical_json_bytes(strict) != canonical_json_bytes(
            existing_binding
        ) or strict.input_binding_sha256 != canonical_model_sha256(
            strict, exclude=("input_binding_sha256",)
        ):
            raise ValueError("trial_identity_conflict")
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise SemanticEvaluatorError("trial_identity_conflict") from None
    return strict


def admit_inputs(
    request: AdmissionRequest | Mapping[str, Any],
    *,
    prompt_sizer: PromptSizer | None,
    loaded_profile: LoadedProfile | None = None,
    existing_binding: InputBinding | None = None,
) -> AdmissionDecision:
    """Validate one typed request before any plan, prompt, or run side effect."""

    try:
        admitted_request = _strict_request(request)
    except ValidationError as exc:
        return _blocked(
            "admission_contract_invalid",
            violations=value_free_violations(exc),
        )
    except (AttributeError, TypeError, ValueError):
        return _blocked("admission_contract_invalid")

    try:
        report_bytes = bytes.fromhex(admitted_request.report_bytes_hex)
    except (TypeError, ValueError):
        return _blocked("admission_contract_invalid")
    if report_bytes.hex() != admitted_request.report_bytes_hex:
        return _blocked("admission_contract_invalid")
    if not report_bytes:
        return _blocked("input_missing")
    try:
        decoded = normalized_utf8_text(report_bytes)
    except (UnicodeError, ValueError):
        return _blocked("input_not_utf8")
    if "\x00" in decoded:
        return _blocked("input_not_utf8")
    if sha256_bytes(report_bytes) != admitted_request.declared_report_sha256:
        return _blocked("input_sha_mismatch")
    try:
        context = verify_bounded_context(admitted_request.bounded_context)
    except SemanticEvaluatorError:
        return _blocked("input_sha_mismatch")
    if context.context_sha256 != admitted_request.declared_bounded_context_sha256:
        return _blocked("input_sha_mismatch")
    config = InstrumentConfig.model_validate(
        admitted_request.instrument_config.model_dump(mode="json")
    )
    if context.language != "zh-CN" or config.language != "zh-CN":
        return _blocked("unsupported_language")
    if context.data_class not in {"public", "synthetic"}:
        return _blocked("unsupported_data_class")
    if not admitted_request.public_data_attestation:
        return _blocked("public_data_attestation_required")
    if admitted_request.private_or_confidential_material:
        return _blocked("private_material_forbidden")

    archive_root = admitted_request.archive_root
    workspace_root = admitted_request.workspace_root
    if (archive_root is None) != (workspace_root is None):
        return _blocked("archive_root_unsafe")
    if archive_root is not None and workspace_root is not None:
        if not archive_root_is_safe(
            archive_root=Path(archive_root),
            workspace_root=Path(workspace_root),
        ):
            return _blocked("archive_root_unsafe")
    if prompt_sizer is None:
        return _blocked("prompt_sizer_unavailable")
    if (
        getattr(prompt_sizer, "sizer_id", None) != config.prompt_sizer.sizer_id
        or getattr(prompt_sizer, "sizer_version", None)
        != config.prompt_sizer.sizer_version
    ):
        return _blocked("prompt_sizer_unavailable")

    try:
        profile = _strict_loaded_profile(loaded_profile)
    except SemanticEvaluatorError:
        return _blocked("profile_invalid")
    try:
        strict_existing_binding = _strict_existing_binding(existing_binding)
    except SemanticEvaluatorError:
        return _blocked("trial_identity_conflict")
    if profile.profile.language != "zh-CN":
        return _blocked("unsupported_language")
    from multi_agent_brief.semantic_evaluator.instrument import (
        build_instrument_manifest,
        verify_instrument_manifest,
    )

    try:
        manifest = build_instrument_manifest(config, loaded_profile=profile)
        verify_instrument_manifest(manifest, config, loaded_profile=profile)
    except (SemanticEvaluatorError, OSError, RuntimeError, TypeError, ValueError):
        return _blocked("instrument_manifest_mismatch")
    try:
        report_evidence, reader = build_admitted_report_evidence(
            report_bytes,
            artifact_id=admitted_request.artifact_id,
        )
    except SemanticEvaluatorError as exc:
        return _blocked(exc.reason_code)

    config_sha = canonical_model_sha256(config)
    binding = build_input_binding(
        trial_id=admitted_request.trial_id,
        reader=reader,
        context=context,
        profile_sha256=profile.profile_sha256,
        config_sha256=config_sha,
        public_data_attestation=admitted_request.public_data_attestation,
        private_or_confidential_material=admitted_request.private_or_confidential_material,
    )
    if strict_existing_binding is not None and trial_identity_conflicts(
        strict_existing_binding,
        binding,
    ):
        return _blocked("trial_identity_conflict")
    try:
        plan = build_assessment_plan(
            trial_id=admitted_request.trial_id,
            report_sha256=reader.artifact.report_sha256,
            profile=profile.profile,
            profile_sha256=profile.profile_sha256,
        )
    except (SemanticEvaluatorError, ValidationError, TypeError, ValueError):
        return _blocked("profile_invalid")
    prompts: list[FrozenDimensionPrompt] = []
    for dimension in profile.profile.dimensions:
        try:
            prompt = build_dimension_prompt(
                reader_artifact=reader.artifact,
                normalized_text=reader.normalized_text,
                bounded_context=context,
                dimension=dimension,
                assessment_plan=plan,
            )
            count = prompt_sizer.count_tokens(
                system_text=prompt.system_text,
                user_text=prompt.user_text,
            )
        except Exception:
            return _blocked("prompt_sizer_unavailable")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return _blocked("prompt_sizer_unavailable")
        if (
            count + config.prompt_sizer.reserved_output_tokens
            > config.prompt_sizer.max_context_tokens
        ):
            return _blocked("input_too_long_for_full_context_instrument")
        prompts.append(prompt)
    return AdmissionDecision(
        admitted=True,
        reason_codes=(),
        report_evidence=report_evidence,
        reader=reader,
        bounded_context=context,
        instrument_config=config,
        input_binding=binding,
        instrument_manifest=manifest,
        assessment_plan=plan,
        prompts=tuple(prompts),
        prompt_request_sha256s=tuple(item.request_sha256 for item in prompts),
    )


__all__ = [
    "AdmissionDecision",
    "admit_inputs",
    "archive_root_is_safe",
    "build_input_binding",
    "input_binding_sha256",
]
