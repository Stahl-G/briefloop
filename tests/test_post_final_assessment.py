"""Focused Store-qualified PF-LAJ-1 lifecycle rows."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from multi_agent_brief.control_store import SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.post_final_assessment import (
    POST_FINAL_ASSESSMENT_POLICY_SCHEMA,
    POST_FINAL_ASSESSMENT_RUN_SCHEMA,
    PostFinalAssessmentError,
    PostFinalAssessmentService,
    finalized_lineage_fingerprint,
    post_final_assessment_archive_root,
    reassessed_facts_fingerprint,
    resolve_current_post_final_assessment_request,
)
from multi_agent_brief.product.post_final_assessment_projection import (
    build_post_final_assessment_projection,
)
from multi_agent_brief.product.brief_html import render_brief_pages_html
from multi_agent_brief.product.brief_html.builder import build_brief_pages_data
from multi_agent_brief.runtime_host_v2 import (
    build_finalized_local_review_projection,
)
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_API_KEY_SETTING,
    AnthropicMessagesAdapterV1,
    synthetic_anthropic_message_bytes_v1,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    _rubric_from_prompt,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    DIMENSION_RESPONSE_SCHEMA_ID,
)
from multi_agent_brief.semantic_evaluator.archive import trial_archive_path
from multi_agent_brief.semantic_evaluator.serialization import canonical_json_bytes
import multi_agent_brief.product.post_final_assessment as post_final_assessment_module
import multi_agent_brief.semantic_evaluator.runner as runner_module
from tests.test_core_run_v2_packaging import _real_finalized_local_workspace
from tests.test_finalized_local_review_facts import _finalized_local_workspace
from tests.helpers import initialize_workspace


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
_ASSESSMENT_RESULT_READOUT_FIELDS = (
    "assessed_unit_count",
    "finding_count",
    "withheld_finding_count",
    "abstention_count",
    "reason_codes",
)


def _assessment_result_readout(result) -> dict[str, object]:
    return {
        field: getattr(result, field) for field in _ASSESSMENT_RESULT_READOUT_FIELDS
    }


def _listed_assessment_result_readout(item: dict[str, object]) -> dict[str, object]:
    return {field: item[field] for field in _ASSESSMENT_RESULT_READOUT_FIELDS}


class _MessagesFixtureAdapter:
    """Public-safe in-process Messages fixture; no SDK or network use."""

    def __init__(
        self,
        execution,
        calls: list[tuple[str, int]],
        *,
        endpoint: str,
        terminal_mode: str = "accepted",
    ) -> None:
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.base_url = endpoint
        self._delegate = object.__new__(AnthropicMessagesAdapterV1)
        self._calls = calls
        self._terminal_mode = terminal_mode

    def invoke(self, request):
        self._calls.append((request.dimension_id, request.attempt_ordinal))
        if self._terminal_mode == "transport_failure":
            return self._delegate._transport_attempt(
                request=request,
                kind="adapter_error",
            )
        rubric = _rubric_from_prompt(request.user_text)
        unit_results = [
            {
                "assessment_unit_id": item["assessment_unit_id"],
                "disposition": "no_finding",
            }
            for item in rubric["assessment_units"]
        ]
        if (
            self._terminal_mode == "finding"
            and rubric["dimension"]["scope_class"] == "O1"
        ):
            start_marker = "<REPORT_DATA>\n"
            end_marker = "\n</REPORT_DATA>"
            start = request.user_text.index(start_marker) + len(start_marker)
            end = request.user_text.index(end_marker, start)
            report_data = json.loads(request.user_text[start:end])
            unit = rubric["assessment_units"][0]
            span = report_data["span_locator_contract"]["full_block_candidates"][0]
            unit_results[0] = {
                "assessment_unit_id": unit["assessment_unit_id"],
                "disposition": "finding_emitted",
                "findings": [
                    {
                        "assessment_unit_id": unit["assessment_unit_id"],
                        "scope_class": unit["scope_class"],
                        "dimension_id": unit["dimension_id"],
                        "severity": "major",
                        "impact_scope": "key_conclusion",
                        "report_spans": [span],
                        "context_requirement_ids": [],
                        "observation": "结论与正文约束不一致。",
                        "rationale": "同一报告内的两种表述不能同时成立。",
                        "severity_basis": "可能误导读者采取不受支持的行动。",
                        "confidence_basis": "direct_cross_span_conflict",
                        "external_premise_disclosure": "none",
                        "recommended_human_action": "reconcile_status_language",
                        "suggested_rewrite": None,
                    }
                ],
            }
        output = canonical_json_bytes(
            {
                "dimension_id": request.dimension_id,
                "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
                "trial_id": request.trial_id,
                "unit_results": unit_results,
            }
        )
        raw = synthetic_anthropic_message_bytes_v1(
            stop_reason={
                "accepted": "end_turn",
                "finding": "end_turn",
                "refusal": "refusal",
                "truncation": "max_tokens",
            }[self._terminal_mode],
            response_id=f"msg-pf-laj-{len(self._calls)}",
            model=request.expected_model_version,
            content=[
                {
                    "type": "thinking",
                    "thinking": "public synthetic protocol evidence",
                    "signature": "public-synthetic-signature",
                },
                {"type": "text", "text": output.decode("utf-8")},
            ],
        )
        return self._delegate._attempt_from_response(
            request=request,
            raw=raw,
            sdk_response=None,
        )


def _fixture_service(
    workspace: Path,
    calls: list[tuple[str, int]],
    *,
    terminal_mode: str = "accepted",
):
    endpoint = _policy_payload()["messages_endpoint"]
    assert type(endpoint) is str
    return PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda execution: _MessagesFixtureAdapter(
            execution,
            calls,
            endpoint=endpoint,
            terminal_mode=terminal_mode,
        ),
    )


def _current_action(history, run_id: str, revision: int):
    snapshot = history.snapshot_at_revision(run_id, revision)
    verified = CoreRunDomainVerifier()._verify_snapshot(history, snapshot)
    return classify_core_run_next_action(verified)


def _policy_payload() -> dict[str, object]:
    instrument = json.loads((FIXTURES / "instrument.json").read_text(encoding="utf-8"))
    instrument.update(
        {
            "instrument_config_id": "pf-laj-anthropic-instrument-v1",
            "provider_id": "anthropic_messages",
            "model_id": "public-compatible-model-v1",
            "model_version": "public-compatible-model-v1",
            "decoding": {
                "max_output_tokens": 4096,
                "seed": None,
                "temperature": 1.0,
                "top_p": 1.0,
            },
            "prompt_sizer": {
                "max_context_tokens": 200000,
                "reserved_output_tokens": 4096,
                "sizer_id": "anthropic_utf8_bytes_conservative_v1",
                "sizer_version": "anthropic_utf8_bytes_conservative_v1",
            },
        }
    )
    return {
        "schema_version": POST_FINAL_ASSESSMENT_POLICY_SCHEMA,
        "human_actor_id": "human-1",
        "human_request_id": "pf-laj-policy-request-1",
        "enabled": True,
        "auto_run": False,
        "auto_open": False,
        "messages_endpoint": "https://messages.example.test/v1",
        "requested_model_id": "public-compatible-model-v1",
        "model_version": "public-compatible-model-v1",
        "expected_model_identity": "public-compatible-model-v1",
        "instrument_config": instrument,
        "max_provider_calls": 9,
        "max_total_input_tokens": 700000,
        "max_total_output_tokens": 36864,
        "max_output_tokens_per_call": 4096,
        "public_safe_egress_attested": True,
    }


def _generation_one_run_payload(
    service: PostFinalAssessmentService,
    *,
    human_request_id: str = "pf-laj-assessment-run-1",
) -> dict[str, object]:
    facts, snapshot, _binding, _workspace_id, _history, action = service._load()
    policy = service._policy_for_facts(snapshot, facts)
    if policy is None:
        raise AssertionError("assessment policy is missing")
    return {
        "schema_version": POST_FINAL_ASSESSMENT_RUN_SCHEMA,
        "human_actor_id": "human-1",
        "human_request_id": human_request_id,
        "expected_store_revision": facts.store_revision,
        "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
            facts,
            action,
        ),
        "assessment_generation": 1,
        "assessment_purpose": "post_final_review",
        "predecessor_assessment_request_id": None,
        "predecessor_assessment_request_fingerprint": None,
        "predecessor_assessment_result_id": None,
        "predecessor_result_fingerprint": None,
        "predecessor_abandonment_id": None,
        "predecessor_abandonment_fingerprint": None,
        "abandon_predecessor": False,
        "policy_revision_id": policy.policy_revision_id,
        "policy_fingerprint": policy.policy_fingerprint,
        "public_safe_egress_attested": True,
        "max_provider_calls": policy.max_provider_calls,
        "max_total_input_tokens": policy.max_total_input_tokens,
        "max_total_output_tokens": policy.max_total_output_tokens,
        "max_output_tokens_per_call": policy.max_output_tokens_per_call,
    }


def _abandoning_next_generation_run_payload(
    service: PostFinalAssessmentService,
    *,
    human_request_id: str,
) -> dict[str, object]:
    facts, snapshot, _binding, _workspace_id, history, action = service._load()
    series = post_final_assessment_module.resolve_post_final_assessment_series(
        history,
        snapshot,
        facts,
        action,
    )
    if not series:
        raise AssertionError("assessment predecessor is missing")
    predecessor = series[-1]
    policy = service._policy_for_facts(snapshot, facts)
    if policy is None:
        raise AssertionError("assessment policy is missing")
    return {
        "schema_version": POST_FINAL_ASSESSMENT_RUN_SCHEMA,
        "human_actor_id": "human-1",
        "human_request_id": human_request_id,
        "expected_store_revision": facts.store_revision,
        "finalized_lineage_fingerprint": finalized_lineage_fingerprint(
            facts,
            action,
        ),
        "assessment_generation": predecessor.assessment_generation + 1,
        "assessment_purpose": "post_final_review",
        "predecessor_assessment_request_id": predecessor.assessment_request_id,
        "predecessor_assessment_request_fingerprint": (predecessor.request_fingerprint),
        "predecessor_assessment_result_id": None,
        "predecessor_result_fingerprint": None,
        "predecessor_abandonment_id": None,
        "predecessor_abandonment_fingerprint": None,
        "abandon_predecessor": True,
        "policy_revision_id": policy.policy_revision_id,
        "policy_fingerprint": policy.policy_fingerprint,
        "public_safe_egress_attested": True,
        "max_provider_calls": policy.max_provider_calls,
        "max_total_input_tokens": policy.max_total_input_tokens,
        "max_total_output_tokens": policy.max_total_output_tokens,
        "max_output_tokens_per_call": policy.max_output_tokens_per_call,
    }


def test_explicit_human_generation_one_claims_then_replays_without_redial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    claim_revisions: list[int] = []
    endpoint = _policy_payload()["messages_endpoint"]
    assert type(endpoint) is str

    def adapter_factory(execution):
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            snapshot = store.load_snapshot(run_id)
            assert len(snapshot.post_final_assessment_requests) == 1
            assert snapshot.post_final_assessment_results == ()
            claim_revisions.append(store.current_revision)
        return _MessagesFixtureAdapter(
            execution,
            calls,
            endpoint=endpoint,
            terminal_mode="finding",
        )

    service = PostFinalAssessmentService(
        workspace,
        adapter_factory=adapter_factory,
    )
    assert service.policy_set(_policy_payload())["ok"] is True
    request = _generation_one_run_payload(service)
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")

    outcome = service.assessment_run(request)

    assert outcome["ok"] is True, outcome
    assert outcome["replayed"] is False
    assert outcome["status"] == "available"
    assert len(calls) == 9

    assert claim_revisions == [before_revision + 1]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
        assert store.current_revision == before_revision + 3
    claimed = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]
    assert claimed.schema_version == claimed.series_schema_id
    assert claimed.assessment_generation == 1
    assert claimed.human_request_id == request["human_request_id"]
    assert claimed.predecessor_assessment_request_id is None
    assert result.assessment_request_id == claimed.assessment_request_id
    claim_receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == claimed.accepted_transaction_id
    )
    assert claim_receipt.transaction_type == "post_final_assessment_series_claim"
    database_before = (workspace / "briefloop.db").read_bytes()
    revision_before = snapshot.store_revision

    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("exact Human replay touched adapter")
        ),
    )
    replayed = replay.assessment_run(request)

    assert replayed == {
        "ok": True,
        "replayed": True,
        "status": "available",
        "assessment_result_id": result.assessment_result_id,
        "assessment_result_fingerprint": result.result_fingerprint,
    }
    assert len(calls) == 9
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before


def test_assessment_next_is_a_complete_read_only_generation_one_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    service = PostFinalAssessmentService(workspace)
    assert service.policy_set(_policy_payload())["ok"] is True
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        before_bytes = (workspace / "briefloop.db").read_bytes()
    # The public projection takes the explicit policy id; it does not inspect
    # SQL or require the caller to reconstruct internal fingerprints.
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(_run_id)
    policy_id = snapshot.post_final_assessment_policy_revisions[0].policy_revision_id
    first = service.assessment_next(
        policy_revision_id=policy_id,
        human_actor_id="human-1",
        human_request_id="pf-laj-assessment-next-1",
        assessment_purpose="post_final_review",
    )
    second = service.assessment_next(
        policy_revision_id=policy_id,
        human_actor_id="human-1",
        human_request_id="pf-laj-assessment-next-1",
        assessment_purpose="post_final_review",
    )
    assert first["ok"] is True
    assert first == second
    request = first["request"]
    assert isinstance(request, dict)
    assert request["assessment_generation"] == 1
    assert request["predecessor_assessment_request_id"] is None
    assert request["policy_revision_id"] == policy_id
    assert request["human_request_id"] == "pf-laj-assessment-next-1"
    assert request["max_provider_calls"] == 9
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision
    assert (workspace / "briefloop.db").read_bytes() == before_bytes


def test_assessment_admission_rejects_invalid_ids_before_store_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls)
    policy = service.policy_set(_policy_payload())
    assert policy["ok"] is True
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        before_bytes = (workspace / "briefloop.db").read_bytes()

    invalid_values = ("", " ", "x" * 161, "bad/id")
    for index, value in enumerate(invalid_values):
        preview = service.assessment_next(
            policy_revision_id=policy["policy_revision_id"],
            human_actor_id=value,
            human_request_id=f"valid-human-request-{index}",
            assessment_purpose="post_final_review",
        )
        assert preview == {
            "ok": False,
            "status": "invalid",
            "reason_code": "post_final_assessment_request_invalid",
        }
        request = _generation_one_run_payload(
            service,
            human_request_id=f"valid-human-request-run-{index}",
        )
        request["human_actor_id"] = value
        assert service.assessment_run(request) == {
            "ok": False,
            "status": "invalid",
            "reason_code": "post_final_assessment_request_invalid",
        }

    for index, value in enumerate(invalid_values):
        preview = service.assessment_next(
            policy_revision_id=policy["policy_revision_id"],
            human_actor_id="human-1",
            human_request_id=value,
            assessment_purpose="post_final_review",
        )
        assert preview == {
            "ok": False,
            "status": "invalid",
            "reason_code": "post_final_assessment_request_invalid",
        }
        request = _generation_one_run_payload(
            service,
            human_request_id=value,
        )
        assert service.assessment_run(request) == {
            "ok": False,
            "status": "invalid",
            "reason_code": "post_final_assessment_request_invalid",
        }

    assert calls == []
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == before_revision
    assert (workspace / "briefloop.db").read_bytes() == before_bytes


def test_outcome_unknown_is_atomically_abandoned_before_next_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode="finding")
    assert service.policy_set(_policy_payload())["ok"] is True
    generation_one_request = _generation_one_run_payload(service)

    class _ProcessStop(BaseException):
        pass

    original_execute = post_final_assessment_module.execute_prepared_shadow_run
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    monkeypatch.setattr(
        post_final_assessment_module,
        "execute_prepared_shadow_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_ProcessStop()),
    )
    with pytest.raises(_ProcessStop):
        service.assessment_run(generation_one_request)
    monkeypatch.setattr(
        post_final_assessment_module,
        "execute_prepared_shadow_run",
        original_execute,
    )
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    assert len(snapshot.post_final_assessment_requests) == 1
    assert snapshot.post_final_assessment_results == ()
    assert snapshot.post_final_assessment_abandonments == ()
    predecessor = snapshot.post_final_assessment_requests[0]
    assert calls == []
    pending_item = service.assessment_list()["assessments"][0]
    assert pending_item["terminal_evidence_class"] == "outcome_unknown"
    assert pending_item["abandonment_id"] is None
    assert pending_item["assessment_result_id"] is None
    assert pending_item["assessment_result_fingerprint"] is None
    assert _listed_assessment_result_readout(pending_item) == dict.fromkeys(
        _ASSESSMENT_RESULT_READOUT_FIELDS
    )

    generation_two_request = _abandoning_next_generation_run_payload(
        service,
        human_request_id="pf-laj-assessment-run-after-abandonment",
    )
    generation_two = service.assessment_run(generation_two_request)

    assert generation_two["ok"] is True
    assert generation_two["status"] == "available"
    assert len(calls) == 9
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
    assert len(snapshot.post_final_assessment_requests) == 2
    assert len(snapshot.post_final_assessment_abandonments) == 1
    abandonment = snapshot.post_final_assessment_abandonments[0]
    successor = snapshot.post_final_assessment_requests[1]
    assert abandonment.assessment_request_id == predecessor.assessment_request_id
    assert successor.predecessor_abandonment_id == abandonment.abandonment_id
    assert (
        successor.predecessor_abandonment_fingerprint
        == abandonment.abandonment_fingerprint
    )
    assert abandonment.accepted_transaction_id == successor.accepted_transaction_id
    assert not any(
        result.assessment_request_id == predecessor.assessment_request_id
        for result in snapshot.post_final_assessment_results
    )
    listing = service.assessment_list()
    abandoned_item, successor_item = listing["assessments"]
    assert abandoned_item["assessment_request_id"] == predecessor.assessment_request_id
    assert abandoned_item["terminal_evidence_class"] == "abandoned"
    assert abandoned_item["abandonment_id"] == abandonment.abandonment_id
    assert abandoned_item["assessment_result_id"] is None
    assert abandoned_item["assessment_result_fingerprint"] is None
    assert _listed_assessment_result_readout(abandoned_item) == dict.fromkeys(
        _ASSESSMENT_RESULT_READOUT_FIELDS
    )
    successor_result = snapshot.post_final_assessment_results[0]
    assert successor_item["assessment_request_id"] == successor.assessment_request_id
    assert (
        successor_item["terminal_evidence_class"]
        == successor_result.terminal_evidence_class
    )
    assert successor_item["abandonment_id"] is None
    assert (
        successor_item["assessment_result_id"] == successor_result.assessment_result_id
    )
    assert (
        successor_item["assessment_result_fingerprint"]
        == successor_result.result_fingerprint
    )
    assert _listed_assessment_result_readout(successor_item) == (
        _assessment_result_readout(successor_result)
    )
    database_before = (workspace / "briefloop.db").read_bytes()
    revision_before = snapshot.store_revision

    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("abandonment replay touched adapter")
        ),
    )
    replayed = replay.assessment_run(generation_two_request)
    assert replayed["replayed"] is True
    assert replayed["assessment_result_id"] == generation_two["assessment_result_id"]
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before
        assert len(store.load_snapshot(run_id).post_final_assessment_abandonments) == 1


def test_policy_is_store_owned_replayable_and_manual_view_cannot_override(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    service = PostFinalAssessmentService(workspace)

    first = service.policy_set(_policy_payload())
    second = service.policy_set(_policy_payload())
    status = service.status()
    projection = build_post_final_assessment_projection(workspace)

    assert first["ok"] is True
    assert first["replayed"] is False
    assert second == {
        "ok": True,
        "replayed": True,
        "policy_revision_id": first["policy_revision_id"],
        "receipt_id": first["receipt_id"],
    }
    assert status["ok"] is True
    assert status["status"] == "not_requested"
    assert projection.lifecycle_present is True
    assert projection.status == "not_requested"
    assert projection.view.finding_count == 0


def test_invalid_policy_rejects_before_store_assessment_write(
    tmp_path: Path, monkeypatch
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    service = PostFinalAssessmentService(workspace)
    payload = _policy_payload()
    payload["messages_endpoint"] = "https:// Messages.example.test/v1"

    try:
        service.policy_set(payload)
    except PostFinalAssessmentError as exc:
        assert str(exc) == "post_final_assessment_policy_invalid"
    else:  # pragma: no cover - safety assertion
        raise AssertionError("invalid endpoint was accepted")
    assert service.status()["status"] == "not_requested"


def test_nonfinal_workspace_is_typed_unavailable_without_assessment_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = initialize_workspace(tmp_path / "workspace")
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls)
    database_before = (workspace / "briefloop.db").read_bytes()
    capability_calls: list[Path] = []

    def _unsupported(path: Path) -> object:
        capability_calls.append(path)
        raise CoreRunError("checkout_publication_unsupported")

    monkeypatch.setattr(
        post_final_assessment_module, "capability_profile", _unsupported
    )

    for payload in (
        service.status(),
        service.assess(),
        service.retry("pf-laj-request-not-present"),
    ):
        assert payload == {
            "ok": False,
            "status": "unavailable",
            "reason_code": "post_final_assessment_unavailable",
        }
    projection = build_post_final_assessment_projection(workspace)
    assert projection.lifecycle_present is False
    assert projection.status == "not_requested"
    assert projection.reason_code == "laj_not_run"
    assert projection.view.finding_count == 0
    assert calls == []
    assert capability_calls == []
    assert (workspace / "briefloop.db").read_bytes() == database_before


def test_invalid_facts_precede_publication_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Store/facts errors win before the platform capability boundary."""

    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    service = PostFinalAssessmentService(workspace)
    capability_calls: list[Path] = []

    def _invalid_load() -> object:
        raise PostFinalAssessmentError("control_store_integrity_invalid")

    def _unsupported(path: Path) -> object:
        capability_calls.append(path)
        raise CoreRunError("checkout_publication_unsupported")

    monkeypatch.setattr(service, "_load", _invalid_load)
    monkeypatch.setattr(
        post_final_assessment_module, "capability_profile", _unsupported
    )

    assert service.assess() == {
        "ok": False,
        "status": "unavailable",
        "reason_code": "control_store_integrity_invalid",
    }
    assert capability_calls == []


