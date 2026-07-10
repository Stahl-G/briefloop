from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from multi_agent_brief.contracts.agent_artifact_intake import (
    AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
    INTAKE_PROJECTION_SCHEMA_VERSION,
    canonical_normalized_json_bytes,
    evaluate_agent_artifact_intake,
    normalize_claim_drafts,
    validate_intake_projection,
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


def _draft(**updates: object) -> dict[str, object]:
    draft: dict[str, object] = {
        "statement": "ExampleCo opened a demonstration facility.",
        "source_id": "SRC-001",
        "evidence_text": "ExampleCo said the facility opened on 1 June.",
        "source_url": "https://example.com/facility",
        "source_category": "news_media",
        "published_at": "2026-06-01",
        "claim_type": "fact",
        "confidence": "high",
    }
    draft.update(updates)
    return draft


def _screened_payload() -> dict[str, object]:
    return {
        "selected": [_candidate()],
        "excluded": [],
        "screening_policy": {"total_candidates": 1},
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


def test_candidate_intake_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    raw_payload = {"metadata": {"model": "example"}, "claims": [_candidate()]}
    _write_json(path, raw_payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "valid"
    assert result.normalized_payload == [_candidate()]
    assert result.normalizations[0]["operation"] == "root_wrapper"
    assert raw_payload == {"metadata": {"model": "example"}, "claims": [_candidate()]}


def test_candidate_intake_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    path.write_text("{broken", encoding="utf-8")

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.normalized_sha256 == ""
    assert result.findings[0]["code"] == "parse_error"
    assert result.findings[0]["path"] == "<root>"


def test_candidate_intake_alias_conflict(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    candidate = _candidate()
    candidate["claim_statement"] = "A conflicting statement."
    _write_json(path, [candidate])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.findings[0]["code"] == "alias_conflict"
    assert result.findings[0]["path"] == "candidate[0].statement"


def test_candidate_intake_plain_source_url(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    candidate = _candidate()
    candidate["source_url"] = "South China Morning Post"
    _write_json(path, [candidate])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.findings[0]["path"] == "candidate[0].source_url"
    assert result.normalized_payload[0]["source_url"] == "South China Morning Post"


def test_candidate_intake_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    _write_json(path, [_candidate(), _candidate()])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.validation_result == "candidate_claims_schema_error:duplicate_candidate_id:CAND-001"


def test_screened_bucket_aliases(tmp_path: Path) -> None:
    path = tmp_path / "screened_candidates.json"
    payload = {
        "selected_candidates": [_candidate()],
        "excluded_candidates": [],
        "screening_policy": {"total_candidates": 1},
    }
    _write_json(path, payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="screened_candidates")

    assert result.status == "valid"
    assert result.normalized_payload["selected"] == [_candidate()]
    assert result.normalized_payload["excluded"] == []
    assert {item["path"] for item in result.normalizations} == {
        "selected",
        "excluded",
    }


def test_screened_reason_not_reason_code(tmp_path: Path) -> None:
    path = tmp_path / "screened_candidates.json"
    payload = {
        "selected": [],
        "excluded": [
            {
                "candidate_id": "CAND-001",
                "reason": "Outside the requested scope.",
                "explanation": "The item does not match the requested market.",
            }
        ],
        "screening_policy": {"total_candidates": 1},
    }
    _write_json(path, payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="screened_candidates")

    assert result.status == "invalid"
    assert result.findings[0]["path"] == "excluded[0].reason_code"
    assert "reason_code" not in result.normalized_payload["excluded"][0]


def test_screened_universe_uses_intake(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidate_claims.json"
    screened_path = tmp_path / "screened_candidates.json"
    _write_json(candidate_path, {"claims": [_candidate()]})
    _write_json(
        screened_path,
        {
            "selected_candidates": [_candidate()],
            "excluded_candidates": [],
            "screening_policy": {"total_candidates": 1},
        },
    )
    candidate_result = evaluate_agent_artifact_intake(
        candidate_path, artifact_id="candidate_claims"
    )

    screened_result = evaluate_agent_artifact_intake(
        screened_path,
        artifact_id="screened_candidates",
        candidate_universe=candidate_result,
    )

    assert candidate_result.status == "valid"
    assert screened_result.status == "valid"
    assert screened_result.validation_result == "valid_screened_candidates_schema_normalized"


def test_screened_universe_duplicate_id(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidate_claims.json"
    screened_path = tmp_path / "screened_candidates.json"
    _write_json(candidate_path, [_candidate(), _candidate()])
    _write_json(screened_path, _screened_payload())
    candidate_result = evaluate_agent_artifact_intake(
        candidate_path, artifact_id="candidate_claims"
    )

    screened_result = evaluate_agent_artifact_intake(
        screened_path,
        artifact_id="screened_candidates",
        candidate_universe=candidate_result,
    )

    assert candidate_result.status == "invalid"
    assert screened_result.status == "invalid"
    assert "candidate_universe_duplicate_candidate_id" in screened_result.validation_result


def test_claim_draft_mechanical_normalization(tmp_path: Path) -> None:
    path = tmp_path / "claim_drafts.json"
    raw_payload = {
        "schema_version": "mabw.claim_drafts.v1",
        "claim_drafts": [
            {
                "claim_statement": "ExampleCo opened a demonstration facility.",
                "source_id": "SRC-001",
                "source_excerpt": "ExampleCo said the facility opened on 1 June.",
                "source_url": "https://example.com/facility",
                "source_category": "industry_news",
                "published_at": "2026-06-01",
                "claim_type": "fact",
                "confidence": 0.91,
            }
        ],
    }
    _write_json(path, raw_payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="claim_drafts")

    assert result.status == "valid"
    draft = result.normalized_payload["drafts"][0]
    assert draft["statement"] == "ExampleCo opened a demonstration facility."
    assert draft["evidence_text"] == "ExampleCo said the facility opened on 1 June."
    assert draft["confidence"] == "high"
    assert draft["source_category"] == "news_media"
    assert result.normalization_count == 5
    assert "claim_drafts" in raw_payload
    assert "drafts" not in raw_payload


def test_claim_draft_unknown_claim_type(tmp_path: Path) -> None:
    path = tmp_path / "claim_drafts.json"
    _write_json(
        path,
        {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [_draft(claim_type="product_launch")],
        },
    )

    result = evaluate_agent_artifact_intake(path, artifact_id="claim_drafts")

    assert result.status == "invalid"
    assert result.findings[0]["path"] == "drafts[0].claim_type"
    assert result.normalized_payload["drafts"][0]["claim_type"] == "product_launch"


def test_claim_draft_nested_claim_id(tmp_path: Path) -> None:
    path = tmp_path / "claim_drafts.json"
    _write_json(
        path,
        {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [_draft(metadata={"binding": {"claim_id": "CL-0001"}})],
        },
    )

    result = evaluate_agent_artifact_intake(path, artifact_id="claim_drafts")

    assert result.status == "invalid"
    assert result.findings[0]["path"] == "drafts[0].metadata.binding.claim_id"


def test_claim_draft_prose_not_identity_field(tmp_path: Path) -> None:
    path = tmp_path / "claim_drafts.json"
    _write_json(
        path,
        {
            "schema_version": "mabw.claim_drafts.v1",
            "drafts": [
                _draft(
                    evidence_text="The source document labels the example as CL-0001."
                )
            ],
        },
    )

    result = evaluate_agent_artifact_intake(path, artifact_id="claim_drafts")

    assert result.status == "valid"
    assert result.fatal_finding_count == 0


def test_normalize_claim_drafts_does_not_mutate_input() -> None:
    payload = {"claim_drafts": [{"claim_statement": "A", "confidence": 0.5}]}
    original = copy_payload = json.loads(json.dumps(payload))

    normalize_claim_drafts(payload)

    assert payload == original == copy_payload


@pytest.mark.parametrize(
    ("corruption", "expected_reason"),
    [
        (
            "normalization_count",
            "intake_projection normalization_count does not match normalizations",
        ),
        (
            "fatal_finding_count",
            "intake_projection fatal_finding_count does not match findings",
        ),
        (
            "normalization_entry",
            "intake_projection normalizations entries must be objects",
        ),
        ("finding_entry", "intake_projection findings entries must be objects"),
        (
            "raw_sha256",
            "intake_projection raw_sha256 must be a lowercase SHA-256 digest",
        ),
        (
            "normalized_sha256",
            "intake_projection normalized_sha256 must be empty or a lowercase SHA-256 digest",
        ),
    ],
)
def test_intake_projection_internal_binding_fails_closed(
    tmp_path: Path,
    corruption: str,
    expected_reason: str,
) -> None:
    path = tmp_path / "candidate_claims.json"
    _write_json(path, [_candidate()])
    projection = evaluate_agent_artifact_intake(
        path,
        artifact_id="candidate_claims",
    ).projection()
    if corruption == "normalization_count":
        projection["normalization_count"] = 1
    elif corruption == "fatal_finding_count":
        projection["fatal_finding_count"] = 1
    elif corruption == "normalization_entry":
        projection["normalizations"] = ["not-an-object"]
        projection["normalization_count"] = 1
    elif corruption == "finding_entry":
        projection["findings"] = ["not-an-object"]
    elif corruption == "raw_sha256":
        projection["raw_sha256"] = "not-a-digest"
    else:
        projection["normalized_sha256"] = "not-a-digest"

    reasons = validate_intake_projection(projection)

    assert expected_reason in reasons
