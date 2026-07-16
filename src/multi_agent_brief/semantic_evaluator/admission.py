"""Fail-closed admission for the deterministic PR-SE-1 instrument surface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from multi_agent_brief.semantic_evaluator.contracts import (
    INPUT_BINDING_SCHEMA_ID,
    AssessmentPlan,
    BoundedContext,
    InputBinding,
    InstrumentConfig,
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
    reader: Optional[NormalizedReader] = None
    input_binding: Optional[InputBinding] = None
    assessment_plan: Optional[AssessmentPlan] = None
    prompts: tuple[FrozenDimensionPrompt, ...] = ()


def _blocked(*reason_codes: str) -> AdmissionDecision:
    return AdmissionDecision(False, tuple(sorted(set(reason_codes))))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def archive_root_is_safe(*, archive_root: Path, workspace_root: Path) -> bool:
    archive = archive_root.expanduser().resolve(strict=False)
    workspace = workspace_root.expanduser().resolve(strict=False)
    return archive != workspace and not _is_relative_to(archive, workspace)


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
) -> AdmissionDecision:
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
        input_binding=binding,
        assessment_plan=plan,
        prompts=tuple(prompts),
    )


__all__ = [
    "AdmissionDecision",
    "admit_inputs",
    "archive_root_is_safe",
    "input_binding_sha256",
]
