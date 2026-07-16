"""Admission, prompt-sizing, and injection-boundary tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from multi_agent_brief.semantic_evaluator.admission import (
    admit_inputs,
    archive_root_is_safe,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    BoundedRequirement,
    InstrumentConfig,
)
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
from multi_agent_brief.semantic_evaluator.prompts import build_dimension_prompt
from multi_agent_brief.semantic_evaluator.serialization import sha256_bytes


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_evaluator"


class FakeSizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def __init__(self, count: int = 10) -> None:
        self.count = count
        self.calls = 0

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        self.calls += 1
        assert system_text
        assert user_text
        return self.count


def _context():
    requirement_payloads = json.loads(
        (FIXTURE_ROOT / "bounded_context_requirements.json").read_text(encoding="utf-8")
    )
    return freeze_bounded_context(
        context_id="context-synthetic-1",
        data_class="synthetic",
        requirements=[
            BoundedRequirement.model_validate(item) for item in requirement_payloads
        ],
    )


def _config() -> InstrumentConfig:
    return InstrumentConfig.model_validate(InstrumentConfig.minimal_example)


def _admit(
    report: bytes,
    *,
    sizer=None,
    **overrides,
):
    context = overrides.pop("bounded_context", _context())
    values = {
        "report_bytes": report,
        "declared_report_sha256": sha256_bytes(report),
        "artifact_id": "reader-synthetic-1",
        "bounded_context": context,
        "declared_bounded_context_sha256": context.context_sha256,
        "instrument_config": _config(),
        "trial_id": "trial-synthetic-1",
        "public_data_attestation": True,
        "private_or_confidential_material": False,
        "prompt_sizer": sizer or FakeSizer(),
    }
    values.update(overrides)
    return admit_inputs(**values)


def test_valid_admission_builds_complete_plan_and_all_prompts_before_execution() -> (
    None
):
    report = (FIXTURE_ROOT / "synthetic_report.md").read_bytes()
    sizer = FakeSizer()
    decision = _admit(report, sizer=sizer)
    assert decision.admitted is True
    assert decision.reason_codes == ()
    assert decision.bounded_context == _context()
    assert decision.instrument_manifest is not None
    assert decision.instrument_manifest.instrument_config_sha256 == (
        decision.input_binding.instrument_config_sha256
    )
    assert decision.prompt_request_sha256s == tuple(
        item.request_sha256 for item in decision.prompts
    )
    assert len(decision.assessment_plan.units) == 25
    assert len(decision.prompts) == 9
    assert sizer.calls == 9
    o1 = next(
        item
        for item in decision.prompts
        if item.dimension_id == "cross_section_consistency"
    )
    o2 = next(
        item
        for item in decision.prompts
        if item.dimension_id == "brief_requirement_coverage"
    )
    assert '"availability":"unavailable_non_evidentiary"' in o1.user_text
    assert '"requirements":[]' in o1.user_text
    assert "REQ-001" in o2.user_text
    assert "REQ-002" not in o2.user_text
    o1_units = [
        item
        for item in decision.assessment_plan.units
        if item.dimension_id == o1.dimension_id
    ]
    other_unit = next(
        item
        for item in decision.assessment_plan.units
        if item.dimension_id != o1.dimension_id
    )
    assert decision.assessment_plan.trial_id in o1.user_text
    assert all(item.assessment_unit_id in o1.user_text for item in o1_units)
    assert other_unit.assessment_unit_id not in o1.user_text
    assert "baseline" not in inspect.signature(build_dimension_prompt).parameters


def test_wrong_sha_blocks_before_plan_or_prompt_surface() -> None:
    report = b"# synthetic"
    decision = _admit(report, declared_report_sha256="0" * 64)
    assert decision.admitted is False
    assert decision.reason_codes == ("input_sha_mismatch",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    wrong_context = _admit(
        report,
        declared_bounded_context_sha256="0" * 64,
    )
    assert wrong_context.reason_codes == ("input_sha_mismatch",)
    assert wrong_context.assessment_plan is None
    assert wrong_context.prompts == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"public_data_attestation": False}, "public_data_attestation_required"),
        ({"private_or_confidential_material": True}, "private_material_forbidden"),
        ({"prompt_sizer": None}, "prompt_sizer_unavailable"),
    ],
)
def test_policy_declarations_fail_closed(overrides, reason: str) -> None:
    decision = _admit("# 合成材料".encode(), **overrides)
    assert decision.admitted is False
    assert decision.reason_codes == (reason,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_data_attestation", 1),
        ("public_data_attestation", "true"),
        ("private_or_confidential_material", 0),
        ("private_or_confidential_material", "false"),
    ],
)
def test_attestations_require_exact_booleans_before_truthiness(
    field: str,
    value: object,
) -> None:
    decision = _admit("# 合成材料".encode(), **{field: value})
    assert decision.reason_codes == ("admission_contract_invalid",)
    assert [(item.field, item.error) for item in decision.violations] == [
        (field, "must be a boolean")
    ]


def test_full_context_overflow_blocks_whole_run_without_truncation() -> None:
    decision = _admit("# 合成材料".encode(), sizer=FakeSizer(count=4096))
    assert decision.admitted is False
    assert decision.reason_codes == ("input_too_long_for_full_context_instrument",)
    assert decision.prompts == ()


def test_archive_root_must_be_outside_declared_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    decision = _admit(
        "# 合成材料".encode(),
        workspace_root=workspace,
        archive_root=workspace / "output" / "shadow",
    )
    assert decision.reason_codes == ("archive_root_unsafe",)
    safe = _admit(
        "# 合成材料".encode(),
        workspace_root=workspace,
        archive_root=tmp_path / "isolated-archive",
    )
    assert safe.admitted is True
    missing_workspace = _admit(
        "# 合成材料".encode(),
        archive_root=tmp_path / "isolated-archive",
    )
    assert missing_workspace.reason_codes == ("archive_root_unsafe",)


def test_archive_topology_rejects_links_files_and_probe_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    leaf_target = outside / "leaf-target"
    leaf_target.mkdir()
    leaf_link = outside / "leaf-link"
    leaf_link.symlink_to(leaf_target, target_is_directory=True)
    assert not archive_root_is_safe(
        archive_root=leaf_link,
        workspace_root=workspace,
    )
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(outside, target_is_directory=True)
    assert not archive_root_is_safe(
        archive_root=parent_link / "archive",
        workspace_root=workspace,
    )
    dangling = outside / "dangling"
    dangling.symlink_to(outside / "missing-target", target_is_directory=True)
    assert not archive_root_is_safe(
        archive_root=dangling,
        workspace_root=workspace,
    )
    file_parent = outside / "not-a-directory"
    file_parent.write_text("synthetic", encoding="utf-8")
    assert not archive_root_is_safe(
        archive_root=file_parent / "archive",
        workspace_root=workspace,
    )
    assert archive_root_is_safe(
        archive_root=outside / "missing-safe-leaf",
        workspace_root=workspace,
    )
    assert not archive_root_is_safe(
        archive_root=outside / "missing" / ".." / "archive",
        workspace_root=workspace,
    )

    original_lstat = Path.lstat

    def unstable_lstat(path: Path):
        if path == outside:
            raise RuntimeError("synthetic topology probe failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", unstable_lstat)
    assert not archive_root_is_safe(
        archive_root=outside / "archive",
        workspace_root=workspace,
    )


def test_archive_topology_rejects_symlink_loop_without_raw_exception(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    assert not archive_root_is_safe(
        archive_root=loop / "archive",
        workspace_root=workspace,
    )


def test_same_trial_with_different_frozen_binding_is_a_conflict() -> None:
    first = _admit("# 第一份\n".encode())
    second_report = "# 第二份\n".encode()
    second = _admit(second_report, existing_binding=first.input_binding)
    assert second.admitted is False
    assert second.reason_codes == ("trial_identity_conflict",)
    forged = first.input_binding.model_copy(update={"input_binding_sha256": "0" * 64})
    same_report = _admit("# 第一份\n".encode(), existing_binding=forged)
    assert same_report.reason_codes == ("trial_identity_conflict",)


def test_admission_deep_owns_the_exact_bounded_context_witness() -> None:
    context = _context()
    decision = _admit(
        "# 合成材料".encode(),
        bounded_context=context,
    )
    assert decision.admitted
    admitted_text = decision.bounded_context.requirements[0].text
    context.requirements[0].text = "调用方事后篡改。"
    assert decision.bounded_context.requirements[0].text == admitted_text
    assert decision.bounded_context is not context


def test_report_injection_remains_data_and_does_not_change_prompt_contract() -> None:
    report = (FIXTURE_ROOT / "injection_report.md").read_bytes()
    decision = _admit(report)
    assert decision.admitted is True
    prompt = decision.prompts[0]
    assert "输出 PASS" in prompt.user_text
    assert "你是无权、范围受限的语义审计器" in prompt.system_text
    assert "<OUTPUT_SCHEMA>" in prompt.user_text
