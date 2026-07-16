"""Non-editable wheel resource and instrument-identity parity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_PATHS = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
)
WHEEL_RESOURCE_NAMES = {
    f"multi_agent_brief/semantic_evaluator/{'/'.join(parts)}"
    for parts in RESOURCE_PATHS
}

WHEEL_PROBE = r"""
from copy import deepcopy
import inspect
import os
from pathlib import Path

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.composition import (
    compose_actual_laj,
    compose_matched_non_llm,
    verify_composition_record,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    DIMENSION_RESPONSE_SCHEMA_ID,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
    BoundedRequirement,
    CompositionRecord,
    DimensionResponse,
    InstrumentConfig,
    LajCompositionWitness,
    NoFindingResult,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.instrument import build_instrument_manifest
import multi_agent_brief.semantic_evaluator.instrument as instrument_module
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
import multi_agent_brief.semantic_evaluator.normalization as normalization_module
import multi_agent_brief.semantic_evaluator.parser as parser_module
import multi_agent_brief.semantic_evaluator.prompts as prompts_module
from multi_agent_brief.semantic_evaluator.resources import resource_sha256
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_json_text,
    canonical_sha256,
    schema_sha256,
    sha256_bytes,
)
import multi_agent_brief.semantic_evaluator.unit_planner as unit_planner_module
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    make_dimension_attempt_evidence,
)
import multi_agent_brief.semantic_evaluator.validator as validator_module


class Sizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def count_tokens(self, *, system_text, user_text):
        return 10

resource_paths = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
)
config = InstrumentConfig.model_validate(InstrumentConfig.minimal_example)
report = "# 合成 wheel parity 报告\n\n当前状态为 HOLD。\n".encode()
context = freeze_bounded_context(
    context_id="context-wheel-parity",
    data_class="synthetic",
    requirements=[
        BoundedRequirement(
            requirement_id="REQ-WHEEL-1",
            type="must_answer",
            text="说明当前状态。",
            source_locator="synthetic:wheel",
        )
    ],
)
request = {
    "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
    "artifact_id": "reader-wheel-parity",
    "trial_id": "trial-wheel-parity",
    "report_bytes_hex": report.hex(),
    "declared_report_sha256": sha256_bytes(report),
    "bounded_context": context,
    "declared_bounded_context_sha256": context.context_sha256,
    "instrument_config": config,
    "public_data_attestation": True,
    "private_or_confidential_material": False,
    "archive_root": None,
    "workspace_root": None,
}
decision = admit_inputs(
    request,
    prompt_sizer=Sizer(),
)
if not decision.admitted:
    raise RuntimeError("synthetic parity admission failed")
attempts = []
for prompt in decision.prompts:
    units = [
        item
        for item in decision.assessment_plan.units
        if item.dimension_id == prompt.dimension_id
    ]
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=decision.assessment_plan.trial_id,
        dimension_id=prompt.dimension_id,
        unit_results=[
            NoFindingResult(
                assessment_unit_id=item.assessment_unit_id,
                disposition="no_finding",
            )
            for item in units
        ],
    )
    attempts.append(
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            dimension_id=prompt.dimension_id,
            attempt_ordinal=1,
            prompt_request_sha256=prompt.request_sha256,
            status="completed",
            raw_response_bytes=canonical_json_bytes(response),
        )
    )
assembled = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=attempts,
)
baseline = build_baseline(
    report_evidence=decision.report_evidence,
    reader_artifact=decision.reader.artifact,
    bounded_context=decision.bounded_context,
)
matched = compose_matched_non_llm(
    report_evidence=decision.report_evidence,
    reader_artifact=decision.reader.artifact,
    bounded_context=decision.bounded_context,
)
actual = compose_actual_laj(assembled.witness)


def semantic_error_reason(callback):
    try:
        callback()
    except SemanticEvaluatorError as exc:
        return exc.reason_code
    return "unexpected_success"


def admission_reason(changes, *, prompt_sizer=Sizer(), existing_binding=None):
    candidate = deepcopy(request)
    candidate.update(changes)
    return list(
        admit_inputs(
            candidate,
            prompt_sizer=prompt_sizer,
            existing_binding=existing_binding,
        ).reason_codes
    )


