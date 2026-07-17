"""Hermetic source-clone E2E coverage for PR-SE-2 shadow execution."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from multi_agent_brief.semantic_evaluator.contracts import (
    BoundedRequirement,
    InstrumentConfig,
)
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    SYNTHETIC_PROMPT_SIZER_ID,
    SYNTHETIC_PROMPT_SIZER_VERSION,
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_evaluator"
FIXED_TIME = "2026-07-17T00:00:00Z"

PACKET_STATE_PATH_EVIDENCE = {
    "S01": "test_synthetic_shadow_run_publishes_and_exactly_replays",
    "S02": "test_openai_adapter_disables_sdk_retry_and_sends_only_frozen_responses_shape",
    "S03": "test_cli_duplicate_json_member_is_value_free_and_writes_no_archive",
    "S04": "test_hash_and_policy_failures_block_before_prompt_or_provider",
    "S05": "test_hash_and_policy_failures_block_before_prompt_or_provider",
    "S06": "test_prompt_sizers_are_local_strict_and_have_no_unknown_model_fallback",
    "S07": "test_adapter_unavailable_and_prompt_sizer_failure_write_no_final_archive",
    "S08": "test_openai_missing_key_is_value_free_and_zero_call",
    "S09": "test_synthetic_shadow_run_publishes_and_exactly_replays",
    "S10": "test_same_trial_changed_request_conflicts_without_overwrite",
    "S11": "test_incomplete_claimed_archive_is_never_repaired_or_overwritten",
    "S12": "test_self_consistent_rehash_of_semantic_projection_still_fails_replay",
    "S13": "test_existing_winner_is_verified_without_merge_or_overwrite",
    "S14": "test_retry_sequence_is_owned_by_runner_and_all_attempts_are_archived",
    "S15": "test_retry_exhaustion_is_complete_failure_evidence_not_semantic_success",
    "S16": "test_one_terminal_dimension_failure_is_linked_and_withholds_all_advice",
    "S17": "test_model_identity_drift_archives_terminal_reason_without_parsing_output",
    "S18": "test_parser_failure_is_not_retried_or_repaired",
    "S19": "test_security_failure_is_not_retried_and_displays_no_advice",
    "S20": "merged PR-SE-1 parser-validator adversarial suite",
    "S21": "test_one_terminal_dimension_failure_is_linked_and_withholds_all_advice",
    "S22": "test_synthetic_shadow_run_publishes_and_exactly_replays",
    "S23": "merged PR-SE-1 abstention and O3 handoff suite",
    "S24": "test_synthetic_shadow_run_publishes_and_exactly_replays",
    "S25": "test_frozen_provider_request_has_no_baseline_workflow_or_tool_surface",
    "S26": "test_publication_failure_before_claim_leaves_no_final_directory",
    "S27": "test_publication_failure_after_claim_remains_incomplete_and_future_fails_closed",
    "S28": "test_complete_write_or_reopen_failure_never_reports_success",
    "S29": "test_staging_cleanup_failure_does_not_change_verified_receipt",
    "S30": "test_non_editable_wheel_synthetic_cli_matches_source",
    "S31": "test_execution_identity_rotates_with_behavior_source",
    "S32": "test_shadow_cli_normal_and_optimized_outputs_are_equivalent",
    "S33": "test_no_normal_workflow_module_imports_semantic_evaluator",
    "S34": "test_existing_o3_contract_identity_and_registry_path_remain_unchanged",
    "S35": "test_only_frozen_live_adapter_imports_one_provider_sdk",
    "S36": "test_policy_records_retention_without_automatic_deletion",
}

PRD_SCENARIO_EVIDENCE = {
    f"SE-{ordinal:02d}": "packet state-path and merged PR-SE-1 evidence"
    for ordinal in range(1, 27)
}


def _write_inputs(tmp_path: Path, *, trial_id: str = "trial-shadow-e2e-v1"):
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    report = input_root / "report.md"
    report.write_bytes((FIXTURE_ROOT / "synthetic_report.md").read_bytes())
    requirements = [
        BoundedRequirement.model_validate(item)
        for item in json.loads(
            (FIXTURE_ROOT / "bounded_context_requirements.json").read_text(
                encoding="utf-8"
            )
        )
    ]
    context = freeze_bounded_context(
        context_id="context-shadow-e2e-v1",
        data_class="synthetic",
        requirements=requirements,
    )
    context_path = input_root / "bounded-context.json"
    context_path.write_text(
        json.dumps(
            context.model_dump(mode="json", warnings="error"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    config = InstrumentConfig.model_validate(
        {
            "schema_version": InstrumentConfig.schema_id,
            "instrument_config_id": "synthetic-shadow-instrument-v1",
            "provider_id": "synthetic_fixture",
            "model_id": "synthetic-fixture-v1",
            "model_version": "synthetic-fixture-v1",
            "language": "zh-CN",
            "decoding": {
                "temperature": 0.0,
                "top_p": 1.0,
                "max_output_tokens": 4096,
                "seed": None,
            },
            "retry_policy": {
                "max_attempts": 1,
                "retryable_reason_codes": [],
                "backoff_schedule_ms": [],
            },
            "prompt_sizer": {
                "sizer_id": SYNTHETIC_PROMPT_SIZER_ID,
                "sizer_version": SYNTHETIC_PROMPT_SIZER_VERSION,
                "max_context_tokens": 200000,
                "reserved_output_tokens": 4096,
            },
            "transport_policy": {
                "provider_transport_only": True,
                "model_tools": False,
                "browser": False,
                "cross_run_memory": False,
                "provider_file_search": False,
            },
        }
    )
    instrument_path = input_root / "instrument.json"
    instrument_path.write_text(
        json.dumps(
            config.model_dump(mode="json", warnings="error"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return {
        "report": report,
        "bounded_context": context_path,
        "profile": PROFILE_ID,
        "instrument": instrument_path,
        "trial_id": trial_id,
        "archive_root": tmp_path / "archives",
        "clock": lambda: FIXED_TIME,
        "sleep": lambda _seconds: None,
    }


def test_packet_and_prd_trace_tables_are_complete_and_finite() -> None:
    assert set(PACKET_STATE_PATH_EVIDENCE) == {
        f"S{ordinal:02d}" for ordinal in range(1, 37)
    }
    assert set(PRD_SCENARIO_EVIDENCE) == {
        f"SE-{ordinal:02d}" for ordinal in range(1, 27)
    }
    assert all(PACKET_STATE_PATH_EVIDENCE.values())
    assert all(PRD_SCENARIO_EVIDENCE.values())


def test_synthetic_shadow_run_publishes_and_exactly_replays(tmp_path: Path) -> None:
    invocation = _write_inputs(tmp_path)
    first = run_shadow(**invocation)
    assert first.to_dict() == {
        "ok": True,
        "replayed": False,
        "archive_complete": True,
        "archive_path": first.archive_path,
        "receipt_id": first.receipt_id,
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "qualification_eligible": False,
    }
    assert first.archive_path is not None
    archive = Path(first.archive_path)
    assert (archive / "COMPLETE").is_file()
    assert len(list((archive / "prompts").glob("*.json"))) == 9
    assert len(list((archive / "attempts").glob("*/*/transport.json"))) == 9

    adapter_touched = False

    def forbidden_factory(_execution):
        nonlocal adapter_touched
        adapter_touched = True
        raise AssertionError("exact replay must precede adapter construction")

    replay = run_shadow(**invocation, adapter_factory=forbidden_factory)
    assert replay.ok is True
    assert replay.replayed is True
    assert replay.receipt_id == first.receipt_id
    assert adapter_touched is False


def test_same_trial_changed_request_conflicts_without_overwrite(tmp_path: Path) -> None:
    invocation = _write_inputs(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    report = Path(invocation["report"])
    report.write_text(
        report.read_text(encoding="utf-8") + "\n新增合成行。\n", encoding="utf-8"
    )
    changed = run_shadow(**invocation)
    assert changed.ok is False
    assert changed.reason_codes == ("shadow_request_conflict",)
    assert Path(first.archive_path or "").joinpath("COMPLETE").is_file()


def test_tampered_complete_archive_fails_closed_before_adapter(tmp_path: Path) -> None:
    invocation = _write_inputs(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    archive = Path(first.archive_path or "")
    output = next((archive / "attempts").glob("*/*/output.txt"))
    output.write_bytes(output.read_bytes() + b" ")
    touched = False

    def forbidden_factory(_execution):
        nonlocal touched
        touched = True
        raise AssertionError

    result = run_shadow(**invocation, adapter_factory=forbidden_factory)
    assert result.ok is False
    assert result.reason_codes == ("shadow_archive_invalid",)
    assert touched is False


def test_shadow_cli_normal_and_optimized_outputs_are_equivalent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fixtures = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"

    def invoke(*, optimized: bool, archive_root: Path):
        argv = [
            "experiments",
            "laj",
            "shadow-run",
            "--report",
            str(fixtures / "report.md"),
            "--bounded-context",
            str(fixtures / "bounded_context.json"),
            "--profile",
            PROFILE_ID,
            "--instrument",
            str(fixtures / "instrument.json"),
            "--trial-id",
            "trial-optimized-parity-v1",
            "--archive-root",
            str(archive_root),
            "--json",
        ]
        script = (
            "import json; from multi_agent_brief.cli.main import main; "
            f"raise SystemExit(main(json.loads({json.dumps(json.dumps(argv))})))"
        )
        command = [sys.executable]
        if optimized:
            command.append("-O")
        command.extend(["-c", script])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "src")
        env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout), Path(
            json.loads(completed.stdout)["archive_path"]
        )

    normal, normal_archive = invoke(
        optimized=False, archive_root=tmp_path / "normal-archive"
    )
    optimized, optimized_archive = invoke(
        optimized=True, archive_root=tmp_path / "optimized-archive"
    )
    stable_keys = {
        "ok",
        "replayed",
        "archive_complete",
        "run_status",
        "validation_status",
        "reason_codes",
        "qualification_eligible",
    }
    assert {key: normal[key] for key in stable_keys} == {
        key: optimized[key] for key in stable_keys
    }
    for relative in (
        "request.json",
        "execution_manifest.json",
        "input_binding.json",
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
    ):
        assert (normal_archive / relative).read_bytes() == (
            optimized_archive / relative
        ).read_bytes()
