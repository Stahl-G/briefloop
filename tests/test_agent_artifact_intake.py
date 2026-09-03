from __future__ import annotations

import hashlib
import json
from pathlib import Path

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
    INTAKE_PROJECTION_SCHEMA_VERSION,
    canonical_normalized_json_bytes,
    evaluate_agent_artifact_intake,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate(candidate_id: str = "CAND-001") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "statement": "ExampleCo opened a demonstration facility.",
        "evidence_text": "ExampleCo said the facility opened on 1 June.",
        "topic": "manufacturing",
        "claim_type": "fact",
        "source_url": "https://example.com/facility",
        "source_category": "news_media",
        "published_at": "2026-06-01",
        "confidence": "high",
    }


def _legacy_candidate(candidate_id: str) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "claim": f"Example claim for {candidate_id}.",
        "source_id": f"SRC-{candidate_id}",
    }


def test_candidate_intake_canonical(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    payload = [_candidate()]
    _write_json(path, payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "valid"
    assert result.normalization_count == 0
    assert result.raw_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.normalized_sha256 == hashlib.sha256(
        canonical_normalized_json_bytes(payload)
    ).hexdigest()
    assert result.transform_version == AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION
    assert result.projection()["schema_version"] == INTAKE_PROJECTION_SCHEMA_VERSION


def test_candidate_intake_requires_candidate_id(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    candidate = _candidate()
    candidate.pop("candidate_id")
    _write_json(path, [candidate])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.validation_result == "candidate_claims_schema_error:candidate[0].candidate_id"


def test_screened_reason_code_alias_is_normalized_once_at_intake(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate_claims.json"
    path = tmp_path / "screened_candidates.json"
    _write_json(candidate_path, [_legacy_candidate("CAND-001")])
    _write_json(
        path,
        {
            "selected": [],
            "excluded": [
                {
                    "candidate_id": "CAND-001",
                    "statement": "Example item outside the requested scope.",
                    "screening_reason_code": "capacity cap",
                    "explanation": "The capacity limit excluded this item.",
                }
            ],
            "screening_policy": {"total_candidates": 1},
        },
    )

    candidate = evaluate_agent_artifact_intake(
        candidate_path,
        artifact_id="candidate_claims",
    )
    result = evaluate_agent_artifact_intake(
        path,
        artifact_id="screened_candidates",
        candidate_universe=candidate,
    )

    assert result.status == "valid"
    excluded = result.normalized_payload["excluded"][0]
    assert excluded["reason_code"] == "capacity_capped"
    assert "screening_reason_code" not in excluded
    assert any(
        item["operation"] == "reason_code_alias" for item in result.normalizations
    )
