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
    evaluate_workspace_agent_artifact_intakes,
    normalize_claim_drafts,
    validate_intake_projection,
    validate_registry_intake_context,
    validate_workspace_intake_consumption_context,
)
from multi_agent_brief.orchestrator.runtime_state.artifact_paths import (
    agent_artifact_paths_from_contracts,
)
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.orchestrator.runtime_state import (
    check_runtime_state,
    initialize_runtime_state,
)
from multi_agent_brief.product.materiality_selection import (
    project_workspace_materiality_selection,
)
from multi_agent_brief.quality_gates.state import _coverage_omission_projection


ROOT = Path(__file__).resolve().parent.parent


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


def _legacy_candidate(candidate_id: str) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "claim": f"Example claim for {candidate_id}.",
        "source_id": f"SRC-{candidate_id}",
    }


def _selected_candidate(candidate_id: str) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "statement": f"Example selected claim for {candidate_id}.",
        "evidence_text": "Public-safe example evidence.",
        "source_id": "SRC-001",
        "published_at": "2026-06-01",
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


def test_candidate_intake_requires_candidate_id(tmp_path: Path) -> None:
    path = tmp_path / "candidate_claims.json"
    candidate = _candidate()
    candidate.pop("candidate_id")
    _write_json(path, [candidate])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.validation_result == "candidate_claims_schema_error:candidate[0].candidate_id"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_url", "South China Morning Post"),
        ("source_url", ""),
        ("source_category", "unregistered_category"),
        ("published_at", ""),
    ],
)
def test_legacy_candidate_supplied_identity_fields_are_validated(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = tmp_path / "candidate_claims.json"
    candidate = _legacy_candidate("CAND-001")
    candidate[field] = value
    _write_json(path, [candidate])

    result = evaluate_agent_artifact_intake(path, artifact_id="candidate_claims")

    assert result.status == "invalid"
    assert result.validation_result == f"candidate_claims_schema_error:candidate[0].{field}"


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
                "statement": "Example item outside the requested scope.",
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


@pytest.mark.parametrize(
    ("stale_artifact_id", "reason_prefix"),
    [
        ("candidate_claims", "candidate_claims dependency:"),
        ("screened_candidates", "screened_candidates artifact record"),
    ],
    ids=["INTAKE-CONSUME-01-stale-universe", "INTAKE-CONSUME-02-stale-screening"],
)
def test_screened_intake_consumption_rejects_stale_registry_dependencies(
    tmp_path: Path,
    stale_artifact_id: str,
    reason_prefix: str,
) -> None:
    candidate_path = tmp_path / "candidate_claims.json"
    screened_path = tmp_path / "screened_candidates.json"
    _write_json(candidate_path, [_legacy_candidate("CAND-001")])
    _write_json(
        screened_path,
        {
            "selected": [_selected_candidate("CAND-001")],
            "excluded": [],
            "screening_policy": {"total_candidates": 1},
        },
    )
    bundle = evaluate_workspace_agent_artifact_intakes(
        tmp_path,
        artifact_paths={
            "candidate_claims": candidate_path,
            "screened_candidates": screened_path,
        },
    )
    assert bundle.candidate_claims is not None
    assert bundle.screened_candidates is not None
    registry = {
        "run_id": "run-current",
        "artifacts": {
            "candidate_claims": {
                "status": "valid",
                "validation_result": bundle.candidate_claims.validation_result,
                "sha256": bundle.candidate_claims.raw_sha256,
                "intake_projection": bundle.candidate_claims.projection(),
            },
            "screened_candidates": {
                "status": "valid",
                "validation_result": bundle.screened_candidates.validation_result,
                "sha256": bundle.screened_candidates.raw_sha256,
                "intake_projection": bundle.screened_candidates.projection(),
            },
        },
    }
    registry["artifacts"][stale_artifact_id].update(
        {
            "status": "stale",
            "validation_result": "stale_after_supersede",
        }
    )

    reasons = validate_workspace_intake_consumption_context(
        registry,
        expected_run_id="run-current",
        bundle=bundle,
        artifact_id="screened_candidates",
    )

    assert any(reason.startswith(reason_prefix) for reason in reasons)


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


def test_screened_candidate_requires_candidate_id_without_universe(tmp_path: Path) -> None:
    path = tmp_path / "screened_candidates.json"
    payload = _screened_payload()
    payload["selected"][0].pop("candidate_id")
    _write_json(path, payload)

    result = evaluate_agent_artifact_intake(path, artifact_id="screened_candidates")

    assert result.status == "invalid"
    assert result.validation_result == (
        "screened_candidates_schema_error:selected[0].candidate_id"
    )


def test_workspace_intake_bundle_rejects_missing_candidate_universe(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    _write_json(intermediate / "screened_candidates.json", _screened_payload())

    bundle = evaluate_workspace_agent_artifact_intakes(workspace)

    assert bundle.candidate_claims is None
    assert bundle.screened_candidates is not None
    assert bundle.screened_candidates.status == "invalid"
    assert bundle.screened_candidates.validation_result == (
        "screened_candidates_schema_error:candidate_universe_invalid"
    )


@pytest.mark.parametrize(
    ("screened_payload", "expected_result"),
    [
        (
            [
                {"candidate_id": "CAND-999", "screening_status": "selected"},
                {
                    "candidate_id": "CAND-002",
                    "screening_status": "rejected",
                    "screening_reason": "outside scope",
                },
            ],
            "screened_candidates_schema_error:candidate[0].unknown_candidate_id:CAND-999",
        ),
        (
            [{"candidate_id": "CAND-001", "screening_status": "selected"}],
            "screened_candidates_schema_error:candidate_universe_id_coverage_mismatch",
        ),
        (
            [
                {"candidate_id": "CAND-001", "screening_status": "selected"},
                {
                    "candidate_id": "CAND-001",
                    "screening_status": "rejected",
                    "screening_reason": "duplicate",
                },
            ],
            "screened_candidates_schema_error:duplicate_screened_candidate_id:CAND-001",
        ),
    ],
    ids=[
        "INTAKE-UNIVERSE-08-unknown",
        "INTAKE-UNIVERSE-08-coverage",
        "INTAKE-UNIVERSE-08-duplicate",
    ],
)
def test_workspace_intake_bundle_binds_legacy_list_universe(
    tmp_path: Path,
    screened_payload: list[dict[str, str]],
    expected_result: str,
) -> None:
    workspace = tmp_path / "ws"
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    _write_json(
        intermediate / "candidate_claims.json",
        [_legacy_candidate("CAND-001"), _legacy_candidate("CAND-002")],
    )
    _write_json(intermediate / "screened_candidates.json", screened_payload)

    bundle = evaluate_workspace_agent_artifact_intakes(workspace)

    assert bundle.screened_candidates is not None
    assert bundle.screened_candidates.status == "invalid"
    assert bundle.screened_candidates.validation_result == expected_result


def test_workspace_intake_bundle_requires_object_universe_coverage_without_total(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True)
    _write_json(
        intermediate / "candidate_claims.json",
        [_legacy_candidate("CAND-001"), _legacy_candidate("CAND-002")],
    )
    _write_json(
        intermediate / "screened_candidates.json",
        {
            "selected": [_selected_candidate("CAND-001")],
            "excluded": [],
            "screening_policy": {"method": "deterministic_test"},
        },
    )

    bundle = evaluate_workspace_agent_artifact_intakes(workspace)

    assert bundle.screened_candidates is not None
    assert bundle.screened_candidates.status == "invalid"
    assert bundle.screened_candidates.validation_result == (
        "screened_candidates_schema_error:candidate_universe_id_coverage_mismatch"
    )


@pytest.mark.parametrize(
    ("candidate_payload", "screened_payload", "expected_result"),
    [
        (
            [_legacy_candidate("CAND-001")],
            {
                "selected": [_selected_candidate("CAND-999")],
                "excluded": [],
                "screening_policy": {"total_candidates": 1},
            },
            "screened_candidates_schema_error:selected[0].unknown_candidate_id:CAND-999",
        ),
        (
            [_legacy_candidate("CAND-001"), _legacy_candidate("CAND-001")],
            {
                "selected": [_selected_candidate("CAND-001")],
                "excluded": [],
                "screening_policy": {"method": "deterministic_test"},
            },
            "screened_candidates_schema_error:candidate_universe_duplicate_candidate_id:CAND-001",
        ),
        (
            [_legacy_candidate("CAND-001"), _legacy_candidate("CAND-002")],
            {
                "selected": [_selected_candidate("CAND-001")],
                "excluded": [],
                "screening_policy": {"total_candidates": 1},
            },
            "screened_candidates_schema_error:candidate_universe_count_mismatch",
        ),
    ],
    ids=["INTAKE-UNIVERSE-03", "INTAKE-UNIVERSE-04", "INTAKE-UNIVERSE-05"],
)
def test_workspace_intake_bundle_owns_screened_universe_verdict(
    tmp_path: Path,
    candidate_payload: list[dict[str, str]],
    screened_payload: dict[str, object],
    expected_result: str,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "project:\n  name: Intake Consumer Equivalence\n"
        "output:\n  path: output\n"
        "input:\n  path: input\n",
        encoding="utf-8",
    )
    (workspace / "user.md").write_text("# User\n", encoding="utf-8")
    (workspace / "input").mkdir()
    initialize_runtime_state(runtime="operator", workspace=workspace, repo_workdir=ROOT)
    intermediate = workspace / "output" / "intermediate"
    _write_json(intermediate / "candidate_claims.json", candidate_payload)
    _write_json(intermediate / "screened_candidates.json", screened_payload)

    bundle = evaluate_workspace_agent_artifact_intakes(workspace)
    state = check_runtime_state(workspace=workspace, repo_workdir=ROOT)
    registry_record = state["artifact_registry"]["artifacts"]["screened_candidates"]
    materiality = project_workspace_materiality_selection(workspace)
    coverage = _coverage_omission_projection(
        workspace=workspace,
        markdown="## Executive Summary\n",
        ledger=ClaimLedger(),
    )

    assert bundle.candidate_claims is not None
    assert bundle.screened_candidates is not None
    assert bundle.screened_candidates.status == "invalid"
    assert bundle.screened_candidates.validation_result == expected_result
    assert registry_record["status"] == "invalid"
    assert materiality["status"] == "invalid_screened_candidates"
    assert coverage["status"] == "invalid"
    assert {
        registry_record["validation_result"],
        materiality["reason"],
        coverage["screened_candidates_validation_result"],
        coverage["not_interpreted_reason"],
    } == {expected_result}


def test_quality_and_materiality_bind_contract_resolved_intake_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    intermediate = workspace / "output" / "intermediate"
    custom = workspace / "custom"
    intermediate.mkdir(parents=True)
    custom.mkdir()
    (workspace / "config.yaml").write_text(
        "project:\n  name: Contract Path Consumer Equivalence\n",
        encoding="utf-8",
    )
    _write_json(intermediate / "candidate_claims.json", {"malformed": True})
    _write_json(intermediate / "screened_candidates.json", {"malformed": True})
    _write_json(custom / "candidate_claims.json", [_legacy_candidate("CAND-001")])
    _write_json(
        custom / "screened_candidates.json",
        {
            "selected": [_selected_candidate("CAND-001")],
            "excluded": [],
            "screening_policy": {"total_candidates": 1},
        },
    )
    artifact_registry = {
        "artifacts": {
            "candidate_claims": {"path": "custom/candidate_claims.json"},
            "screened_candidates": {"path": "custom/screened_candidates.json"},
        }
    }
    artifact_paths = agent_artifact_paths_from_contracts(
        workspace,
        artifact_registry["artifacts"],
    )

    materiality = project_workspace_materiality_selection(
        workspace,
        artifact_registry=artifact_registry,
    )
    coverage = _coverage_omission_projection(
        workspace=workspace,
        markdown="## Executive Summary\n",
        ledger=ClaimLedger(),
        artifact_paths=artifact_paths,
    )

    assert materiality["status"] == "no_materiality_policy"
    assert materiality["screened_candidates_present"] is True
    assert coverage["status"] == "checked"
    assert coverage["screened_candidates_validation_result"] == (
        "valid_screened_candidates_schema"
    )


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
        (
            "normalization_operation",
            "intake_projection normalizations[0].operation must be a non-empty string",
        ),
        (
            "normalization_target",
            "intake_projection normalizations[0].target is required",
        ),
        (
            "finding_code",
            "intake_projection findings[0].code must be a non-empty string",
        ),
        (
            "finding_severity",
            "intake_projection findings[0].severity must be fatal",
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
    elif corruption == "normalized_sha256":
        projection["normalized_sha256"] = "not-a-digest"
    elif corruption == "normalization_operation":
        projection["normalizations"] = [
            {"operation": "", "path": "candidate[0]", "source": "a", "target": "b"}
        ]
        projection["normalization_count"] = 1
    elif corruption == "normalization_target":
        projection["normalizations"] = [
            {"operation": "field_alias", "path": "candidate[0]", "source": "a"}
        ]
        projection["normalization_count"] = 1
    else:
        projection["findings"] = [
            {
                "artifact_id": "candidate_claims",
                "severity": "warning" if corruption == "finding_severity" else "fatal",
                "code": "" if corruption == "finding_code" else "contract_invalid",
                "path": "candidate[0].candidate_id",
                "message": "candidate id missing",
                "validation_result": "candidate_claims_schema_error:candidate[0].candidate_id",
            }
        ]
        projection["fatal_finding_count"] = (
            0 if corruption == "finding_severity" else 1
        )

    reasons = validate_intake_projection(projection)

    assert expected_reason in reasons


@pytest.mark.parametrize(
    ("status", "normalized_sha256", "fatal_finding_count", "findings", "expected_reason"),
    [
        (
            "valid",
            "",
            0,
            [],
            "valid artifact record requires a normalized intake digest",
        ),
        (
            "valid",
            "a" * 64,
            1,
            [
                {
                    "artifact_id": "candidate_claims",
                    "severity": "fatal",
                    "code": "contract_invalid",
                    "path": "candidate[0].candidate_id",
                    "message": "candidate id missing",
                    "validation_result": (
                        "candidate_claims_schema_error:candidate[0].candidate_id"
                    ),
                }
            ],
            "valid artifact record cannot contain fatal intake findings",
        ),
        (
            "invalid",
            "a" * 64,
            0,
            [],
            "invalid artifact record requires a fatal intake finding",
        ),
    ],
)
def test_projection_rejects_impossible_status_digest_finding_combination(
    status: str,
    normalized_sha256: str,
    fatal_finding_count: int,
    findings: list[dict[str, object]],
    expected_reason: str,
) -> None:
    registry = {
        "run_id": "run-current",
        "artifacts": {
            "candidate_claims": {
                "status": status,
                "sha256": "b" * 64,
                "intake_projection": {
                    "schema_version": INTAKE_PROJECTION_SCHEMA_VERSION,
                    "artifact_id": "candidate_claims",
                    "transform_version": AGENT_ARTIFACT_INTAKE_TRANSFORM_VERSION,
                    "raw_sha256": "b" * 64,
                    "normalized_sha256": normalized_sha256,
                    "normalization_count": 0,
                    "fatal_finding_count": fatal_finding_count,
                    "normalizations": [],
                    "findings": findings,
                },
            }
        },
    }

    reasons = validate_registry_intake_context(
        registry,
        expected_run_id="run-current",
        artifact_id="candidate_claims",
    )

    assert expected_reason in reasons


@pytest.mark.parametrize(
    ("artifact_id", "record_status", "expected_reason"),
    [
        (
            "screened_candidates",
            "valid",
            "intake_projection artifact_id does not match artifact record",
        ),
        (
            "candidate_claims",
            "banana",
            "artifact record status is unsupported for intake projection",
        ),
    ],
    ids=[
        "INTAKE-REGISTRY-06-artifact-transplant",
        "INTAKE-REGISTRY-07-unknown-status",
    ],
)
def test_registry_intake_projection_binds_artifact_identity_and_status(
    tmp_path: Path,
    artifact_id: str,
    record_status: str,
    expected_reason: str,
) -> None:
    candidate_path = tmp_path / "candidate_claims.json"
    _write_json(candidate_path, [_candidate()])
    candidate = evaluate_agent_artifact_intake(
        candidate_path,
        artifact_id="candidate_claims",
    )
    registry = {
        "run_id": "run-current",
        "artifacts": {
            artifact_id: {
                "status": record_status,
                "sha256": candidate.raw_sha256,
                "intake_projection": candidate.projection(),
            }
        },
    }

    reasons = validate_registry_intake_context(
        registry,
        expected_run_id="run-current",
        artifact_id=artifact_id,
    )

    assert expected_reason in reasons
