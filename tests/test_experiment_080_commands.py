from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main


SHA = "b" * 64


def _case_manifest() -> dict:
    return {
        "schema_version": "mabw.experiment_080.case.v1",
        "experiment_id": "MABW-080",
        "case_id": "weekly_public_001",
        "case_title": "Weekly public brief",
        "public_safe": True,
        "created_at": "2026-06-14T00:00:00Z",
        "repo_commit": "abc123",
        "conditions": ["baseline", "memory"],
        "frozen_fact_layer": {"manifest_path": "frozen_fact_layer.json"},
        "guidance_set": {"path": "guidance_set.json"},
        "allowed_claims": {"a_grade_requires_same_fact_layer": True},
    }


def _frozen_fact_layer() -> dict:
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
    }


def _guidance_set() -> dict:
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


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_case(case_dir: Path) -> None:
    case_dir.mkdir(parents=True)
    _write_json(case_dir / "case_manifest.json", _case_manifest())
    _write_json(case_dir / "frozen_fact_layer.json", _frozen_fact_layer())
    _write_json(case_dir / "guidance_set.json", _guidance_set())


def test_experiments_080_validate_case_json_ok(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["case_id"] == "weekly_public_001"
    assert sorted(payload["validated_files"]) == [
        "case_manifest.json",
        "frozen_fact_layer.json",
        "guidance_set.json",
    ]


def test_experiments_080_validate_case_missing_frozen_fact_layer_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    (case_dir / "frozen_fact_layer.json").unlink()

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(error["code"] == "missing_case_file" for error in payload["errors"])


def test_experiments_080_validate_case_source_candidates_only_fails(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    _write_json(
        case_dir / "frozen_fact_layer.json",
        {
            "schema_version": "mabw.experiment_080.frozen_fact_layer.v1",
            "source_run_id": "mabw-20260614T000000Z-test",
            "artifacts": [
                {
                    "artifact_id": "source_candidates",
                    "path": "output/intermediate/source_candidates.yaml",
                    "sha256": SHA,
                }
            ],
        },
    )

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(error["code"] == "source_plan_not_evidence" for error in payload["errors"])


def test_experiments_080_validate_case_is_read_only(tmp_path, capsys):
    case_dir = tmp_path / "weekly_public_001"
    _write_case(case_dir)
    before = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }

    rc = main(["experiments", "080", "validate-case", str(case_dir), "--json"])

    assert rc == 0
    json.loads(capsys.readouterr().out)
    after = {
        path.relative_to(case_dir).as_posix(): path.read_bytes()
        for path in sorted(case_dir.glob("*.json"))
    }
    assert after == before
