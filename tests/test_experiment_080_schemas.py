from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.experiments.experiment_080 import (
    validate_case_dir,
    validate_case_manifest,
    validate_frozen_fact_layer,
    validate_guidance_set,
    validate_run_record,
    validate_scorecard,
)


SHA = "a" * 64


def _valid_case_manifest() -> dict:
    return {
        "schema_version": "mabw.experiment_080.case.v1",
        "experiment_id": "MABW-080",
        "case_id": "solar_public_001",
        "case_title": "Public solar policy briefing",
        "public_safe": True,
        "created_at": "2026-06-14T00:00:00Z",
        "repo_commit": "abc123",
        "conditions": ["baseline", "memory", "prompt_only"],
        "frozen_fact_layer": {"manifest_path": "frozen_fact_layer.json"},
        "guidance_set": {"path": "guidance_set.json"},
        "allowed_claims": {"a_grade_requires_same_fact_layer": True},
    }


def _valid_frozen_fact_layer() -> dict:
    return {
        "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
        "source_run_id": "mabw-20260614T000000Z-test",
        "source_archive_path": "output/runs/mabw-20260614T000000Z-test/manifest.json",
        "artifacts": [
            {
                "artifact_id": "durable_source_evidence_or_source_pack",
                "path": "input/sources/source_pack.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "input_classification",
                "path": "output/input_classification.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "candidate_claims",
                "path": "output/intermediate/candidate_claims.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "screened_candidates",
                "path": "output/intermediate/screened_candidates.json",
                "sha256": SHA,
            },
            {
                "artifact_id": "claim_ledger",
                "path": "output/intermediate/claim_ledger.json",
                "sha256": SHA,
            },
        ],
        "notes": ["source_candidates.yaml is excluded unless evidence-bearing"],
    }


def _valid_guidance_set() -> dict:
    return {
        "schema_version": "mabw.experiment_080.guidance_set.v1",
        "entries": [
            {
                "entry_id": "AG-0001",
                "guidance_text": "Lead with business implication before news recap.",
                "source": "improvement_ledger",
                "expected_manifestation": "Business implication appears before news recap.",
                "relevance_rule": "Applies to management-facing market briefs.",
            }
        ],
    }


def _valid_scorecard() -> dict:
    return {
        "schema_version": "mabw.experiment_080.scorecard.v1",
        "experiment_id": "MABW-080",
        "case_id": "solar_public_001",
        "condition": "memory",
        "run_id": "mabw-20260614T000000Z-test",
        "validity_class": "A_controlled",
        "control_integrity": {
            "terminal_workflow": True,
            "run_integrity_clean": True,
            "artifact_registry_valid": True,
            "quality_gates_passed": True,
            "archive_present": True,
        },
        "frozen_fact_layer": {"matches_case": True, "mismatches": []},
        "reader_clean": {"pass": True},
        "guidance_scores": [
            {
                "entry_id": "AG-0001",
                "relevant": True,
                "manifestation_score": 2,
                "overapplication": False,
                "assessment_method": "human",
                "evidence_excerpt": "The brief starts with the business implication.",
            }
        ],
        "regression": {},
        "notes": [],
    }