def test_disabled_policy_and_budget_block_never_claim_or_touch_sdk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls)
    disabled = _policy_payload()
    disabled["enabled"] = False
    disabled["human_request_id"] = "pf-laj-disabled-policy-request"
    assert service.policy_set(disabled)["ok"] is True
    assert service.assess() == {
        "ok": False,
        "status": "not_requested",
        "reason_code": "policy_not_enabled",
    }
    assert calls == []

    bounded = _policy_payload()
    bounded["human_request_id"] = "pf-laj-budget-policy-request"
    bounded["max_provider_calls"] = 8
    assert service.policy_set(bounded)["ok"] is True
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("budget preflight touched SDK metadata")
        ),
    )
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    assert service.assess() == {
        "ok": False,
        "status": "budget_blocked",
        "reason_code": "budget_exceeded",
    }
    assert calls == []
    assert service.status()["assessment_request_id"] is None


def test_terminal_action_revision_attests_exact_assessed_facts_and_replays(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """V01--V03: advisory revisions advance tip facts but not final lineage."""

    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode="finding")
    archive_root = service._archive_root
    assert archive_root == post_final_assessment_archive_root(workspace / ".")
    assert archive_root.is_absolute()
    assert archive_root.parent.name == ".briefloop-post-final-laj"
    assert archive_root.is_relative_to(workspace) is False
    assert archive_root.name != workspace.name
    assert archive_root != post_final_assessment_archive_root(
        tmp_path / "other-workspace"
    )
    before = build_post_final_assessment_projection(workspace)
    assert (
        before.lifecycle_present,
        before.status,
        before.reason_code,
        before.user_status,
        before.request_template,
        before.run_action_available,
    ) == (
        False,
        "unsupported",
        "reader_review_not_supported",
        "not_assessed",
        None,
        False,
    )
    before_facts = build_finalized_local_review_projection(workspace).facts

    policy = service.policy_set(_policy_payload())
    assert policy["ok"] is True and policy["replayed"] is False
    policy_facts = build_finalized_local_review_projection(workspace).facts
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        policy_history = store.load_history()
        policy_snapshot = policy_history.snapshot_at_revision(
            run_id,
            policy_history.store_revision,
        )
    policy_receipt = next(
        item
        for item in policy_snapshot.transactions
        if item.transaction_type == "post_final_assessment_policy"
    )
    before_action = _current_action(
        policy_history,
        run_id,
        policy_receipt.prior_revision,
    )
    policy_action = _current_action(
        policy_history,
        run_id,
        policy_history.store_revision,
    )
    assert before_facts.store_revision != policy_facts.store_revision
    assert before_facts.facts_fingerprint != policy_facts.facts_fingerprint
    assert before_action.action_fingerprint != policy_action.action_fingerprint
    assert finalized_lineage_fingerprint(
        before_facts,
        before_action,
    ) == finalized_lineage_fingerprint(policy_facts, policy_action)

    # The public-safe fixture supplies protocol bytes, not an installed SDK.
    # A live miss still freezes the declared SDK identity before adapter access.
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    first = service.assess()
    assert first["ok"] is True, first
    assert first["status"] == "available"
    assert len(calls) == 9
    current_facts = build_finalized_local_review_projection(workspace).facts
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        history = store.load_history()
        snapshot = history.snapshot_at_revision(run_id, history.store_revision)
    current_action = _current_action(history, run_id, history.store_revision)
    request = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]
    assessment_receipts = [
        item
        for item in snapshot.transactions
        if item.transaction_type.startswith("post_final_assessment_")
    ]
    assert [item.transaction_type for item in assessment_receipts] == [
        "post_final_assessment_policy",
        "post_final_assessment_claim",
        "post_final_assessment_result",
    ]
    # V02: every advisory receipt prefix remains independently verifier-valid;
    # no final Store tip can conceal a broken policy, claim, or result event.
    for receipt in assessment_receipts:
        CoreRunDomainVerifier().verify_history(
            history,
            through_revision=receipt.committed_revision,
        )
    archive_path = trial_archive_path(archive_root, request.trial_id)
    assert archive_path.is_dir()
    projection = build_post_final_assessment_projection(workspace)
    assert (projection.status, projection.reason_code) == (
        "unsupported",
        "reader_review_not_supported",
    )
    persisted = json.dumps(
        {
            "policies": [
                item.model_dump(mode="json")
                for item in snapshot.post_final_assessment_policy_revisions
            ],
            "requests": [
                item.model_dump(mode="json")
                for item in snapshot.post_final_assessment_requests
            ],
            "results": [
                item.model_dump(mode="json")
                for item in snapshot.post_final_assessment_results
            ],
            "receipts": [item.model_dump(mode="json") for item in assessment_receipts],
            "events": [
                item.model_dump(mode="json")
                for item in snapshot.events
                if item.transaction_id
                in {receipt.transaction_id for receipt in assessment_receipts}
            ],
        },
        sort_keys=True,
    )
    assert str(archive_root) not in persisted
    rendered_data = build_brief_pages_data(
        workspace,
        generated_at="2026-07-28T00:00:00Z",
    )
    rendered_html = render_brief_pages_html(rendered_data).decode("utf-8")
    assert str(archive_root) not in json.dumps(rendered_data, sort_keys=True)
    assert str(archive_root) not in rendered_html
    claim_receipt = next(
        item
        for item in snapshot.transactions
        if item.transaction_id == request.accepted_transaction_id
    )
    assert request.finalized_facts_fingerprint == policy_facts.facts_fingerprint
    assert request.finalized_lineage_fingerprint == finalized_lineage_fingerprint(
        current_facts,
        current_action,
    )
    assert result.finalized_facts_fingerprint == request.finalized_facts_fingerprint
    assert result.finalized_lineage_fingerprint == request.finalized_lineage_fingerprint
    assert (
        reassessed_facts_fingerprint(
            current_facts,
            current_action,
            claim_prior_revision=claim_receipt.prior_revision,
        )
        == request.finalized_facts_fingerprint
    )
    assert (
        resolve_current_post_final_assessment_request(
            history,
            snapshot,
            current_facts,
            current_action,
        )
        == request
    )

    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("exact replay touched distribution metadata")
        ),
    )
    monkeypatch.setattr(
        post_final_assessment_module,
        "capability_profile",
        lambda _workspace: (_ for _ in ()).throw(
            AssertionError("existing result replay touched publication capability")
        ),
    )
    replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("exact replay touched the adapter")
        ),
    )
    replay_status = replay.status()
    assess_replay = replay.assess()
    retry = replay.retry(request.assessment_request_id)
    assert replay_status["status"] == "available"
    assert assess_replay == {
        "ok": True,
        "replayed": True,
        "status": result.terminal_evidence_class,
        "assessment_result_id": result.assessment_result_id,
        "assessment_result_fingerprint": result.result_fingerprint,
    }
    assert retry["ok"] is True and retry["replayed"] is True
    assert len(calls) == 9

    # A durable result cannot make a later archive edit look like a recoverable
    # miss.  The qualified projection emits no advice and retry never redials
    # or rewrites the existing result.
    archive_member = archive_path / "presentation_actual.json"
    archive_member.write_bytes(archive_member.read_bytes() + b" ")
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision_before_tamper_retry = store.current_revision
    tampered_projection = build_post_final_assessment_projection(workspace)
    assert tampered_projection.lifecycle_present is True
    assert tampered_projection.status == "unsupported"
    assert tampered_projection.reason_code == "reader_review_not_supported"
    assert tampered_projection.view.finding_count == 0
    tampered_retry = replay.retry(request.assessment_request_id)
    assert tampered_retry == {
        "ok": False,
        "status": "invalid",
        "reason_code": "archive_verification_failed",
    }
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before_tamper_retry
    assert len(calls) == 9

    # A missing archive for a durable nonzero result fails closed without
    # current preparation, a provider attempt, or a Store rewrite.
    import shutil

    shutil.rmtree(archive_path)
    missing_assess = replay.assess()
    assert missing_assess == {
        "ok": False,
        "status": "invalid",
        "reason_code": "archive_verification_failed",
    }
    missing = replay.retry(request.assessment_request_id)
    assert missing == {
        "ok": False,
        "status": "invalid",
        "reason_code": "archive_verification_failed",
    }
    assert len(calls) == 9


