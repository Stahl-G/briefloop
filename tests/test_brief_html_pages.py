"""Page-data contract tests for the read-only three-page brief HTML."""

from __future__ import annotations

from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest

from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256
from multi_agent_brief.product.brief_html import build_brief_pages_data
from multi_agent_brief.product.brief_html.builder import (
    BRIEF_PAGES_DATA_SCHEMA,
    IMPROVEMENT_CONSUMPTION_NOTE,
    IMPROVEMENT_PLANNED_NOTE,
    LAJ_EXPERIMENTAL_BANNER,
)
from multi_agent_brief.product.post_final_assessment_projection import (
    PostFinalAssessmentProjection,
    ReaderReviewRequirementLabel,
    ReaderReviewRequestTemplate,
    _archive_assessment_projection,
)
from multi_agent_brief.runtime_host_v2.projections import (
    build_local_run_presentation,
    build_store_quality_projection,
)
from multi_agent_brief.runtime_host_v2.contracts import LocalReaderBrief
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LAJ_READER_SCHEMA_ID,
    LajReaderView,
    build_empty_laj_reader_view,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
)
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.helpers import initialize_workspace


def _finding(report_sha256: str) -> dict[str, object]:
    return {
        "assessment_unit_id": "AU-0123456789ab",
        "scope_class": "O1",
        "dimension_id": "uncertainty_calibration",
        "severity": "major",
        "impact_scope": "decision",
        "report_spans": [
            {
                "report_sha256": report_sha256,
                "block_id": "B000001",
                "start_char": 0,
                "end_char": 12,
                "excerpt_sha256": "a" * 64,
            }
        ],
        "context_requirement_ids": [],
        "observation": "Observed uncertainty wording.",
        "rationale": "The wording overstates certainty.",
        "severity_basis": "Major because it changes the decision frame.",
        "confidence_basis": "direct_single_span",
        "external_premise_disclosure": "none",
        "recommended_human_action": "recalibrate_uncertainty",
        "suggested_rewrite": None,
        "finding_id": "F-0123456789ab",
        "status": "proposal",
    }


def _laj_view_payload(report_sha256: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": LAJ_READER_SCHEMA_ID,
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": {
            "artifact_id": "artifact-laj-1",
            "report_sha256": report_sha256,
            "trial_id": "trial-1",
            "shadow_receipt_id": "receipt-shadow-1",
            "instrument_sha256": "b" * 64,
            "execution_sha256": "c" * 64,
            "execution_origin": "synthetic",
            "model_id": "model-1",
            "model_version": "model-version-1",
            "archive_manifest_sha256": "d" * 64,
            "presentation_sha256": "e" * 64,
        },
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": ["assessment_completed"],
        "assessed_unit_count": 3,
        "finding_count": 1,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [_finding(report_sha256)],
        "requirement_assessments": [],
        "disclaimer": "Experimental advisory assessment.",
    }
    payload["view_sha256"] = canonical_sha256(payload)
    return payload