def _valid_run_record() -> dict:
    return {
        "schema_version": "mabw.experiment_080.run_record.v1",
        "experiment_id": "MABW-080",
        "case_id": "solar_public_001",
        "condition": "memory",
        "run_id": "mabw-20260614T000000Z-test",
        "workspace_path": "<redacted-workspace>",
        "run_archive_path": "output/runs/mabw-20260614T000000Z-test/manifest.json",
        "repo_commit": "abc123",
        "runtime": "claude",
        "model": {
            "epistemic_status": "operator_reported",
            "value": "operator-reported-test-model",
        },
        "run_integrity": {
            "status": "clean",
            "reference_eligible": True,
        },
        "imported_fact_layer": {
            "matches_case_frozen_fact_layer": True,
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_case(case_dir: Path) -> None:
    case_dir.mkdir(parents=True)
    _write_json(case_dir / "case_manifest.json", _valid_case_manifest())
    _write_json(case_dir / "frozen_fact_layer.json", _valid_frozen_fact_layer())
    _write_json(case_dir / "guidance_set.json", _valid_guidance_set())


def _codes(diagnostics) -> set[str]:
    return {diagnostic.code for diagnostic in diagnostics}


def test_experiment_080_valid_minimal_case_passes(tmp_path):
    case_dir = tmp_path / "solar_public_001"
    _write_case(case_dir)

    result = validate_case_dir(case_dir)

    assert result["ok"] is True
    assert result["case_id"] == "solar_public_001"
    assert result["conditions"] == ["baseline", "memory", "prompt_only"]
    assert result["errors"] == []


def test_experiment_080_unknown_condition_rejects():
    manifest = _valid_case_manifest()
    manifest["conditions"] = ["baseline", "magic"]

    diagnostics = validate_case_manifest(manifest)

    assert "unknown_condition" in _codes(diagnostics)


def test_experiment_080_guidance_manifested_claim_does_not_require_prompt_only():
    manifest = _valid_case_manifest()
    manifest["conditions"] = ["baseline", "memory"]
    manifest["allowed_claims"] = {"guidance_manifested": True}

    diagnostics = validate_case_manifest(manifest)

    assert "mechanism_claim_requires_prompt_only" not in _codes(diagnostics)


def test_experiment_080_mechanism_claim_requires_prompt_only_condition():
    manifest = _valid_case_manifest()
    manifest["conditions"] = ["baseline", "memory"]
    manifest["allowed_claims"] = {"memory_mechanism_adds_over_prompt": True}

    diagnostics = validate_case_manifest(manifest)

    assert "mechanism_claim_requires_prompt_only" in _codes(diagnostics)


def test_experiment_080_requires_baseline_and_memory_conditions():
    manifest = _valid_case_manifest()
    manifest["conditions"] = ["baseline"]

    diagnostics = validate_case_manifest(manifest)

    assert "missing_measurement_condition" in _codes(diagnostics)


def test_experiment_080_requires_non_empty_guidance_entries():
    guidance = _valid_guidance_set()
    guidance["entries"] = []

    diagnostics = validate_guidance_set(guidance)

    assert "empty_guidance_entries" in _codes(diagnostics)


def test_experiment_080_requires_improvement_ledger_guidance_entry():
    guidance = _valid_guidance_set()
    guidance["entries"][0]["source"] = "manual"

    diagnostics = validate_guidance_set(guidance)

    assert "missing_improvement_ledger_guidance" in _codes(diagnostics)


def test_experiment_080_missing_required_frozen_fact_artifact_rejects():
    fact_layer = _valid_frozen_fact_layer()
    fact_layer["artifacts"] = [
        artifact for artifact in fact_layer["artifacts"] if artifact["artifact_id"] != "claim_ledger"
    ]

    diagnostics = validate_frozen_fact_layer(fact_layer)

    assert "missing_required_fact_artifacts" in _codes(diagnostics)


def test_experiment_080_source_candidates_only_fact_layer_rejects():
    fact_layer = _valid_frozen_fact_layer()
    fact_layer["artifacts"] = [
        {
            "artifact_id": "source_candidates",
            "path": "output/intermediate/source_candidates.yaml",
            "sha256": SHA,
        }
    ]

    diagnostics = validate_frozen_fact_layer(fact_layer)

    assert "source_plan_not_evidence" in _codes(diagnostics)
    assert "missing_required_fact_artifacts" in _codes(diagnostics)


def test_experiment_080_scorecard_manifestation_score_four_rejects():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"][0]["manifestation_score"] = 4

    diagnostics = validate_scorecard(scorecard)

    assert "invalid_manifestation_score" in _codes(diagnostics)


def test_experiment_080_accepts_invalid_incomplete_and_fact_layer_mismatch():
    for validity_class in ("invalid_incomplete", "invalid_fact_layer_mismatch"):
        scorecard = _valid_scorecard()
        scorecard["validity_class"] = validity_class
        scorecard["control_integrity"]["terminal_workflow"] = False
        scorecard["frozen_fact_layer"]["matches_case"] = False

        diagnostics = validate_scorecard(scorecard)

        assert "invalid_validity_class" not in _codes(diagnostics)
        assert "a_controlled_requirements_not_met" not in _codes(diagnostics)


def test_experiment_080_rejects_old_agent_assisted_method():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"][0]["assessment_method"] = "agent_assisted"

    diagnostics = validate_scorecard(scorecard)

    assert "invalid_assessment_method" in _codes(diagnostics)


def test_experiment_080_a_controlled_rejects_llm_only_guidance_score():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"][0]["assessment_method"] = "llm_only"

    diagnostics = validate_scorecard(scorecard)

    assert "a_controlled_requires_human_assessment" in _codes(diagnostics)


def test_experiment_080_a_controlled_rejects_string_false_requirement_values():
    scorecard = _valid_scorecard()
    scorecard["control_integrity"] = {
        "terminal_workflow": "false",
        "run_integrity_clean": "false",
        "artifact_registry_valid": "false",
        "quality_gates_passed": "false",
        "archive_present": "false",
    }
    scorecard["frozen_fact_layer"]["matches_case"] = "false"
    scorecard["reader_clean"]["pass"] = "false"

    diagnostics = validate_scorecard(scorecard)

    codes = _codes(diagnostics)
    assert "invalid_a_controlled_requirement_type" in codes
    assert "a_controlled_requirements_not_met" in codes


def test_experiment_080_scorecard_requires_non_empty_guidance_scores():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"] = []

    diagnostics = validate_scorecard(scorecard)

    assert "empty_guidance_scores" in _codes(diagnostics)


def test_experiment_080_scorecard_rejects_duplicate_guidance_score_entry_ids():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"].append(dict(scorecard["guidance_scores"][0]))

    diagnostics = validate_scorecard(scorecard)

    assert "duplicate_guidance_score_entry_id" in _codes(diagnostics)


def test_experiment_080_score_three_requires_overapplication_true():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"][0]["manifestation_score"] = 3
    scorecard["guidance_scores"][0]["overapplication"] = False

    diagnostics = validate_scorecard(scorecard)

    assert "overapplication_score_mismatch" in _codes(diagnostics)


def test_experiment_080_overapplication_true_requires_score_three():
    scorecard = _valid_scorecard()
    scorecard["guidance_scores"][0]["manifestation_score"] = 2
    scorecard["guidance_scores"][0]["overapplication"] = True

    diagnostics = validate_scorecard(scorecard)

    assert "overapplication_score_mismatch" in _codes(diagnostics)


def test_experiment_080_valid_run_record_passes():
    diagnostics = validate_run_record(_valid_run_record())

    assert diagnostics == []


def test_experiment_080_contaminated_run_record_cannot_be_reference_eligible():
    run_record = _valid_run_record()
    run_record["run_integrity"] = {
        "status": "contaminated",
        "reference_eligible": True,
    }

    diagnostics = validate_run_record(run_record)

    assert "contaminated_run_reference_eligible" in _codes(diagnostics)


def test_experiment_080_a_controlled_requires_matching_fact_layer():
    scorecard = _valid_scorecard()
    scorecard["frozen_fact_layer"]["matches_case"] = False

    diagnostics = validate_scorecard(scorecard)

    assert "a_controlled_requirements_not_met" in _codes(diagnostics)


def test_experiment_080_public_safe_case_rejects_private_path(tmp_path):
    case_dir = tmp_path / "solar_public_001"
    _write_case(case_dir)
    guidance = _valid_guidance_set()
    private_path = "/" + "Users/private/company/source.md"
    guidance["entries"][0]["guidance_text"] = f"See {private_path}"
    _write_json(case_dir / "guidance_set.json", guidance)

    result = validate_case_dir(case_dir)

    assert result["ok"] is False
    assert any(error["code"] == "public_safe_private_path" for error in result["errors"])