@pytest.mark.parametrize("unsafe_kind", ["symlink", "file"])
def test_external_archive_root_fails_closed_before_claim_or_provider(
    tmp_path: Path,
    monkeypatch,
    unsafe_kind: str,
) -> None:
    workspace, _run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls)
    policy = service.policy_set(_policy_payload())
    assert policy["ok"] is True
    root = service._archive_root
    root.parent.mkdir(parents=True, exist_ok=True)
    if unsafe_kind == "symlink":
        target = tmp_path / "archive-root-target"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    else:
        root.write_text("not a directory", encoding="utf-8")

    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("unsafe archive root touched SDK metadata")
        ),
    )
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    blocked = service.assess()

    assert blocked == {
        "ok": False,
        "status": "unavailable",
        "reason_codes": ["archive_root_unsafe"],
    }
    assert calls == []
    assert service.status()["status"] == "not_requested"


@pytest.mark.parametrize(
    ("terminal_mode", "expected_class"),
    (
        ("refusal", "refused"),
        ("truncation", "incomplete"),
        ("transport_failure", "provider_failed"),
    ),
)
def test_terminal_provider_evidence_is_qualified_without_advice_or_redial(
    tmp_path: Path,
    monkeypatch,
    terminal_mode: str,
    expected_class: str,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode=terminal_mode)
    assert service.policy_set(_policy_payload())["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")

    first = service.assess()
    assert first["ok"] is True, first
    assert first["status"] == expected_class
    assert len(calls) == 9
    projection = build_post_final_assessment_projection(workspace)
    assert projection.lifecycle_present is True
    assert projection.status == "unsupported"
    assert projection.reason_code == "reader_review_not_supported"
    assert projection.user_status == "not_assessed"
    assert projection.view.status == "not_available"
    assert projection.view.archive_verified is False
    assert projection.view.binding is None
    assert projection.view.finding_count == 0
    assert projection.view.withheld_finding_count == 0

    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = store.load_snapshot(run_id)
        revision_before = store.current_revision
    request = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]
    assert result.terminal_evidence_class == expected_class
    assert result.finding_count == result.withheld_finding_count == 0
    assert projection.view.reason_codes == ["reader_review_not_supported"]
    database_before = (workspace / "briefloop.db").read_bytes()
    listing = service.assessment_list()
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before
    listed_result = listing["assessments"][0]
    assert listed_result["terminal_evidence_class"] == expected_class
    assert listed_result["assessment_result_id"] == result.assessment_result_id
    assert listed_result["assessment_result_fingerprint"] == result.result_fingerprint
    assert _listed_assessment_result_readout(listed_result) == (
        _assessment_result_readout(result)
    )
    shutil.rmtree(trial_archive_path(service._archive_root, request.trial_id))

    monkeypatch.delenv(ANTHROPIC_API_KEY_SETTING, raising=False)
    monkeypatch.setattr(
        post_final_assessment_module,
        "prepare_shadow_run_from_bytes",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal Store replay touched current preparation")
        ),
    )
    monkeypatch.setattr(
        runner_module.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("terminal evidence replay touched SDK metadata")
        ),
    )
    replay = PostFinalAssessmentService(
        workspace,
        adapter_factory=lambda _execution: (_ for _ in ()).throw(
            AssertionError("terminal evidence replay touched adapter")
        ),
    )
    assessed = replay.assess()
    assert assessed["ok"] is True and assessed["replayed"] is True
    assert assessed["status"] == expected_class
    retry = replay.retry(result.assessment_request_id)
    assert retry["ok"] is True and retry["replayed"] is True
    assert retry["status"] == expected_class
    assert replay.assessment_list() == listing
    replayed_projection = build_post_final_assessment_projection(workspace)
    assert replayed_projection.status == "unsupported"
    assert replayed_projection.reason_code == "reader_review_not_supported"
    assert replayed_projection.view.archive_verified is False
    assert replayed_projection.view.reason_codes == ["reader_review_not_supported"]
    semantic = build_brief_pages_data(workspace)["semantic"]
    assert semantic["status"] == "not_assessed"
    assert semantic["assessment_status"] == "unsupported"
    assert semantic["store_qualified"] is True
    assert semantic["coverage"]["finding_count"] == 0
    assert semantic["findings"] == []
    assert semantic["reason_codes"] == ["reader_review_not_supported"]
    assert semantic["review_actions_available"] is True
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before
    assert len(calls) == 9


