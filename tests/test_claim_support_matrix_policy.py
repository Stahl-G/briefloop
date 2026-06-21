from __future__ import annotations

from multi_agent_brief.orchestrator.runtime_state.claim_support_matrix import (
    CLAIM_SUPPORT_MATRIX_POLICY_PROJECTION_SCHEMA_VERSION,
    project_claim_support_matrix_policy,
    project_claim_support_policy,
    validate_claim_support_matrix_against_artifacts,
)


def _row(
    row_id: str,
    *,
    atom_id: str = "AC-0001-01",
    claim_id: str = "CL-0001",
    evidence_span_id: str | None = "ESP-001-01",
    support_label: str = "direct_support",
    support_strength: str = "high",
    required_action: str = "none",
    repair_owner: str = "none",
    decision_source: str = "human",
) -> dict:
    return {
        "row_id": row_id,
        "claim_id": claim_id,
        "atom_id": atom_id,
        "evidence_span_id": evidence_span_id,
        "support_label": support_label,
        "support_strength": support_strength,
        "support_reason": "Recorded support relation for deterministic policy projection.",
        "required_action": required_action,
        "repair_owner": repair_owner,
        "decision_source": decision_source,
    }


def _ledger_claims() -> list[dict]:
    return [
        {
            "claim_id": "CL-0001",
            "statement": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
            "evidence_text": "Example evidence.",
            "claim_type": "fact",
        }
    ]


def _atomic_graph(*, include_second_high_atom: bool = False) -> dict:
    atoms = [
        {
            "atom_id": "AC-0001-01",
            "text": "ExampleCo opened a demo facility.",
            "claim_role": "observed_fact",
            "materiality": "high",
        }
    ]
    if include_second_high_atom:
        atoms.append(
            {
                "atom_id": "AC-0001-02",
                "text": "The facility is strategically important.",
                "claim_role": "trend_interpretation",
                "materiality": "high",
            }
        )
    return {
        "schema_version": "mabw.atomic_claim_graph.v1",
        "claims": [
            {
                "claim_id": "CL-0001",
                "atoms": atoms,
                "edges": [],
            }
        ],
    }


def _evidence_span_registry() -> dict:
    return {
        "schema_version": "mabw.evidence_span_registry.v1",
        "sources": [
            {
                "source_id": "SRC-001",
                "source_type": "company_release",
                "source_tier": "company_official",
                "url": "https://example.com/release",
                "published_at": "2026-06-10",
                "spans": [
                    {
                        "span_id": "ESP-001-01",
                        "raw_excerpt": "ExampleCo opened a demo facility.",
                        "hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                        "span_role": "direct_statement",
                    }
                ],
            }
        ],
    }


def _validation_reason(
    *,
    rows: list[dict],
    ledger_claims: list[dict] | None = None,
    graph_payload: dict | None = None,
    evidence_span_registry_payload: dict | None = None,
) -> str | None:
    return validate_claim_support_matrix_against_artifacts(
        matrix_payload={"schema_version": "mabw.claim_support_matrix.v1", "rows": rows},
        ledger_claims=ledger_claims if ledger_claims is not None else _ledger_claims(),
        graph_payload=graph_payload if graph_payload is not None else _atomic_graph(),
        evidence_span_registry_payload=(
            evidence_span_registry_payload
            if evidence_span_registry_payload is not None
            else _evidence_span_registry()
        ),
    )


def test_claim_support_matrix_cross_artifact_valid_complete_matrix_passes():
    assert _validation_reason(rows=[_row("CSM-0001")]) is None


def test_claim_support_matrix_cross_artifact_rejects_unknown_claim():
    reason = _validation_reason(
        rows=[_row("CSM-0001", claim_id="CL-9999", atom_id="AC-9999-01")],
        graph_payload={
            "schema_version": "mabw.atomic_claim_graph.v1",
            "claims": [
                {
                    "claim_id": "CL-9999",
                    "atoms": [
                        {
                            "atom_id": "AC-9999-01",
                            "text": "Unknown claim atom.",
                            "claim_role": "observed_fact",
                            "materiality": "high",
                        }
                    ],
                }
            ],
        },
    )

    assert reason == "unknown_claim_reference:CL-9999"


def test_claim_support_matrix_cross_artifact_rejects_unknown_atom():
    reason = _validation_reason(rows=[_row("CSM-0001", atom_id="AC-0001-99")])

    assert reason == "unknown_atom_reference:AC-0001-99"


def test_claim_support_matrix_cross_artifact_rejects_atom_claim_mismatch():
    reason = _validation_reason(
        rows=[_row("CSM-0001", claim_id="CL-0002")],
        ledger_claims=[
            *_ledger_claims(),
            {
                "claim_id": "CL-0002",
                "statement": "Second claim.",
                "source_id": "SRC-001",
                "evidence_text": "Example evidence.",
                "claim_type": "fact",
            },
        ],
    )

    assert reason == "atom_claim_mismatch:AC-0001-01:CL-0002:CL-0001"


def test_claim_support_matrix_cross_artifact_rejects_unknown_span():
    reason = _validation_reason(rows=[_row("CSM-0001", evidence_span_id="ESP-001-99")])

    assert reason == "unknown_evidence_span_reference:ESP-001-99"


def test_claim_support_matrix_cross_artifact_requires_high_materiality_atom_rows():
    reason = _validation_reason(
        rows=[_row("CSM-0001")],
        graph_payload=_atomic_graph(include_second_high_atom=True),
    )

    assert reason == "high_materiality_atom_missing_row:AC-0001-02"


def test_claim_support_matrix_cross_artifact_requires_span_for_support_labels():
    reason = _validation_reason(rows=[_row("CSM-0001", evidence_span_id=None, support_label="direct_support")])

    assert reason == "support_label_requires_span:CSM-0001"