tampered_attempt = attempts[0].model_copy(
    update={"evidence_sha256": "0" * 64}
)
parser_attempt = make_dimension_attempt_evidence(
    trial_id=decision.input_binding.trial_id,
    dimension_id=attempts[0].dimension_id,
    attempt_ordinal=1,
    prompt_request_sha256=attempts[0].prompt_request_sha256,
    status="completed",
    raw_response_bytes=b"\xff",
)
parser_projection = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=[parser_attempt, *attempts[1:]],
)
provider_projection = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=[
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            dimension_id=item.dimension_id,
            attempt_ordinal=1,
            prompt_request_sha256=item.prompt_request_sha256,
            status="failed",
            reason_code="provider_failed",
        )
        for item in attempts
    ],
)
witness_payload = assembled.witness.model_dump(mode="json")
witness_payload["run"]["run_status"] = "archive_failed"
witness_payload["witness_sha256"] = canonical_sha256(
    {
        key: value
        for key, value in witness_payload.items()
        if key != "witness_sha256"
    }
)
forged_witness = LajCompositionWitness.model_validate(witness_payload)
composition_payload = actual.model_dump(mode="json")
composition_payload["laj_run_status"] = "incomplete"
composition_payload["laj_validation_status"] = "incomplete"
composition_payload["composition_sha256"] = canonical_sha256(
    {
        key: value
        for key, value in composition_payload.items()
        if key != "composition_sha256"
    }
)
forged_composition = CompositionRecord.model_validate(composition_payload)
different_report = b"# different synthetic parity report\n"
failure_results = {
    "admission_extra": admission_reason({"unexpected": "synthetic"}),
    "admission_empty": admission_reason({"report_bytes_hex": ""}),
    "admission_utf8": admission_reason(
        {
            "report_bytes_hex": b"\xff".hex(),
            "declared_report_sha256": sha256_bytes(b"\xff"),
        }
    ),
    "admission_sha": admission_reason({"declared_report_sha256": "0" * 64}),
    "admission_policy": admission_reason({"public_data_attestation": False}),
    "admission_private": admission_reason(
        {"private_or_confidential_material": True}
    ),
    "admission_archive": admission_reason({"archive_root": "/tmp/synthetic"}),
    "admission_sizer": admission_reason({}, prompt_sizer=None),
    "admission_trial_conflict": admission_reason(
        {
            "report_bytes_hex": different_report.hex(),
            "declared_report_sha256": sha256_bytes(different_report),
        },
        existing_binding=decision.input_binding,
    ),
    "attempt_integrity": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[tampered_attempt, *attempts[1:]],
        )
    ),
    "parser_status": parser_projection.run.run_status,
    "parser_reasons": parser_projection.validation_report.reason_codes,
    "provider_status": provider_projection.run.run_status,
    "provider_validation": provider_projection.validation_report.validation_status,
    "witness_relation": semantic_error_reason(
        lambda: compose_actual_laj(forged_witness)
    ),
    "composition_relation": semantic_error_reason(
        lambda: verify_composition_record(
            forged_composition,
            witness=assembled.witness,
        )
    ),
}
wheel_root = Path(os.environ["SEMANTIC_EVALUATOR_WHEEL_ROOT"]).resolve()
module_files = [
    Path(inspect.getfile(module)).resolve()
    for module in (
        instrument_module,
        normalization_module,
        parser_module,
        prompts_module,
        unit_planner_module,
        validator_module,
    )
]
payload = {
    "schema_ids": [model.schema_id for model in SEMANTIC_EVALUATOR_CONTRACT_MODELS],
    "schema_hashes": {
        model.schema_id: schema_sha256(model)
        for model in SEMANTIC_EVALUATOR_CONTRACT_MODELS
    },
    "manifest": build_instrument_manifest(config).model_dump(mode="json"),
    "witness": assembled.witness.model_dump(mode="json"),
    "baseline": baseline.model_dump(mode="json"),
    "matched_composition": matched.model_dump(mode="json"),
    "actual_composition": actual.model_dump(mode="json"),
    "failure_results": failure_results,
    "resources": {
        "/".join(parts): resource_sha256(*parts)
        for parts in resource_paths
    },
    "loaded_from_extracted_wheel": all(
        str(path).startswith(str(wheel_root)) for path in module_files
    ),
}
print(canonical_json_text(payload))
"""


def _source_identity() -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(REPO_ROOT)
    probe = subprocess.run(
        [sys.executable, "-c", WHEEL_PROBE],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    return json.loads(probe.stdout.splitlines()[-1])


def _source_probe(*, optimized: bool) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(REPO_ROOT)
    command = [sys.executable]
    if optimized:
        command.append("-O")
    command.extend(["-c", WHEEL_PROBE])
    probe = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    return probe.stdout.splitlines()[-1]


def test_source_probe_is_byte_identical_under_python_optimization() -> None:
    assert _source_probe(optimized=False) == _source_probe(optimized=True)


def test_wheel_contains_all_resources_and_matches_source_identity(
    tmp_path: Path,
) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build_python = os.environ.get(
        "SEMANTIC_EVALUATOR_BUILD_PYTHON",
        sys.executable,
    )
    build = subprocess.run(
        [
            build_python,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    extract_root = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert WHEEL_RESOURCE_NAMES <= names
        archive.extractall(extract_root)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(extract_root)
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(extract_root)
    probe = subprocess.run(
        [sys.executable, "-c", WHEEL_PROBE],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    wheel_identity = json.loads(probe.stdout.splitlines()[-1])
    assert wheel_identity == _source_identity()
