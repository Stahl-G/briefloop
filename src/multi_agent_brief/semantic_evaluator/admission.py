"""Fail-closed admission for the deterministic PR-SE-1 instrument surface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Optional

from multi_agent_brief.contracts.errors import FieldViolation
from multi_agent_brief.semantic_evaluator.contracts import (
    INPUT_BINDING_SCHEMA_ID,
    AssessmentPlan,
    BoundedContext,
    InputBinding,
    InstrumentConfig,
    InstrumentManifest,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.normalization import (
    NormalizedReader,
    bounded_context_sha256,
    normalize_markdown,
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
    canonical_model_sha256,
    canonical_sha256,
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
    reader: Optional[NormalizedReader] = None
    bounded_context: Optional[BoundedContext] = None
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


def _build_input_binding(
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


def admit_inputs(
    *,
    report_bytes: bytes | None,
    declared_report_sha256: str,
    artifact_id: str,
    bounded_context: BoundedContext,
    declared_bounded_context_sha256: str,
    instrument_config: InstrumentConfig,
    trial_id: str,
    public_data_attestation: bool,
    private_or_confidential_material: bool,
    prompt_sizer: PromptSizer | None,
    loaded_profile: LoadedProfile | None = None,
    archive_root: Path | None = None,
    workspace_root: Path | None = None,
    existing_binding: InputBinding | None = None,
    instrument_manifest: InstrumentManifest | None = None,
) -> AdmissionDecision:
    invalid_boolean_fields = tuple(
        field
        for field, value in (
            ("public_data_attestation", public_data_attestation),
            ("private_or_confidential_material", private_or_confidential_material),
        )
        if type(value) is not bool
    )
    if invalid_boolean_fields:
        return _blocked(
            "admission_contract_invalid",
            violations=tuple(
                FieldViolation(field=field, error="must be a boolean")
                for field in invalid_boolean_fields
            ),
        )
    if report_bytes is None or not report_bytes:
        return _blocked("input_missing")
    if sha256_bytes(report_bytes) != declared_report_sha256:
        return _blocked("input_sha_mismatch")
    expected_context_sha = bounded_context_sha256(bounded_context)
    if (
        bounded_context.context_sha256 != expected_context_sha
        or declared_bounded_context_sha256 != expected_context_sha
    ):
        return _blocked("input_sha_mismatch")
    bounded_context = BoundedContext.model_validate(
        bounded_context.model_dump(mode="json")
    )
    profile = loaded_profile or load_profile()
    try:
        validate_loaded_profile(profile)
    except SemanticEvaluatorError:
        return _blocked("profile_invalid")
    if (
        bounded_context.language != "zh-CN"
        or profile.profile.language != "zh-CN"
        or instrument_config.language != "zh-CN"
    ):
        return _blocked("unsupported_language")
    if bounded_context.data_class not in {"public", "synthetic"}:
        return _blocked("unsupported_data_class")
    if not public_data_attestation:
        return _blocked("public_data_attestation_required")
    if private_or_confidential_material:
        return _blocked("private_material_forbidden")
    if (archive_root is None) != (workspace_root is None):
        return _blocked("archive_root_unsafe")
    if archive_root is not None and workspace_root is not None:
        if not archive_root_is_safe(
            archive_root=archive_root, workspace_root=workspace_root
        ):
            return _blocked("archive_root_unsafe")
    if prompt_sizer is None:
        return _blocked("prompt_sizer_unavailable")
    if (
        getattr(prompt_sizer, "sizer_id", None)
        != instrument_config.prompt_sizer.sizer_id
        or getattr(prompt_sizer, "sizer_version", None)
        != instrument_config.prompt_sizer.sizer_version
    ):
        return _blocked("prompt_sizer_unavailable")
    try:
        reader = normalize_markdown(report_bytes, artifact_id=artifact_id)
    except SemanticEvaluatorError as exc:
        return _blocked(exc.reason_code)
    config_sha = canonical_model_sha256(instrument_config)
    from multi_agent_brief.semantic_evaluator.instrument import (
        build_instrument_manifest,
        verify_instrument_manifest,
    )

    try:
        manifest = (
            instrument_manifest
            if instrument_manifest is not None
            else build_instrument_manifest(
                instrument_config,
                loaded_profile=profile,
            )
        )
        verify_instrument_manifest(
            manifest,
            instrument_config,
            loaded_profile=profile,
        )
        manifest = InstrumentManifest.model_validate(
            manifest.model_dump(mode="json")
            if isinstance(manifest, InstrumentManifest)
            else manifest
        )
    except (SemanticEvaluatorError, OSError, RuntimeError, ValueError):
        return _blocked("instrument_manifest_mismatch")
    binding = _build_input_binding(
        trial_id=trial_id,
        reader=reader,
        context=bounded_context,
        profile_sha256=profile.profile_sha256,
        config_sha256=config_sha,
        public_data_attestation=public_data_attestation,
        private_or_confidential_material=private_or_confidential_material,
    )
    if existing_binding is not None and trial_identity_conflicts(
        existing_binding, binding
    ):
        return _blocked("trial_identity_conflict")
    plan = build_assessment_plan(
        trial_id=trial_id,
        report_sha256=reader.artifact.report_sha256,
        profile=profile.profile,
        profile_sha256=profile.profile_sha256,
    )
    prompts: list[FrozenDimensionPrompt] = []
    for dimension in profile.profile.dimensions:
        prompt = build_dimension_prompt(
            reader_artifact=reader.artifact,
            normalized_text=reader.normalized_text,
            bounded_context=bounded_context,
            dimension=dimension,
            assessment_plan=plan,
        )
        try:
            count = prompt_sizer.count_tokens(
                system_text=prompt.system_text,
                user_text=prompt.user_text,
            )
        except Exception:
            return _blocked("prompt_sizer_unavailable")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return _blocked("prompt_sizer_unavailable")
        if (
            count + instrument_config.prompt_sizer.reserved_output_tokens
            > instrument_config.prompt_sizer.max_context_tokens
        ):
            return _blocked("input_too_long_for_full_context_instrument")
        prompts.append(prompt)
    return AdmissionDecision(
        admitted=True,
        reason_codes=(),
        reader=reader,
        bounded_context=bounded_context,
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
    "input_binding_sha256",
]