def test_store_result_binding_mismatch_stops_before_current_preparation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, run_id, _clock = _finalized_local_workspace(tmp_path, monkeypatch)
    calls: list[tuple[str, int]] = []
    service = _fixture_service(workspace, calls, terminal_mode="transport_failure")
    assert service.policy_set(_policy_payload())["ok"] is True
    monkeypatch.setattr(runner_module.metadata, "version", lambda _name: "0.104.1")
    monkeypatch.setenv(ANTHROPIC_API_KEY_SETTING, "public-synthetic-key")
    first = service.assess()
    assert first["ok"] is True and first["status"] == "provider_failed"

    loaded = service._load()
    facts, snapshot, binding, workspace_id, history, action = loaded
    request = snapshot.post_final_assessment_requests[0]
    result = snapshot.post_final_assessment_results[0]
    mismatched = result.model_copy(update={"finalized_lineage_fingerprint": "f" * 64})
    mismatched_snapshot = replace(
        snapshot,
        post_final_assessment_results=(mismatched,),
    )
    database_before = (workspace / "briefloop.db").read_bytes()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        revision_before = store.current_revision

    replay = PostFinalAssessmentService(workspace)
    monkeypatch.setattr(
        replay,
        "_load",
        lambda: (
            facts,
            mismatched_snapshot,
            binding,
            workspace_id,
            history,
            action,
        ),
    )
    monkeypatch.setattr(
        post_final_assessment_module,
        "prepare_shadow_run_from_bytes",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("mismatched Store result touched current preparation")
        ),
    )
    expected = {
        "ok": False,
        "status": "invalid",
        "reason_code": "post_final_assessment_binding_invalid",
    }
    assert replay.assess() == expected
    assert replay.retry(request.assessment_request_id) == expected
    assert (workspace / "briefloop.db").read_bytes() == database_before
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert store.current_revision == revision_before
        assert store.load_snapshot(run_id).post_final_assessment_results == (result,)
    assert len(calls) == 9