def test_claim_support_matrix_cross_artifact_allows_null_span_negative_rows():
    assert (
        _validation_reason(
            rows=[
                _row(
                    "CSM-0001",
                    evidence_span_id=None,
                    support_label="insufficient_evidence",
                    support_strength="none",
                )
            ]
        )
        is None
    )


def test_claim_support_policy_empty_rows_is_not_available():
    projection = project_claim_support_policy(rows=[], atom_materiality={})

    assert projection["schema_version"] == CLAIM_SUPPORT_MATRIX_POLICY_PROJECTION_SCHEMA_VERSION
    assert projection["status"] == "not_available"
    assert projection["row_count"] == 0
    assert projection["atom_count"] == 0
    assert projection["summary_counts"]["blocking_atom_count"] == 0


def test_high_materiality_unsupported_row_projects_blocking_atom():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                evidence_span_id=None,
                support_label="unsupported",
                support_strength="none",
                required_action="add_evidence_span",
                repair_owner="analyst",
            )
        ],
        atom_materiality={"AC-0001-01": "high"},
    )

    atom = projection["atoms"][0]
    assert projection["status"] == "projected"
    assert atom["blocking"] is True
    assert atom["verdict"] == "blocking"
    assert atom["blocking_rows"][0]["row_id"] == "CSM-0001"
    assert projection["summary_counts"]["blocking_row_count"] == 1


def test_low_materiality_unsupported_row_does_not_block_without_policy_action():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                evidence_span_id=None,
                support_label="unsupported",
                support_strength="none",
                required_action="none",
            )
        ],
        atom_materiality={"AC-0001-01": "low"},
    )

    atom = projection["atoms"][0]
    assert atom["blocking"] is False
    assert atom["verdict"] == "recorded"
    assert projection["summary_counts"]["blocking_atom_count"] == 0


def test_block_release_action_projects_blocking_regardless_of_materiality():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                support_label="partial_support",
                support_strength="medium",
                required_action="block_release",
                repair_owner="human_review",
            )
        ],
        atom_materiality={"AC-0001-01": "low"},
    )

    atom = projection["atoms"][0]
    assert atom["blocking"] is True
    assert atom["verdict"] == "blocking"
    assert atom["blocking_rows"][0]["required_action"] == "block_release"


def test_weak_support_projects_weak_and_downgrade_signals():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                support_label="weak_support",
                support_strength="low",
                required_action="downgrade_wording",
                repair_owner="editor",
            )
        ],
        atom_materiality={"AC-0001-01": "medium"},
    )

    atom = projection["atoms"][0]
    assert atom["weak_support"] is True
    assert atom["downgrade_required"] is True
    assert atom["verdict"] == "downgrade_required"
    assert atom["weak_rows"][0]["row_id"] == "CSM-0001"
    assert atom["downgrade_required_rows"][0]["repair_owner"] == "editor"


def test_human_adjudication_action_projects_adjudication_signal():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                support_label="partial_support",
                support_strength="medium",
                required_action="human_adjudication",
                repair_owner="human_review",
            )
        ],
        atom_materiality={"AC-0001-01": "medium"},
    )

    atom = projection["atoms"][0]
    assert atom["adjudication_required"] is True
    assert atom["verdict"] == "adjudication_required"
    assert atom["adjudication_required_rows"][0]["decision_source"] == "human"


def test_inferential_support_projects_framing_signal():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0001",
                support_label="inferential_support",
                support_strength="medium",
                required_action="mark_as_inference",
                repair_owner="analyst",
            )
        ],
        atom_materiality={"AC-0001-01": "high"},
    )

    atom = projection["atoms"][0]
    assert atom["inference_framing_required"] is True
    assert atom["verdict"] == "inference_framing_required"
    assert atom["inference_framing_required_rows"][0]["required_action"] == "mark_as_inference"


def test_multiple_rows_for_same_atom_aggregate_stably():
    projection = project_claim_support_policy(
        rows=[
            _row(
                "CSM-0002",
                support_label="weak_support",
                support_strength="low",
                required_action="downgrade_wording",
                repair_owner="editor",
                decision_source="llm_assisted_human",
            ),
            _row(
                "CSM-0001",
                support_label="direct_support",
                support_strength="high",
                required_action="none",
                repair_owner="none",
                decision_source="human",
            ),
        ],
        atom_materiality={"AC-0001-01": "medium"},
    )

    atom = projection["atoms"][0]
    assert atom["row_ids"] == ["CSM-0001", "CSM-0002"]
    assert atom["support_labels"] == ["direct_support", "weak_support"]
    assert atom["support_strengths"] == ["high", "low"]
    assert atom["required_actions"] == ["downgrade_wording", "none"]
    assert atom["repair_owners"] == ["editor", "none"]
    assert atom["decision_sources"] == ["human", "llm_assisted_human"]
    assert atom["downgrade_required_rows"][0]["row_id"] == "CSM-0002"


def test_project_claim_support_matrix_policy_uses_payload_rows_and_materiality():
    projection = project_claim_support_matrix_policy(
        {
            "schema_version": "mabw.claim_support_matrix.v1",
            "rows": [
                _row(
                    "CSM-0001",
                    atom_id="AC-0002-01",
                    claim_id="CL-0002",
                    evidence_span_id=None,
                    support_label="insufficient_evidence",
                    support_strength="none",
                    required_action="add_evidence_span",
                )
            ],
        },
        atom_materiality={"AC-0002-01": "high"},
    )

    atom = projection["atoms"][0]
    assert atom["atom_id"] == "AC-0002-01"
    assert atom["claim_id"] == "CL-0002"
    assert atom["materiality"] == "high"
    assert atom["blocking"] is True
