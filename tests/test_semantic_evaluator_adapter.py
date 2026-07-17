"""Provider request-shape, sizing, and transport mapping tests."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

import multi_agent_brief.semantic_evaluator.adapters.openai_responses as openai_module
from multi_agent_brief.semantic_evaluator.adapter import FrozenProviderRequest
from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OpenAIResponsesAdapterV1,
    _transport_reason,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SyntheticFixtureAdapterV1,
)
from multi_agent_brief.semantic_evaluator.contracts import DIMENSION_RESPONSE_SCHEMA_ID
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.prompt_sizer import (
    OpenAITiktokenPromptSizerV1,
    SyntheticFixturePromptSizerV1,
)


def _request(**overrides) -> FrozenProviderRequest:
    values = {
        "trial_id": "trial-adapter-v1",
        "dimension_id": "cross_section_consistency",
        "attempt_ordinal": 1,
        "system_text": "system",
        "user_text": "user",
        "prompt_request_sha256": "0" * 64,
        "adapter_id": "openai_responses_v1",
        "provider_id": "openai_responses",
        "model_id": "model-fixed-v1",
        "expected_model_version": "model-observed-v1",
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": 256,
        "seed": None,
        "timeout_seconds": 60,
    }
    values.update(overrides)
    return FrozenProviderRequest(**values)


def test_frozen_provider_request_has_no_baseline_workflow_or_tool_surface() -> None:
    fields = set(FrozenProviderRequest.__dataclass_fields__)
    assert fields.isdisjoint(
        {
            "baseline",
            "human_assessment",
            "quality_panel",
            "control_store",
            "workspace",
            "tools",
            "previous_response_id",
            "conversation",
            "memory",
        }
    )
    with pytest.raises(TypeError):
        _request(seed=1)
    with pytest.raises(TypeError):
        _request(max_output_tokens=True)


def test_synthetic_adapter_reads_only_packaged_fixture_and_returns_strict_json() -> (
    None
):
    rubric = {
        "trial_id": "trial-adapter-v1",
        "dimension": {"dimension_id": "cross_section_consistency"},
        "assessment_units": [{"assessment_unit_id": "AU-000000000001"}],
    }
    request = _request(
        adapter_id="synthetic_fixture_v1",
        provider_id="synthetic_fixture",
        model_id="synthetic-fixture-v1",
        expected_model_version="synthetic-fixture-v1",
        user_text=(
            "<CURRENT_RUBRIC>\n"
            + json.dumps(rubric, ensure_ascii=False, separators=(",", ":"))
            + "\n</CURRENT_RUBRIC>"
        ),
    )
    result = SyntheticFixtureAdapterV1().invoke(request)
    payload = json.loads(result.extracted_output or b"")
    assert payload == {
        "dimension_id": "cross_section_consistency",
        "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
        "trial_id": "trial-adapter-v1",
        "unit_results": [
            {
                "assessment_unit_id": "AU-000000000001",
                "disposition": "no_finding",
            }
        ],
    }
    assert result.observed_model_version == "synthetic-fixture-v1"


def test_openai_adapter_disables_sdk_retry_and_sends_only_frozen_responses_shape(
    monkeypatch,
) -> None:
    constructor: dict[str, object] = {}
    request_kwargs: dict[str, object] = {}

    class Raw:
        http_response = SimpleNamespace(content=b'{"transport":"raw"}')

        def parse(self):
            return SimpleNamespace(
                id="response-fixed-v1",
                model="model-observed-v1",
                output_text='{"strict":"json"}',
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
            )

    class WithRaw:
        def create(self, **kwargs):
            request_kwargs.update(kwargs)
            return Raw()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            constructor.update(kwargs)
            self.responses = SimpleNamespace(
                with_raw_response=WithRaw(),
            )

    fake_module = ModuleType("openai")
    fake_module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(openai_module.metadata, "version", lambda name: "2.1.0")
    adapter = OpenAIResponsesAdapterV1()
    result = adapter.invoke(_request())
    assert constructor == {"api_key": "synthetic-test-key", "max_retries": 0}
    assert request_kwargs == {
        "model": "model-fixed-v1",
        "instructions": "system",
        "input": "user",
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": 256,
        "store": False,
        "timeout": 60,
    }
    assert set(request_kwargs).isdisjoint(
        {"tools", "previous_response_id", "conversation", "stream", "seed"}
    )
    assert result.status == "completed"
    assert result.extracted_output == b'{"strict":"json"}'
    assert result.raw_transport_response == b'{"transport":"raw"}'
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (
        10,
        5,
        15,
    )


def test_openai_model_identity_drift_is_terminal_and_output_is_not_accepted() -> None:
    class Raw:
        http_response = SimpleNamespace(content=b"raw")

        def parse(self):
            return SimpleNamespace(
                id="response-v1",
                model="different-model-version",
                output_text='{"would":"otherwise-parse"}',
                usage=None,
            )

    adapter = object.__new__(OpenAIResponsesAdapterV1)
    adapter.provider_sdk_version = "2.1.0"
    adapter._client = SimpleNamespace(
        responses=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=lambda **_kwargs: Raw())
        )
    )
    result = adapter.invoke(_request())
    assert result.status == "failed"
    assert result.reason_code == "provider_identity_mismatch"
    assert result.extracted_output is None
    assert result.raw_transport_response == b"raw"


def test_openai_missing_key_is_value_free_and_zero_call(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SemanticEvaluatorError) as exc_info:
        OpenAIResponsesAdapterV1()
    assert exc_info.value.reason_code == "shadow_adapter_unavailable"
    assert str(exc_info.value) == "shadow_adapter_unavailable"


@pytest.mark.parametrize(
    ("error", "reason"),
    (
        (type("APITimeoutError", (Exception,), {})(), "provider_retryable_failure"),
        (type("APIConnectionError", (Exception,), {})(), "provider_retryable_failure"),
        (
            type("HTTP429", (Exception,), {"status_code": 429})(),
            "provider_retryable_failure",
        ),
        (
            type("HTTP500", (Exception,), {"status_code": 500})(),
            "provider_retryable_failure",
        ),
        (type("HTTP400", (Exception,), {"status_code": 400})(), "provider_failed"),
    ),
)
def test_transport_error_mapping_is_frozen_and_value_free(error, reason: str) -> None:
    assert _transport_reason(error) == reason


def test_prompt_sizers_are_local_strict_and_have_no_unknown_model_fallback(
    monkeypatch,
) -> None:
    synthetic = SyntheticFixturePromptSizerV1()
    assert synthetic.count_tokens(system_text="甲", user_text="乙") == 10
    with pytest.raises(SemanticEvaluatorError) as exc_info:
        synthetic.count_tokens(system_text=True, user_text="乙")
    assert exc_info.value.reason_code == "prompt_sizer_unavailable"

    fake_tiktoken = ModuleType("tiktoken")
    fake_tiktoken.encoding_for_model = lambda _model: (_ for _ in ()).throw(KeyError())
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)
    with pytest.raises(SemanticEvaluatorError) as exc_info:
        OpenAITiktokenPromptSizerV1(model_id="unknown-model")
    assert exc_info.value.reason_code == "prompt_sizer_unavailable"