def _write_laj_view(workspace: Path, report_sha256: str) -> Path:
    import json

    target_dir = workspace / "laj-advisory-demo"
    target_dir.mkdir(parents=True)
    target = target_dir / "laj.json"
    target.write_text(
        json.dumps(_laj_view_payload(report_sha256), ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def _bind_final_reader(monkeypatch, workspace: Path, markdown: bytes) -> None:
    local = build_local_run_presentation(workspace)
    reader = LocalReaderBrief.model_validate(
        {
            "state": "available",
            "artifact_id": "reader_brief",
            "revision": 1,
            "sha256": hashlib.sha256(markdown).hexdigest(),
            "markdown_utf8": markdown,
        },
        strict=True,
    )
    local = local.model_copy(
        update={
            "view_state": "finalized",
            "terminal_state": "finalized_local",
            "reason_code": "local_finalization_complete",
            "reader_brief": reader,
        }
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder.build_local_run_presentation",
        lambda _workspace: local,
    )


def test_quality_page_matches_store_projection_verbatim(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    data = build_brief_pages_data(workspace)

    assert data["schema_version"] == BRIEF_PAGES_DATA_SCHEMA
    assert data["workspace"]["authority"] == "sqlite_control_store"
    quality = data["quality"]
    assert quality["status"] == "unavailable"
    assert quality["reason_code"] == "final_reader_not_available"
    assert quality["projection"] == build_store_quality_projection(workspace)

    groups = quality["groups"]
    assert set(groups) == {
        "control",
        "source",
        "gates",
        "claims",
        "reader_clean",
        "closeout",
    }
    control = {row["label"]: row["value"] for row in groups["control"]}
    assert control["run_id"] == data["workspace"]["run_id"]
    assert control["store_revision"] == data["workspace"]["store_revision"]
    assert control["view_state"] == "setup"
    assert len(groups["gates"]) >= 1
    assert {row["label"] for row in groups["claims"]} == {"claims"}
    assert quality["actions"]


def test_semantic_page_is_honest_not_run_without_laj(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    _write_laj_view(workspace, "1" * 64)
    semantic = build_brief_pages_data(workspace)["semantic"]

    assert semantic["status"] == "not_run"
    assert semantic["banner"] == LAJ_EXPERIMENTAL_BANNER
    assert semantic["findings"] == []
    assert len(semantic["dimensions"]) == 9
    assert all(row["state"] == "not_assessed_in_view" for row in semantic["dimensions"])
    assert "never trigger Gates" in semantic["handoff_note"]


def test_supported_reader_review_without_policy_is_not_assessed_with_run_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    template = ReaderReviewRequestTemplate(
        schema_version="briefloop.reader_review_assessment_input.v1",
        assessment_kind="reader_review",
        report_type="management_monthly",
        language="en",
        profile_id="management_brief_en_v1",
        protocol="anthropic_messages_compatible",
        endpoint_class="explicit_messages_api",
        egress_scope="public_safe_report",
        report_scope="final_reader_markdown",
        context_scope="frozen_run_direction_requirements",
        disclosure_confirmed=True,
        public_safe_egress_attested=True,
        cost_status="not_measured",
        provider_call_ceiling=2,
        total_input_token_ceiling=400000,
        total_output_token_ceiling=8192,
        output_tokens_per_call=4096,
        automatic_retry=False,
        advisory_only=True,
        authority_effect="none",
    )
    projection = PostFinalAssessmentProjection(
        lifecycle_present=False,
        status="not_requested",
        reason_code="laj_not_run",
        view=build_empty_laj_reader_view(
            status="not_available", reason_code="laj_not_run"
        ),
        user_status="not_assessed",
        compatible_result_options=(),
        requirement_labels=(),
        selected_result_id=None,
        selected_result_fingerprint=None,
        review_status=None,
        request_template=template,
        next_run_consumption="explicit_opt_in_successor_only",
        run_action_available=True,
        selection_required=False,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder."
        "build_post_final_assessment_projection",
        lambda *_args, **_kwargs: projection,
    )

    semantic = build_brief_pages_data(workspace)["semantic"]

    assert semantic["status"] == "not_assessed"
    assert semantic["run_action_available"] is True
    assert semantic["request_template"]["protocol"] == ("anthropic_messages_compatible")


def test_o2_requirement_assessment_uses_binding_verified_human_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    report_sha256 = "1" * 64
    payload = _laj_view_payload(report_sha256)
    payload["finding_count"] = 0
    payload["findings"] = []
    payload["requirement_assessments"] = [
        {
            "assessment_unit_id": "AU-0123456789ab",
            "requirement_id": "requirement-audience-1",
            "state": "unfulfilled_transparent",
            "attention_status": "attention_needed",
            "report_spans": [
                {
                    "report_sha256": report_sha256,
                    "block_id": "B000001",
                    "start_char": 0,
                    "end_char": 12,
                    "excerpt_sha256": "a" * 64,
                }
            ],
            "rationale": "The brief discloses that the requirement is unmet.",
        }
    ]
    payload.pop("view_sha256")
    payload["view_sha256"] = canonical_sha256(payload)
    view = LajReaderView.model_validate(payload, strict=True)
    projection = PostFinalAssessmentProjection(
        lifecycle_present=True,
        status="available",
        reason_code=None,
        view=view,
        user_status="no_finding_returned_in_completed_supported_checks",
        compatible_result_options=(),
        requirement_labels=(
            ReaderReviewRequirementLabel(
                requirement_id="requirement-audience-1",
                requirement_type="audience_need",
                text="State the unresolved decision dependency for management.",
                source_locator="run_direction.audience",
            ),
        ),
        selected_result_id="assessment-result-reader-review-1",
        selected_result_fingerprint="b" * 64,
        review_status=None,
        request_template=None,
        next_run_consumption="explicit_opt_in_successor_only",
        run_action_available=False,
        selection_required=False,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder."
        "build_post_final_assessment_projection",
        lambda *_args, **_kwargs: projection,
    )

    semantic = build_brief_pages_data(workspace)["semantic"]
    assessment = semantic["requirement_assessments"][0]

    assert assessment["state"] == "unfulfilled_transparent"
    assert assessment["attention_status"] == "attention_needed"
    assert assessment["requirement_type"] == "audience_need"
    assert assessment["requirement_text"] == (
        "State the unresolved decision dependency for management."
    )
    assert "not a quality pass" in semantic["disclaimer"]
    assert "does not verify facts" in semantic["disclaimer"]


def test_semantic_page_renders_bound_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    markdown = b"# demo brief\n"
    view = _write_laj_view(workspace, hashlib.sha256(markdown).hexdigest())
    _bind_final_reader(monkeypatch, workspace, markdown)

    semantic = build_brief_pages_data(workspace, laj_view_path=view)["semantic"]
    assert semantic["status"] == "available"
    assert semantic["coverage"]["finding_count"] == 1
    finding = semantic["findings"][0]
    assert finding["finding_id"] == "F-0123456789ab"
    assert finding["severity"] == "major"
    assert finding["dimension_id"] == "uncertainty_calibration"
    assert finding["report_spans"][0]["block_id"] == "B000001"
    states = {row["dimension_id"]: row["state"] for row in semantic["dimensions"]}
    assert states["uncertainty_calibration"] == "finding_reported"
    assert len(states) == 9


def test_semantic_page_marks_stale_when_report_binding_drifts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    view = _write_laj_view(workspace, "1" * 64)
    _bind_final_reader(monkeypatch, workspace, b"# different brief\n")

    semantic = build_brief_pages_data(workspace, laj_view_path=view)["semantic"]
    assert semantic["status"] == "stale"
    assert semantic["findings"] == []


def test_semantic_page_honors_explicit_laj_view_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    markdown = b"# final\n"
    digest = hashlib.sha256(markdown).hexdigest()
    view_path = _write_laj_view(workspace, digest)
    _bind_final_reader(monkeypatch, workspace, markdown)
    semantic = build_brief_pages_data(workspace, laj_view_path=view_path)["semantic"]
    assert semantic["status"] == "available"
    assert semantic["coverage"]["finding_count"] == 1


def test_semantic_page_prefers_store_qualified_assessment_over_manual_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S13/S18/S23: canonical HTML consumes only the bound Store/archive view."""

    from tests.test_finalized_local_review_facts import _finalized_local_workspace
    from tests.test_post_final_assessment import _fixture_service, _policy_payload

    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls)
    assert service.policy_set(_policy_payload())["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    assessed = service.assess()
    assert assessed["ok"] is True and assessed["status"] == "available"
    assert len(calls) == 9

    report_sha256 = build_local_run_presentation(workspace).reader_brief.sha256
    assert report_sha256 is not None
    manual = _write_laj_view(workspace, report_sha256)
    semantic = build_brief_pages_data(workspace, laj_view_path=manual)["semantic"]

    assert semantic["store_qualified"] is True
    assert semantic["status"] == "not_assessed"
    assert semantic["coverage"]["finding_count"] == 0
    assert semantic["findings"] == []
    assert semantic["reason_codes"] != ["assessment_completed"]


def _archive_inventory_fixture(*, incomplete: bool) -> tuple[object, object, object]:
    """Build safe metadata-only archive objects for projection regression tests."""

    units: list[SimpleNamespace] = []
    for ordinal in range(5):
        units.append(
            SimpleNamespace(
                assessment_unit_id=f"unit-o1-{ordinal}",
                scope_class="O1",
                dimension_id="cross_section_consistency",
                sub_aspect_id=f"o1-{ordinal}",
            )
        )
    for ordinal in range(7):
        units.append(
            SimpleNamespace(
                assessment_unit_id=f"unit-o2-{ordinal}",
                scope_class="O2",
                dimension_id="brief_requirement_coverage",
                sub_aspect_id=f"o2-{ordinal}",
            )
        )
    plan = SimpleNamespace(
        units=units,
        assessment_plan_sha256="a" * 64,
    )
    outcomes = [
        SimpleNamespace(
            assessment_unit_id=unit.assessment_unit_id,
            disposition="no_finding",
        )
        for unit in units[:5]
    ]
    if not incomplete:
        outcomes.extend(
            SimpleNamespace(
                assessment_unit_id=unit.assessment_unit_id,
                disposition="no_finding",
            )
            for unit in units[5:]
        )
    attempts = [
        SimpleNamespace(
            dimension_id="cross_section_consistency",
            attempt_ref="attempt-o1",
            status="completed",
            reason_code=None,
            prompt_request_sha256="1" * 64,
        ),
        SimpleNamespace(
            dimension_id="brief_requirement_coverage",
            attempt_ref="attempt-o2",
            status="failed" if incomplete else "completed",
            reason_code="provider_failed" if incomplete else None,
            prompt_request_sha256="2" * 64,
        ),
    ]
    evidence = [
        SimpleNamespace(
            dimension_id=item.dimension_id,
            status=item.status,
            reason_code=item.reason_code,
            prompt_request_sha256=item.prompt_request_sha256,
            raw_response_bytes_hex=("not-read" if item.status == "completed" else None),
        )
        for item in attempts
    ]
    witness = SimpleNamespace(
        assessment_plan=plan,
        run=SimpleNamespace(
            run_status="incomplete" if incomplete else "completed",
            assessment_units=outcomes,
            attempt_refs=attempts,
        ),
        dimension_attempt_evidence=evidence,
        instrument_manifest=SimpleNamespace(
            system_prompt_sha256="3" * 64,
            dimension_prompt_sha256="4" * 64,
            instrument_sha256="5" * 64,
        ),
        input_binding=SimpleNamespace(input_binding_sha256="6" * 64),
    )
    archive = SimpleNamespace(
        witness=witness,
        reason_codes=("provider_incomplete",)
        if incomplete
        else ("assessment_completed",),
        request=SimpleNamespace(ordered_prompt_request_sha256s=("1" * 64, "2" * 64)),
    )
    request = SimpleNamespace(
        schema_version="briefloop.post_final_assessment_request_record.v4",
        human_actor_id="human-1",
        human_request_id="request-1",
        assessment_generation=1,
        assessment_request_id="assessment-request-1",
        request_fingerprint="7" * 64,
        policy_revision_id="policy-1",
        requested_model_id="model-1",
        model_version="model-version-1",
        expected_model_identity="model-identity-1",
        profile_id="management_brief_en_v1",
        claimed_at="2026-08-08T00:00:00Z",
    )
    policy = SimpleNamespace(
        schema_version="briefloop.post_final_assessment_policy_revision.v3",
        auto_run=False,
        auto_open=False,
        policy_revision_id="policy-1",
        policy_fingerprint="8" * 64,
    )
    return archive, request, policy


@pytest.mark.parametrize("incomplete", [True, False])
def test_archive_projection_uses_profile_plan_and_never_failed_raw_output(
    incomplete: bool,
) -> None:
    archive, request, policy = _archive_inventory_fixture(incomplete=incomplete)
    if incomplete:
        # A retained body must not become an outcome when its terminal attempt
        # failed; emulate the malformed/raw-response temptation explicitly.
        archive.witness.run.assessment_units.append(
            SimpleNamespace(
                assessment_unit_id="unit-o2-0",
                disposition="no_finding",
            )
        )
    scopes, units, evidence = _archive_assessment_projection(
        archive=archive,
        request=request,
        policy=policy,
    )

    assert [item.scope_class for item in scopes] == ["O1", "O2"]
    assert len(units) == 12
    assert [item.state for item in units[:5]] == ["completed_no_finding"] * 5
    if incomplete:
        assert scopes[0].state == "completed_no_finding"
        assert scopes[0].note_code == "completed_no_finding_not_pass"
        assert scopes[1].state == "unable_to_assess"
        assert scopes[1].note_code == "provider_attempt_incomplete"
        assert scopes[1].reason_code == "provider_incomplete"
        assert [item.state for item in units[5:]] == ["unable_to_assess"] * 7
        assert {item.reason_code for item in units[5:]} == {"provider_incomplete"}
    else:
        assert [item.state for item in units[5:]] == ["completed_no_finding"] * 7
        assert [item.state for item in scopes] == [
            "completed_no_finding",
            "completed_no_finding",
        ]
    assert evidence is not None
    assert evidence.trigger == "explicit_human_authorization"
    assert evidence.surface == "not_recorded"
    assert evidence.provider_call_count == 2
    assert len(evidence.calls) == 2
    assert all(not hasattr(item, "raw_response_bytes_hex") for item in evidence.calls)


def test_store_semantic_projection_exposes_scopes_without_legacy_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    archive, request, policy = _archive_inventory_fixture(incomplete=True)
    scopes, units, evidence = _archive_assessment_projection(
        archive=archive,
        request=request,
        policy=policy,
    )
    projection = PostFinalAssessmentProjection(
        lifecycle_present=True,
        status="incomplete",
        reason_code="provider_incomplete",
        view=build_empty_laj_reader_view(
            status="not_available", reason_code="provider_incomplete"
        ),
        user_status="partially_assessed",
        compatible_result_options=(),
        requirement_labels=(),
        selected_result_id="result-1",
        selected_result_fingerprint="9" * 64,
        review_status=None,
        request_template=None,
        next_run_consumption="explicit_opt_in_successor_only",
        run_action_available=False,
        selection_required=False,
        assessment_scopes=scopes,
        assessment_units=units,
        run_evidence=evidence,
    )
    monkeypatch.setattr(
        "multi_agent_brief.product.brief_html.builder."
        "build_post_final_assessment_projection",
        lambda *_args, **_kwargs: projection,
    )

    semantic = build_brief_pages_data(workspace)["semantic"]
    assert semantic["dimensions"] == []
    assert len(semantic["assessment_scopes"]) == 2
    assert len(semantic["assessment_units"]) == 12
    assert semantic["assessment_scopes"][1]["state"] == "unable_to_assess"
    assert semantic["run_evidence"]["surface"] == "not_recorded"
    assert len(semantic["run_evidence"]["calls"]) == 2


def test_improvement_page_is_honest_unavailable(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "ws")
    improvement = build_brief_pages_data(workspace)["improvement"]

    assert improvement["status"] == "unavailable"
    assert improvement["reason_code"] == "post_final_review_not_available"
    assert improvement["recorded"] == []
    assert improvement["consumption_note"] == IMPROVEMENT_CONSUMPTION_NOTE
    assert improvement["planned_note"] == IMPROVEMENT_PLANNED_NOTE
    assert improvement["next_run_consumption"] == "explicit_opt_in_successor_only"
