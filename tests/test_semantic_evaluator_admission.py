"""Admission, prompt-sizing, and injection-boundary tests."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

import multi_agent_brief.semantic_evaluator.admission as admission_module
import multi_agent_brief.semantic_evaluator.instrument as instrument_module
import multi_agent_brief.semantic_evaluator.profile as profile_module
import multi_agent_brief.semantic_evaluator.snapshot as snapshot_module
from multi_agent_brief.semantic_evaluator.admission import (
    admit_inputs,
    archive_root_is_safe,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    AdmissionRequest,
    BoundedRequirement,
    InstrumentConfig,
)
from multi_agent_brief.semantic_evaluator.errors import (
    SemanticEvaluatorError,
    _is_current_instrument_source_failure,
)
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
from multi_agent_brief.semantic_evaluator.profile import LoadedProfile, load_profile
from multi_agent_brief.semantic_evaluator.prompts import build_dimension_prompt
from multi_agent_brief.semantic_evaluator.resources import EvaluatorResourceError
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
    valid_context = _context()
    context = overrides.pop("bounded_context", valid_context)
    dependency_sizer = overrides.pop(
        "prompt_sizer",
        sizer if sizer is not None else FakeSizer(),
    )
    existing_binding = overrides.pop("existing_binding", None)
    loaded_profile = overrides.pop("loaded_profile", None)
    values = {
        "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
        "report_bytes_hex": report.hex(),
        "declared_report_sha256": sha256_bytes(report),
        "artifact_id": "reader-synthetic-1",
        "bounded_context": context,
        "declared_bounded_context_sha256": valid_context.context_sha256,
        "instrument_config": _config(),
        "trial_id": "trial-synthetic-1",
        "public_data_attestation": True,
        "private_or_confidential_material": False,
        "archive_root": None,
        "workspace_root": None,
    }
    values.update(overrides)
    return admit_inputs(
        values,
        prompt_sizer=dependency_sizer,
        loaded_profile=loaded_profile,
        existing_binding=existing_binding,
    )


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


def test_admission_acquires_each_current_instrument_input_once(monkeypatch) -> None:
    calls: dict[str, int] = {
        "profile": 0,
        "system_prompt": 0,
        "dimension_prompt": 0,
        "checklist": 0,
        "component_source": 0,
    }
    original_profile_resource = profile_module.resource_text
    original_snapshot_resource = snapshot_module.resource_text
    original_source_hasher = instrument_module.source_sha256_for_module

    def counted_profile(*parts: str) -> str:
        calls["profile"] += 1
        return original_profile_resource(*parts)

    def counted_snapshot(*parts: str) -> str:
        key = {
            ("prompts", "system_v1.txt"): "system_prompt",
            ("prompts", "dimension_v1.txt"): "dimension_prompt",
            ("baselines", "structured_checklist_zh_v1.yaml"): "checklist",
        }[parts]
        calls[key] += 1
        return original_snapshot_resource(*parts)

    def counted_source(module_name: str) -> str:
        calls["component_source"] += 1
        return original_source_hasher(module_name)

    monkeypatch.setattr(profile_module, "resource_text", counted_profile)
    monkeypatch.setattr(snapshot_module, "resource_text", counted_snapshot)
    monkeypatch.setattr(
        instrument_module,
        "source_sha256_for_module",
        counted_source,
    )
    decision = _admit("# 合成单次快照\n".encode())
    assert decision.admitted is True
    assert calls == {
        "profile": 1,
        "system_prompt": 1,
        "dimension_prompt": 1,
        "checklist": 0,
        "component_source": 5,
    }


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_id", True),
        ("trial_id", True),
        ("bounded_context", True),
        ("instrument_config", True),
        ("archive_root", Path("/tmp/synthetic")),
    ],
)
def test_typed_admission_rejects_coerced_primitives_without_side_effects(
    field: str,
    value: object,
) -> None:
    sizer = FakeSizer()
    decision = _admit("# 合成材料".encode(), sizer=sizer, **{field: value})
    assert decision.reason_codes == ("admission_contract_invalid",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_request_shape_errors_are_value_free_and_preplan(mutation: str) -> None:
    payload = deepcopy(AdmissionRequest.minimal_example)
    secret = "PRIVATE-SYNTHETIC-CANARY"
    if mutation == "missing":
        payload.pop("trial_id")
    else:
        payload["attacker_extra"] = secret
    sizer = FakeSizer()
    decision = admit_inputs(payload, prompt_sizer=sizer)
    assert decision.reason_codes == ("admission_contract_invalid",)
    assert secret not in str(decision.violations)
    assert sizer.calls == 0


@pytest.mark.parametrize(
    ("report_bytes_hex", "reason"),
    [
        ("", "input_missing"),
        ("0", "admission_contract_invalid"),
        ("AA", "admission_contract_invalid"),
        ("zz", "admission_contract_invalid"),
        (b"# bytes are not a JSON string", "admission_contract_invalid"),
    ],
)
def test_report_hex_boundary_is_canonical_and_preplan(
    report_bytes_hex: object,
    reason: str,
) -> None:
    payload = deepcopy(AdmissionRequest.minimal_example)
    payload["report_bytes_hex"] = report_bytes_hex
    sizer = FakeSizer()
    decision = admit_inputs(payload, prompt_sizer=sizer)
    assert decision.reason_codes == (reason,)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


@pytest.mark.parametrize("raw", [b"\xff", b"# ok\x00private-synthetic-canary"])
def test_invalid_utf8_and_nul_are_value_free_and_preplan(raw: bytes) -> None:
    decision = _admit(raw)
    assert decision.reason_codes == ("input_not_utf8",)
    assert decision.violations == ()
    assert decision.report_evidence is None
    assert decision.assessment_plan is None
    assert decision.prompts == ()


def test_stale_context_content_hash_blocks_before_sizing() -> None:
    context = _context().model_copy(deep=True)
    context.requirements[0].text = "stale synthetic mutation"
    sizer = FakeSizer()
    decision = _admit(
        "# 合成材料".encode(),
        sizer=sizer,
        bounded_context=context,
    )
    assert decision.reason_codes == ("input_sha_mismatch",)
    assert decision.input_binding is None
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


def test_direct_prompt_assembly_rejects_stale_context_hash() -> None:
    decision = _admit("# 合成材料".encode())
    stale = decision.bounded_context.model_copy(deep=True)
    stale.requirements[0].source_locator = "brief:stale"
    dimension = load_profile().profile.dimensions[0]
    with pytest.raises(SemanticEvaluatorError, match="input_sha_mismatch"):
        build_dimension_prompt(
            reader_artifact=decision.reader.artifact,
            normalized_text=decision.reader.normalized_text,
            bounded_context=stale,
            dimension=dimension,
            assessment_plan=decision.assessment_plan,
        )


class _BadSizer(FakeSizer):
    def __init__(self, result: object = None, *, raises: bool = False) -> None:
        super().__init__()
        self.result = result
        self.raises = raises

    def count_tokens(self, *, system_text: str, user_text: str):
        self.calls += 1
        if self.raises:
            raise RuntimeError("synthetic sizing failure")
        return self.result


class _OSErrorSizer(FakeSizer):
    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        self.calls += 1
        raise OSError("/private/synthetic-customer/sizer")


class _HostileInt(int):
    def __lt__(self, _other):
        raise RuntimeError("synthetic hidden token comparison")

    def __add__(self, _other):
        raise RuntimeError("synthetic hidden token arithmetic")


class _ExplodingIdentitySizer:
    def __init__(self, failing_property: str) -> None:
        self.failing_property = failing_property
        self.id_reads = 0
        self.version_reads = 0
        self.calls = 0

    @property
    def sizer_id(self) -> str:
        self.id_reads += 1
        if self.failing_property == "sizer_id":
            raise RuntimeError("synthetic hidden sizer identity")
        return "fake-sizer"

    @property
    def sizer_version(self) -> str:
        self.version_reads += 1
        if self.failing_property == "sizer_version":
            raise RuntimeError("synthetic hidden sizer identity")
        return "v1"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        self.calls += 1
        return 10


@pytest.mark.parametrize(
    "sizer",
    [
        _BadSizer(True),
        _BadSizer("10"),
        _BadSizer(-1),
        _BadSizer(_HostileInt(10)),
        _BadSizer(raises=True),
        _OSErrorSizer(),
    ],
)
def test_prompt_sizer_failures_do_not_return_partial_prompts(sizer: _BadSizer) -> None:
    decision = _admit("# 合成材料".encode(), sizer=sizer)
    assert decision.reason_codes == ("prompt_sizer_unavailable",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 1


def test_prompt_sizer_identity_mismatch_is_preplan() -> None:
    sizer = FakeSizer()
    sizer.sizer_version = "wrong-version"
    decision = _admit("# 合成材料".encode(), sizer=sizer)
    assert decision.reason_codes == ("prompt_sizer_unavailable",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


def test_prompt_sizer_missing_and_malformed_identity_are_preplan() -> None:
    malformed_sizers = (
        object(),
        type(
            "MalformedIdentitySizer",
            (),
            {
                "sizer_id": True,
                "sizer_version": "v1",
                "count_tokens": lambda *_args, **_kwargs: pytest.fail(
                    "malformed identity reached count_tokens"
                ),
            },
        )(),
    )
    for sizer in malformed_sizers:
        decision = _admit("# 合成材料\n".encode(), sizer=sizer)
        assert decision.reason_codes == ("prompt_sizer_unavailable",)
        assert decision.assessment_plan is None
        assert decision.prompts == ()


@pytest.mark.parametrize(
    ("failing_property", "expected_id_reads", "expected_version_reads"),
    [("sizer_id", 1, 0), ("sizer_version", 1, 1)],
)
def test_prompt_sizer_identity_snapshot_is_one_read_and_zero_effect(
    failing_property: str,
    expected_id_reads: int,
    expected_version_reads: int,
    monkeypatch,
) -> None:
    sizer = _ExplodingIdentitySizer(failing_property)
    monkeypatch.setattr(
        instrument_module,
        "acquire_resource_snapshot",
        _fail_if_dependency_reaches_planning,
    )
    monkeypatch.setattr(
        admission_module,
        "build_assessment_plan",
        _fail_if_dependency_reaches_planning,
    )
    monkeypatch.setattr(
        admission_module,
        "build_dimension_prompt",
        _fail_if_dependency_reaches_planning,
    )
    decision = _admit("# 合成材料\n".encode(), sizer=sizer)
    assert decision.reason_codes == ("prompt_sizer_unavailable",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.id_reads == expected_id_reads
    assert sizer.version_reads == expected_version_reads
    assert sizer.calls == 0
    assert "synthetic hidden" not in repr(decision)


def test_full_context_overflow_blocks_whole_run_without_truncation() -> None:
    decision = _admit("# 合成材料".encode(), sizer=FakeSizer(count=4096))
    assert decision.admitted is False
    assert decision.reason_codes == ("input_too_long_for_full_context_instrument",)
    assert decision.prompts == ()


def test_archive_root_must_be_outside_declared_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    decision = _admit(
        "# 合成材料".encode(),
        archive_root=str(workspace / "output" / "shadow"),
        workspace_root=str(workspace),
    )
    assert decision.reason_codes == ("archive_root_unsafe",)
    safe = _admit(
        "# 合成材料".encode(),
        workspace_root=str(workspace),
        archive_root=str(tmp_path / "isolated-archive"),
    )
    assert safe.admitted is True
    missing_workspace = _admit(
        "# 合成材料".encode(),
        archive_root=str(tmp_path / "isolated-archive"),
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
    sizer = FakeSizer()
    second = _admit(
        second_report,
        sizer=sizer,
        existing_binding=first.input_binding,
    )
    assert second.admitted is False
    assert second.reason_codes == ("trial_identity_conflict",)
    assert sizer.calls == 0
    forged = first.input_binding.model_copy(update={"input_binding_sha256": "0" * 64})
    same_report = _admit("# 第一份\n".encode(), existing_binding=forged)
    assert same_report.reason_codes == ("trial_identity_conflict",)

    exact_replay = _admit(
        "# 第一份\n".encode(),
        existing_binding=first.input_binding,
    )
    assert exact_replay.admitted is True
    assert exact_replay.input_binding == first.input_binding


def _fail_if_dependency_reaches_planning(*_args, **_kwargs):
    pytest.fail("malformed optional dependency reached planning or prompt assembly")


@pytest.mark.parametrize(
    "loaded_profile",
    [{}, False, True, "synthetic-invalid-profile", object()],
    ids=["empty-mapping", "false", "true", "string", "object"],
)
def test_explicit_malformed_loaded_profile_is_typed_and_side_effect_free(
    loaded_profile,
    monkeypatch,
) -> None:
    sizer = FakeSizer()
    monkeypatch.setattr(
        admission_module,
        "build_assessment_plan",
        _fail_if_dependency_reaches_planning,
    )
    monkeypatch.setattr(
        admission_module,
        "build_dimension_prompt",
        _fail_if_dependency_reaches_planning,
    )
    decision = _admit(
        "# 合成材料\n".encode(),
        sizer=sizer,
        loaded_profile=loaded_profile,
    )
    assert decision.admitted is False
    assert decision.reason_codes == ("profile_invalid",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


@pytest.mark.parametrize("mutation", ["hash", "profile"])
def test_malformed_loaded_profile_instance_is_never_a_source_failure(
    mutation: str,
) -> None:
    current = load_profile()
    malformed = (
        LoadedProfile(profile=current.profile, profile_sha256="0" * 64)
        if mutation == "hash"
        else LoadedProfile(profile=object(), profile_sha256=current.profile_sha256)
    )
    sizer = FakeSizer()
    decision = _admit(
        "# 合成材料\n".encode(),
        sizer=sizer,
        loaded_profile=malformed,
    )
    assert decision.reason_codes == ("profile_invalid",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


@pytest.mark.parametrize(
    "existing_binding",
    [{}, False, True, "synthetic-invalid-binding", object()],
    ids=["empty-mapping", "false", "true", "string", "object"],
)
def test_explicit_malformed_existing_binding_is_typed_and_side_effect_free(
    existing_binding,
    monkeypatch,
) -> None:
    sizer = FakeSizer()
    monkeypatch.setattr(
        admission_module,
        "build_assessment_plan",
        _fail_if_dependency_reaches_planning,
    )
    monkeypatch.setattr(
        admission_module,
        "build_dimension_prompt",
        _fail_if_dependency_reaches_planning,
    )
    decision = _admit(
        "# 合成材料\n".encode(),
        sizer=sizer,
        existing_binding=existing_binding,
    )
    assert decision.admitted is False
    assert decision.reason_codes == ("trial_identity_conflict",)
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert sizer.calls == 0


def test_optional_dependency_precedence_and_explicit_valid_profile() -> None:
    sizer = FakeSizer()
    malformed = _admit(
        "# 合成材料\n".encode(),
        sizer=sizer,
        loaded_profile={},
        existing_binding=object(),
    )
    assert malformed.reason_codes == ("profile_invalid",)
    assert sizer.calls == 0

    valid = _admit(
        "# 合成材料\n".encode(),
        loaded_profile=load_profile(),
    )
    assert valid.admitted is True


@pytest.mark.parametrize("failure_site", ["profile", "component", "prompt"])
def test_current_instrument_source_failure_is_typed_and_value_free(
    monkeypatch,
    failure_site: str,
) -> None:
    hidden_detail = "/private/synthetic-customer/source.py"

    if failure_site == "profile":

        def fail_profile_resource(*_args) -> str:
            raise OSError(hidden_detail)

        monkeypatch.setattr(profile_module, "resource_text", fail_profile_resource)
    elif failure_site == "component":

        def fail_source_resolution(_module_name: str) -> str:
            raise EvaluatorResourceError("evaluator_source_unavailable")

        monkeypatch.setattr(
            instrument_module,
            "source_sha256_for_module",
            fail_source_resolution,
        )
    else:

        def fail_prompt_resource(*_parts: str) -> str:
            raise EvaluatorResourceError("evaluator_resource_unavailable")

        monkeypatch.setattr(
            snapshot_module,
            "resource_text",
            fail_prompt_resource,
        )
    sizer = FakeSizer()
    decision = _admit("# 合成材料\n".encode(), sizer=sizer)
    assert decision.admitted is False
    assert decision.reason_codes == ("instrument_manifest_mismatch",)
    assert decision.violations == ()
    assert decision.assessment_plan is None
    assert decision.prompts == ()
    assert hidden_detail not in repr(decision)
    assert sizer.calls == 0


def test_current_source_classifier_is_causal_bounded_and_non_laundering() -> None:
    source_failure = EvaluatorResourceError("evaluator_source_unavailable")
    assert _is_current_instrument_source_failure(source_failure) is True

    unrelated_source = OSError("/private/synthetic-customer/source.py")
    wrapped = SemanticEvaluatorError("profile_invalid")
    wrapped.__cause__ = unrelated_source
    assert _is_current_instrument_source_failure(wrapped) is False
    assert _is_current_instrument_source_failure(RuntimeError("synthetic")) is False
    assert _is_current_instrument_source_failure(AttributeError("synthetic")) is False

    marker_wrapped = RuntimeError("synthetic-wrapper")
    marker_wrapped.__cause__ = source_failure
    assert _is_current_instrument_source_failure(marker_wrapped) is False


def test_unrelated_prompt_and_sizer_failures_are_not_source_laundered(
    monkeypatch,
) -> None:
    hidden_detail = "/private/synthetic-customer/not-a-source-failure"

    def fail_prompt_build(**_kwargs) -> str:
        raise RuntimeError(hidden_detail)

    monkeypatch.setattr(admission_module, "build_dimension_prompt", fail_prompt_build)
    prompt_sizer = FakeSizer()
    prompt_decision = _admit("# 合成材料\n".encode(), sizer=prompt_sizer)
    assert prompt_decision.reason_codes == ("prompt_sizer_unavailable",)
    assert hidden_detail not in repr(prompt_decision)
    assert prompt_sizer.calls == 0

    monkeypatch.undo()
    runtime_sizer = _BadSizer(raises=True)
    sizer_decision = _admit("# 合成材料\n".encode(), sizer=runtime_sizer)
    assert sizer_decision.reason_codes == ("prompt_sizer_unavailable",)
    assert runtime_sizer.calls == 1


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
