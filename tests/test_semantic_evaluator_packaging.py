"""Non-editable wheel resource and instrument-identity parity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_PATHS = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
    ("fixtures", "synthetic_shadow_v1", "manifest.json"),
    ("fixtures", "synthetic_shadow_v1", "bounded_context.json"),
    ("fixtures", "synthetic_shadow_v1", "instrument.json"),
    ("fixtures", "synthetic_shadow_v1", "report.md"),
)
WHEEL_RESOURCE_NAMES = {
    f"multi_agent_brief/semantic_evaluator/{'/'.join(parts)}"
    for parts in RESOURCE_PATHS
}

WHEEL_PROBE = r"""
from copy import deepcopy
from importlib import resources
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

from multi_agent_brief.semantic_evaluator.admission import admit_inputs
from multi_agent_brief.semantic_evaluator.baseline import build_baseline
from multi_agent_brief.semantic_evaluator.composition import (
    compose_actual_laj,
    compose_matched_non_llm,
    verify_composition_record,
)
from multi_agent_brief.semantic_evaluator.contracts import (
    ADMISSION_REQUEST_SCHEMA_ID,
    DIMENSION_RESPONSE_SCHEMA_ID,
    SEMANTIC_EVALUATOR_CONTRACT_MODELS,
    BoundedRequirement,
    CompositionRecord,
    DimensionResponse,
    InstrumentConfig,
    LajCompositionWitness,
    NoFindingResult,
)
from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError
from multi_agent_brief.semantic_evaluator.instrument import build_instrument_manifest
import multi_agent_brief.semantic_evaluator.instrument as instrument_module
import multi_agent_brief.semantic_evaluator.adapter as shadow_adapter_module
import multi_agent_brief.semantic_evaluator.archive as shadow_archive_module
import multi_agent_brief.semantic_evaluator.runner as shadow_runner_module
import multi_agent_brief.semantic_evaluator.shadow_contracts as shadow_contracts_module
import multi_agent_brief.semantic_evaluator.study as study_module
import multi_agent_brief.semantic_evaluator.study_contracts as study_contracts_module
import multi_agent_brief.semantic_evaluator.adapters.anthropic_messages as anthropic_adapter_module
from multi_agent_brief.semantic_evaluator.adapters.anthropic_messages import (
    ANTHROPIC_ADAPTER_ID,
    ANTHROPIC_ADAPTER_VERSION,
    ANTHROPIC_API_KEY_SETTING,
    ANTHROPIC_ENDPOINT_SETTING,
    ANTHROPIC_PROVIDER_ID,
    AnthropicMessagesAdapterV1,
    canonical_messages_endpoint_v1,
    project_anthropic_message_bytes_v1,
    synthetic_anthropic_message_bytes_v1,
)
import multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses as local_proxy_adapter_module
from multi_agent_brief.semantic_evaluator.adapters.local_proxy_responses import (
    CLIPROXY_ADAPTER_ID,
    CLIPROXY_ADAPTER_VERSION,
    CLIPROXY_BASE_URL,
    CLIPROXY_PROVIDER_ID,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SYNTHETIC_ADAPTER_ID,
    SYNTHETIC_ADAPTER_VERSION,
    SYNTHETIC_PROVIDER_ID,
    _load_fixture_manifest,
    _rubric_from_prompt,
)
from multi_agent_brief.semantic_evaluator.normalization import freeze_bounded_context
import multi_agent_brief.semantic_evaluator.normalization as normalization_module
import multi_agent_brief.semantic_evaluator.parser as parser_module
import multi_agent_brief.semantic_evaluator.profile as profile_module
import multi_agent_brief.semantic_evaluator.prompts as prompts_module
import multi_agent_brief.semantic_evaluator.snapshot as snapshot_module
from multi_agent_brief.semantic_evaluator.resources import (
    EvaluatorResourceError,
    resource_sha256,
)
from multi_agent_brief.semantic_evaluator.serialization import (
    canonical_json_bytes,
    canonical_json_text,
    canonical_sha256,
    schema_sha256,
    sha256_bytes,
)
import multi_agent_brief.semantic_evaluator.unit_planner as unit_planner_module
from multi_agent_brief.semantic_evaluator.validator import (
    assemble_semantic_assessment_run,
    make_dimension_attempt_evidence,
)
from multi_agent_brief.cli.main import build_parser
import multi_agent_brief.semantic_evaluator.validator as validator_module
from multi_agent_brief.semantic_evaluator.shadow_contracts import (
    SHADOW_CONTRACT_MODELS_V5,
)
from multi_agent_brief.semantic_evaluator.study_contracts import STUDY_CONTRACT_MODELS


class Sizer:
    sizer_id = "fake-sizer"
    sizer_version = "v1"

    def count_tokens(self, *, system_text, user_text):
        return 10


class ExplodingIdentitySizer:
    def __init__(self, failing_property):
        self.failing_property = failing_property
        self.id_reads = 0
        self.version_reads = 0
        self.calls = 0

    @property
    def sizer_id(self):
        self.id_reads += 1
        if self.failing_property == "sizer_id":
            raise RuntimeError("synthetic hidden identity")
        return "fake-sizer"

    @property
    def sizer_version(self):
        self.version_reads += 1
        if self.failing_property == "sizer_version":
            raise RuntimeError("synthetic hidden identity")
        return "v1"

    def count_tokens(self, *, system_text, user_text):
        self.calls += 1
        return 10


class AnthropicProbeAdapter:
    def __init__(self, execution, endpoint, mode):
        self.adapter_id = execution.adapter_id
        self.adapter_version = execution.adapter_version
        self.provider_sdk_name = execution.provider_sdk_name
        self.provider_sdk_version = execution.provider_sdk_version
        self.qualification_eligible = execution.qualification_eligible
        self.base_url = endpoint
        self.mode = mode
        self._delegate = object.__new__(AnthropicMessagesAdapterV1)
        self.calls = 0
        self.parse_calls = 0
        self.provider_calls = 0

    def invoke(self, request):
        self.calls += 1
        if self.mode == "retry_then_success" and request.attempt_ordinal == 1:
            return self._delegate._transport_attempt(
                request=request,
                kind="timeout",
            )
        if self.mode == "terminal_transport":
            return self._delegate._transport_attempt(
                request=request,
                kind="adapter_error",
            )
        rubric = _rubric_from_prompt(request.user_text)
        output = canonical_json_bytes(
            {
                "dimension_id": request.dimension_id,
                "schema_version": DIMENSION_RESPONSE_SCHEMA_ID,
                "trial_id": request.trial_id,
                "unit_results": [
                    {
                        "assessment_unit_id": item["assessment_unit_id"],
                        "disposition": "no_finding",
                    }
                    for item in rubric["assessment_units"]
                ],
            }
        )
        if self.mode in {
            "http_captured_empty",
            "http_unreadable_none",
            "parse_failure_invalid_utf8",
        }:
            class TimeoutError(Exception):
                pass

            class ConnectionError(Exception):
                pass

            class StatusError(Exception):
                pass

            if self.mode == "parse_failure_invalid_utf8":
                raw = b"\xffwheel-captured-response"
                sentinel = "wheel-parse-provider-diagnostic-must-not-survive"

                def parse():
                    self.parse_calls += 1
                    raise RuntimeError(sentinel)

                def create(**_kwargs):
                    self.provider_calls += 1
                    return SimpleNamespace(
                        http_response=SimpleNamespace(content=raw),
                        parse=parse,
                    )

            else:
                content = b"" if self.mode == "http_captured_empty" else None

                def create(**_kwargs):
                    self.provider_calls += 1
                    error = StatusError(
                        "wheel-http-provider-diagnostic-must-not-survive"
                    )
                    error.status_code = 503
                    error.response = SimpleNamespace(content=content)
                    raise error

            self._delegate._client = SimpleNamespace(
                messages=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=create),
                )
            )
            self._delegate._anthropic = SimpleNamespace(
                APITimeoutError=TimeoutError,
                APIConnectionError=ConnectionError,
                APIStatusError=StatusError,
            )
            return self._delegate.invoke(request)
        stop_reason = {
            "malformed_missing_id": "end_turn",
            "success": "end_turn",
            "retry_then_success": "end_turn",
            "stop_sequence_surrogate": "end_turn",
            "truncation": "max_tokens",
            "refusal": "refusal",
        }[self.mode]
        raw = synthetic_anthropic_message_bytes_v1(
            stop_reason=stop_reason,
            response_id=f"msg-wheel-{self.calls}",
            model=request.expected_model_version,
            content=[
                {
                    "type": "thinking",
                    "thinking": "non-output",
                    "signature": "public-signature",
                },
                {"type": "text", "text": output.decode("utf-8")},
            ],
            input_tokens=7,
            output_tokens=5,
        )
        if self.mode in {"malformed_missing_id", "stop_sequence_surrogate"}:
            payload = json.loads(raw)
            if self.mode == "malformed_missing_id":
                payload.pop("id")
            else:
                payload["stop_sequence"] = "\ud800"
            raw = json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        return self._delegate._attempt_from_response(
            request=request,
            raw=raw,
            sdk_response=None,
        )


def _anthropic_result_projection(result):
    return {
        "archive_complete": result.archive_complete,
        "execution_origin": result.execution_origin,
        "ok": result.ok,
        "qualification_class": result.qualification_class,
        "qualification_eligible": result.qualification_eligible,
        "reason_codes": list(result.reason_codes),
        "replayed": result.replayed,
        "run_status": result.run_status,
        "validation_status": result.validation_status,
    }


def _rehash_anthropic_probe_outer(archive, changed_member):
    manifest_path = archive / "archive_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    members = manifest["payload_members"]
    for member in members:
        if member["path"] == changed_member:
            raw = (archive / changed_member).read_bytes()
            member["size_bytes"] = len(raw)
            member["sha256"] = sha256_bytes(raw)
            break
    else:
        raise RuntimeError("missing changed member")
    manifest["aggregate_payload_sha256"] = canonical_sha256(members)
    manifest["archive_id"] = (
        "archive-"
        + canonical_sha256(
            [
                manifest["shadow_request_sha256"],
                manifest["aggregate_payload_sha256"],
            ]
        )[:16]
    )
    manifest["archive_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "archive_manifest_sha256"
        }
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    receipt_path = archive / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["archive_id"] = manifest["archive_id"]
    receipt["archive_manifest_sha256"] = manifest["archive_manifest_sha256"]
    receipt["receipt_id"] = (
        "receipt-"
        + canonical_sha256(
            [receipt["archive_manifest_sha256"], receipt["run_id"]]
        )[:16]
    )
    receipt["receipt_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
    )
    receipt_raw = canonical_json_bytes(receipt)
    receipt_path.write_bytes(receipt_raw)
    (archive / "COMPLETE").write_bytes(
        (sha256_bytes(receipt_raw) + "\n").encode("ascii")
    )


def anthropic_archive_probe():
    endpoint_a = "https://messages-a.example.test/v1"
    endpoint_b = "https://messages-b.example.test/v1"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        inputs = root / "inputs"
        inputs.mkdir()
        fixture_root = resources.files(
            "multi_agent_brief.semantic_evaluator"
        ).joinpath("fixtures", "synthetic_shadow_v1")
        report = inputs / "report.md"
        context = inputs / "bounded_context.json"
        instrument = inputs / "instrument.json"
        report.write_bytes(fixture_root.joinpath("report.md").read_bytes())
        context.write_bytes(fixture_root.joinpath("bounded_context.json").read_bytes())
        instrument_payload = json.loads(
            fixture_root.joinpath("instrument.json").read_bytes()
        )
        instrument_payload.update(
            {
                "instrument_config_id": "anthropic-wheel-probe-v1",
                "provider_id": ANTHROPIC_PROVIDER_ID,
                "model_id": "public-nonclaude-model-v1",
                "model_version": "public-nonclaude-model-v1",
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
                "retry_policy": {
                    "backoff_schedule_ms": [0],
                    "max_attempts": 2,
                    "retryable_reason_codes": ["provider_retryable_failure"],
                },
            }
        )
        instrument.write_bytes(canonical_json_bytes(instrument_payload))
        model_instrument = inputs / "instrument-model-change.json"
        model_instrument_payload = dict(instrument_payload)
        model_instrument_payload["model_id"] = "other-public-nonclaude-model-v1"
        model_instrument_payload["model_version"] = (
            "other-public-nonclaude-model-v1"
        )
        model_instrument.write_bytes(canonical_json_bytes(model_instrument_payload))
        os.environ[ANTHROPIC_ENDPOINT_SETTING] = endpoint_a
        shadow_runner_module.metadata.version = lambda _name: "0.104.1"

        def run_case(mode, trial_id, current_instrument=instrument):
            adapters = []

            def factory(execution):
                adapter = AnthropicProbeAdapter(execution, endpoint_a, mode)
                adapters.append(adapter)
                return adapter

            invocation = {
                "report": report,
                "bounded_context": context,
                "profile": "research_design_report_zh_v1",
                "instrument": current_instrument,
                "trial_id": trial_id,
                "archive_root": root / f"archives-{mode}",
                "clock": lambda: "2026-07-27T00:00:00Z",
                "sleep": lambda _seconds: None,
            }
            result = shadow_runner_module.run_shadow(
                **invocation,
                adapter_factory=factory,
            )
            return (
                result,
                adapters[0].calls,
                invocation,
                {
                    "parse": adapters[0].parse_calls,
                    "provider": adapters[0].provider_calls,
                },
            )

        first, success_calls, invocation, _ = run_case(
            "success",
            "trial-anthropic-wheel-success",
        )
        truncation, truncation_calls, _, _ = run_case(
            "truncation",
            "trial-anthropic-wheel-truncation",
        )
        refusal, refusal_calls, _, _ = run_case(
            "refusal",
            "trial-anthropic-wheel-refusal",
        )
        retry, retry_calls, _, _ = run_case(
            "retry_then_success",
            "trial-anthropic-wheel-retry",
        )
        terminal, terminal_calls, _, _ = run_case(
            "terminal_transport",
            "trial-anthropic-wheel-terminal",
        )
        malformed, malformed_calls, malformed_invocation, _ = run_case(
            "malformed_missing_id",
            "trial-anthropic-wheel-malformed",
        )
        stop_sequence, stop_sequence_calls, stop_sequence_invocation, _ = run_case(
            "stop_sequence_surrogate",
            "trial-anthropic-wheel-stop-sequence",
        )
        (
            parse_failure,
            parse_failure_calls,
            parse_failure_invocation,
            parse_failure_boundary_calls,
        ) = run_case(
            "parse_failure_invalid_utf8",
            "trial-anthropic-wheel-parse-failure",
        )
        (
            http_captured,
            http_captured_calls,
            http_captured_invocation,
            http_captured_boundary_calls,
        ) = run_case(
            "http_captured_empty",
            "trial-anthropic-wheel-http-captured",
        )
        (
            http_unreadable,
            http_unreadable_calls,
            http_unreadable_invocation,
            http_unreadable_boundary_calls,
        ) = run_case(
            "http_unreadable_none",
            "trial-anthropic-wheel-http-unreadable",
        )
        archive = Path(first.archive_path)
        before = {
            path.relative_to(archive).as_posix(): sha256_bytes(path.read_bytes())
            for path in archive.rglob("*")
            if path.is_file()
        }
        parse_failure_archive = Path(parse_failure.archive_path)
        parse_failure_records = [
            json.loads(path.read_bytes())
            for path in sorted(
                parse_failure_archive.glob("attempts/*/*/transport.json")
            )
        ]
        parse_failure_responses = sorted(
            parse_failure_archive.glob("attempts/*/*/response.body")
        )
        parse_failure_sdk_members = sorted(
            parse_failure_archive.glob("attempts/*/*/sdk_projection.json")
        )
        parse_failure_evidence = {
            "exact_raw_retained": all(
                path.read_bytes() == b"\xffwheel-captured-response"
                for path in parse_failure_responses
            ),
            "present_invalid": all(
                record["facts"]["transport_kind"] == "response"
                and record["facts"]["envelope"]["state"] == "present_invalid"
                and record["shadow_reason"] == "provider_boundary_invalid"
                and record["retry_eligible"] is False
                and record["output_eligible"] is False
                for record in parse_failure_records
            ),
            "response_count": len(parse_failure_responses),
            "sdk_body_invalid": all(
                json.loads(path.read_bytes())["body_state"] == "invalid"
                for path in parse_failure_sdk_members
            ),
            "sentinel_absent": all(
                b"wheel-parse-provider-diagnostic-must-not-survive"
                not in path.read_bytes()
                for path in parse_failure_sdk_members
            ),
        }
        http_captured_archive = Path(http_captured.archive_path)
        http_captured_records = [
            json.loads(path.read_bytes())
            for path in sorted(
                http_captured_archive.glob("attempts/*/*/transport.json")
            )
        ]
        http_captured_responses = sorted(
            http_captured_archive.glob("attempts/*/*/response.body")
        )
        http_captured_sdk_members = sorted(
            http_captured_archive.glob("attempts/*/*/sdk_projection.json")
        )
        empty_sha = sha256_bytes(b"")
        http_captured_evidence = {
            "exact_empty_retained": all(
                path.read_bytes() == b"" for path in http_captured_responses
            ),
            "present_invalid": all(
                record["facts"]["transport_kind"] == "http_error"
                and record["facts"]["envelope"] == {
                    "invalid_code": "envelope_projection_failed",
                    "raw_sha256": empty_sha,
                    "raw_size_bytes": 0,
                    "state": "present_invalid",
                }
                and record["facts"]["http_status"] == {
                    "invalid_code": None,
                    "state": "present_valid",
                    "value": 503,
                }
                and record["raw_transport_response_sha256"] == empty_sha
                and record["shadow_reason"] == "provider_boundary_invalid"
                and record["retry_eligible"] is False
                and record["output_eligible"] is False
                for record in http_captured_records
            ),
            "response_count": len(http_captured_responses),
            "sdk_body_invalid": all(
                json.loads(path.read_bytes())["body_state"] == "invalid"
                for path in http_captured_sdk_members
            ),
            "sentinel_absent": all(
                b"wheel-http-provider-diagnostic-must-not-survive"
                not in path.read_bytes()
                for path in http_captured_sdk_members
            ),
        }
        http_unreadable_archive = Path(http_unreadable.archive_path)
        http_unreadable_records = [
            json.loads(path.read_bytes())
            for path in sorted(
                http_unreadable_archive.glob("attempts/*/*/transport.json")
            )
        ]
        http_unreadable_responses = sorted(
            http_unreadable_archive.glob("attempts/*/*/response.body")
        )
        http_unreadable_sdk_members = sorted(
            http_unreadable_archive.glob("attempts/*/*/sdk_projection.json")
        )
        http_unreadable_evidence = {
            "absent_envelope_without_raw_hash": all(
                record["facts"]["transport_kind"] == "http_error"
                and record["facts"]["envelope"] == {
                    "invalid_code": None,
                    "raw_sha256": None,
                    "raw_size_bytes": None,
                    "state": "absent",
                }
                and record["facts"]["http_status"] == {
                    "invalid_code": None,
                    "state": "present_valid",
                    "value": 503,
                }
                and record["raw_transport_response_sha256"] is None
                and record["shadow_reason"] == "provider_boundary_invalid"
                and record["retry_eligible"] is False
                and record["output_eligible"] is False
                for record in http_unreadable_records
            ),
            "empty_sha_absent": all(
                empty_sha.encode("ascii") not in canonical_json_bytes(record)
                for record in http_unreadable_records
            )
            and all(
                empty_sha.encode("ascii") not in path.read_bytes()
                for path in http_unreadable_sdk_members
            ),
            "response_count": len(http_unreadable_responses),
            "sdk_body_invalid": all(
                json.loads(path.read_bytes())["body_state"] == "invalid"
                for path in http_unreadable_sdk_members
            ),
            "sentinel_absent": all(
                b"wheel-http-provider-diagnostic-must-not-survive"
                not in path.read_bytes()
                for path in http_unreadable_sdk_members
            ),
        }

        policy_payload = {
            "schema_version": (
                study_contracts_module.PROVIDER_BUDGET_POLICY_SCHEMA_ID
            ),
            "max_provider_calls": 18,
            "max_input_tokens": 10_000_000,
        }
        budget_policy = (
            study_contracts_module.LajProviderBudgetPolicyV1.model_validate(
                {
                    **policy_payload,
                    "policy_sha256": canonical_sha256(policy_payload),
                }
            )
        )
        prepared_a = shadow_runner_module.prepare_shadow_run(
            report=report,
            bounded_context=context,
            profile="research_design_report_zh_v1",
            instrument=instrument,
            trial_id="trial-anthropic-study-a",
            archive_root=root / "study-a",
        )
        authorization_a = study_module.make_execution_authorization(
            study_id="study-anthropic-wheel",
            prepared=prepared_a,
            policy=budget_policy,
        )
        preflight_a = study_module.compute_budget_preflight(
            prepared=prepared_a,
            authorization=authorization_a,
            policy=budget_policy,
        )
        os.environ[ANTHROPIC_ENDPOINT_SETTING] = endpoint_b
        prepared_b = shadow_runner_module.prepare_shadow_run(
            report=report,
            bounded_context=context,
            profile="research_design_report_zh_v1",
            instrument=instrument,
            trial_id="trial-anthropic-study-b",
            archive_root=root / "study-b",
        )
        authorization_b = study_module.make_execution_authorization(
            study_id="study-anthropic-wheel",
            prepared=prepared_b,
            policy=budget_policy,
        )
        os.environ[ANTHROPIC_ENDPOINT_SETTING] = endpoint_a
        prepared_model = shadow_runner_module.prepare_shadow_run(
            report=report,
            bounded_context=context,
            profile="research_design_report_zh_v1",
            instrument=model_instrument,
            trial_id="trial-anthropic-study-model",
            archive_root=root / "study-model",
        )
        authorization_model = study_module.make_execution_authorization(
            study_id="study-anthropic-wheel",
            prepared=prepared_model,
            policy=budget_policy,
        )

        os.environ.pop(ANTHROPIC_API_KEY_SETTING, None)
        sys.modules["anthropic"] = None
        metadata_calls = []

        def forbidden_metadata(name):
            metadata_calls.append(name)
            raise RuntimeError("replay touched distribution metadata")

        shadow_runner_module.metadata.version = forbidden_metadata
        replay = shadow_runner_module.run_shadow(
            **invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("replay touched adapter")
            ),
        )
        malformed_replay = shadow_runner_module.run_shadow(
            **malformed_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("malformed replay touched adapter")
            ),
        )
        stop_sequence_replay = shadow_runner_module.run_shadow(
            **stop_sequence_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("stop-sequence replay touched adapter")
            ),
        )
        parse_failure_replay = shadow_runner_module.run_shadow(
            **parse_failure_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("parse-failure replay touched adapter")
            ),
        )
        http_captured_replay = shadow_runner_module.run_shadow(
            **http_captured_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("HTTP captured replay touched adapter")
            ),
        )
        http_unreadable_replay = shadow_runner_module.run_shadow(
            **http_unreadable_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("HTTP unreadable replay touched adapter")
            ),
        )
        after = {
            path.relative_to(archive).as_posix(): sha256_bytes(path.read_bytes())
            for path in archive.rglob("*")
            if path.is_file()
        }
        sdk_projection = next(archive.glob("attempts/*/*/sdk_projection.json"))
        sdk_payload = json.loads(sdk_projection.read_bytes())
        sdk_payload["http_status"] = {
            "invalid_code": None,
            "state": "present_valid",
            "value": 200,
        }
        sdk_projection.write_bytes(canonical_json_bytes(sdk_payload))
        _rehash_anthropic_probe_outer(
            archive,
            sdk_projection.relative_to(archive).as_posix(),
        )
        tampered = shadow_runner_module.run_shadow(
            **invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("tampered replay touched adapter")
            ),
        )
        malformed_archive = Path(malformed.archive_path)
        malformed_sdk = next(
            malformed_archive.glob("attempts/*/*/sdk_projection.json")
        )
        malformed_sdk_payload = json.loads(malformed_sdk.read_bytes())
        tampered_id = b"msg-rehashed-invalid-raw"
        malformed_sdk_payload["response_id"] = {
            "invalid_code": None,
            "state": "present_valid",
            "utf8_hex": tampered_id.hex(),
            "utf8_sha256": sha256_bytes(tampered_id),
        }
        malformed_sdk.write_bytes(canonical_json_bytes(malformed_sdk_payload))
        _rehash_anthropic_probe_outer(
            malformed_archive,
            malformed_sdk.relative_to(malformed_archive).as_posix(),
        )
        invalid_raw_sdk_tamper = shadow_runner_module.run_shadow(
            **malformed_invocation,
            adapter_factory=lambda _execution: (_ for _ in ()).throw(
                RuntimeError("invalid-raw SDK tamper touched adapter")
            ),
        )
        return {
            "archive_results": {
                "http_captured_empty": _anthropic_result_projection(
                    http_captured
                ),
                "http_unreadable_none": _anthropic_result_projection(
                    http_unreadable
                ),
                "malformed_missing_id": _anthropic_result_projection(malformed),
                "parse_failure_invalid_utf8": _anthropic_result_projection(
                    parse_failure
                ),
                "refusal": _anthropic_result_projection(refusal),
                "retry_then_success": _anthropic_result_projection(retry),
                "stop_sequence_surrogate": _anthropic_result_projection(
                    stop_sequence
                ),
                "success": _anthropic_result_projection(first),
                "terminal_transport": _anthropic_result_projection(terminal),
                "truncation": _anthropic_result_projection(truncation),
            },
            "mocked_adapter_calls": {
                "http_captured_empty": http_captured_calls,
                "http_unreadable_none": http_unreadable_calls,
                "malformed_missing_id": malformed_calls,
                "parse_failure_invalid_utf8": parse_failure_calls,
                "refusal": refusal_calls,
                "retry_then_success": retry_calls,
                "stop_sequence_surrogate": stop_sequence_calls,
                "success": success_calls,
                "terminal_transport": terminal_calls,
                "truncation": truncation_calls,
            },
            "real_provider_calls": 0,
            "actual_invoke_boundary_calls": parse_failure_boundary_calls,
            "http_actual_invoke_boundary_calls": {
                "captured_empty": http_captured_boundary_calls,
                "unreadable_none": http_unreadable_boundary_calls,
            },
            "http_captured_evidence": http_captured_evidence,
            "http_unreadable_evidence": http_unreadable_evidence,
            "parse_failure_evidence": parse_failure_evidence,
            "archive_files_hash": canonical_sha256(before),
            "archive_replay_unchanged": after == before,
            "receipt_id": first.receipt_id,
            "replay_receipt_id": replay.receipt_id,
            "replayed": replay.replayed,
            "malformed_replayed": malformed_replay.replayed,
            "malformed_replay_reason_codes": list(
                malformed_replay.reason_codes
            ),
            "stop_sequence_replayed": stop_sequence_replay.replayed,
            "stop_sequence_replay_reason_codes": list(
                stop_sequence_replay.reason_codes
            ),
            "parse_failure_replayed": parse_failure_replay.replayed,
            "parse_failure_replay_reason_codes": list(
                parse_failure_replay.reason_codes
            ),
            "http_captured_replayed": http_captured_replay.replayed,
            "http_captured_replay_reason_codes": list(
                http_captured_replay.reason_codes
            ),
            "http_unreadable_replayed": http_unreadable_replay.replayed,
            "http_unreadable_replay_reason_codes": list(
                http_unreadable_replay.reason_codes
            ),
            "replay_metadata_calls": len(metadata_calls),
            "sdk_status_tamper_reason_codes": list(tampered.reason_codes),
            "invalid_raw_sdk_tamper_reason_codes": list(
                invalid_raw_sdk_tamper.reason_codes
            ),
            "study_identity": {
                "authorization_a_sha256": authorization_a.authorization_sha256,
                "authorization_b_sha256": authorization_b.authorization_sha256,
                "authorization_model_sha256": (
                    authorization_model.authorization_sha256
                ),
                "count_semantics": preflight_a.count_semantics,
                "endpoint_changes_authorization": (
                    authorization_a.authorization_sha256
                    != authorization_b.authorization_sha256
                ),
                "endpoint_changes_execution": (
                    authorization_a.execution_sha256
                    != authorization_b.execution_sha256
                ),
                "model_changes_authorization": (
                    authorization_a.authorization_sha256
                    != authorization_model.authorization_sha256
                ),
                "model_changes_execution": (
                    authorization_a.execution_sha256
                    != authorization_model.execution_sha256
                ),
                "raw_endpoint_absent": (
                    endpoint_a.encode("ascii")
                    not in canonical_json_bytes(authorization_a)
                ),
            },
        }


resource_paths = (
    ("profiles", "research_design_report_zh_v1.yaml"),
    ("prompts", "system_v1.txt"),
    ("prompts", "dimension_v1.txt"),
    ("baselines", "structured_checklist_zh_v1.yaml"),
)
config_payload = deepcopy(InstrumentConfig.minimal_example)
config_payload["retry_policy"] = {
    "max_attempts": 2,
    "retryable_reason_codes": ["provider_retryable_failure"],
    "backoff_schedule_ms": [0],
}
config = InstrumentConfig.model_validate(config_payload)
report = "# 合成 wheel parity 报告\n\n当前状态为 HOLD。\n".encode()
context = freeze_bounded_context(
    context_id="context-wheel-parity",
    data_class="synthetic",
    requirements=[
        BoundedRequirement(
            requirement_id="REQ-WHEEL-1",
            type="must_answer",
            text="说明当前状态。",
            source_locator="synthetic:wheel",
        )
    ],
)
request = {
    "schema_version": ADMISSION_REQUEST_SCHEMA_ID,
    "artifact_id": "reader-wheel-parity",
    "trial_id": "trial-wheel-parity",
    "report_bytes_hex": report.hex(),
    "declared_report_sha256": sha256_bytes(report),
    "bounded_context": context,
    "declared_bounded_context_sha256": context.context_sha256,
    "instrument_config": config,
    "public_data_attestation": True,
    "private_or_confidential_material": False,
    "archive_root": None,
    "workspace_root": None,
}
decision = admit_inputs(
    request,
    prompt_sizer=Sizer(),
)
if not decision.admitted:
    raise RuntimeError("synthetic parity admission failed")


def prompt_for(dimension_id):
    return next(
        item for item in decision.prompts if item.dimension_id == dimension_id
    )


attempts = []
for prompt in decision.prompts:
    units = [
        item
        for item in decision.assessment_plan.units
        if item.dimension_id == prompt.dimension_id
    ]
    response = DimensionResponse(
        schema_version=DIMENSION_RESPONSE_SCHEMA_ID,
        trial_id=decision.assessment_plan.trial_id,
        dimension_id=prompt.dimension_id,
        unit_results=[
            NoFindingResult(
                assessment_unit_id=item.assessment_unit_id,
                disposition="no_finding",
            )
            for item in units
        ],
    )
    attempts.append(
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=prompt,
            attempt_ordinal=1,
            status="completed",
            raw_response_bytes=canonical_json_bytes(response),
        )
    )
assembled = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=attempts,
)
baseline = build_baseline(
    report_evidence=decision.report_evidence,
    reader_artifact=decision.reader.artifact,
    bounded_context=decision.bounded_context,
)
matched = compose_matched_non_llm(
    report_evidence=decision.report_evidence,
    reader_artifact=decision.reader.artifact,
    bounded_context=decision.bounded_context,
)
actual = compose_actual_laj(assembled.witness)


def semantic_error_reason(callback):
    try:
        callback()
    except SemanticEvaluatorError as exc:
        return exc.reason_code
    return "unexpected_success"


def admission_reason(
    changes,
    *,
    prompt_sizer=Sizer(),
    existing_binding=None,
    loaded_profile=None,
):
    candidate = deepcopy(request)
    candidate.update(changes)
    return list(
        admit_inputs(
            candidate,
            prompt_sizer=prompt_sizer,
            existing_binding=existing_binding,
            loaded_profile=loaded_profile,
        ).reason_codes
    )


tampered_attempt = attempts[0].model_copy(
    update={"evidence_sha256": "0" * 64}
)
extra_config = config.model_copy(
    update={"unknown_extra": "PRIVATE_SYNTHETIC_CONFIG_EXTRA"}
)
nested_extra_config = config.model_copy(
    update={
        "retry_policy": config.retry_policy.model_copy(
            update={"unknown_extra": "PRIVATE_SYNTHETIC_RETRY_EXTRA"}
        )
    }
)
canary_tamper_payload = attempts[0].model_dump(mode="json")
canary_tamper_payload["forbidden_canary_values"] = []
canary_tamper_payload["evidence_sha256"] = canonical_sha256(
    {
        key: value
        for key, value in canary_tamper_payload.items()
        if key != "evidence_sha256"
    }
)
canary_tampered_attempt = attempts[0].model_copy(update=canary_tamper_payload)
parser_attempt = make_dimension_attempt_evidence(
    trial_id=decision.input_binding.trial_id,
    prompt=prompt_for(attempts[0].dimension_id),
    attempt_ordinal=1,
    status="completed",
    raw_response_bytes=b"\xff",
)
parser_projection = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=[parser_attempt, *attempts[1:]],
)
security_prompt = prompt_for(attempts[0].dimension_id)
security_canary = security_prompt.forbidden_canary_values[0]
escaped_canary = "".join(
    f"\\u00{ord(character):02x}" for character in security_canary
).encode()
security_attempt = make_dimension_attempt_evidence(
    trial_id=decision.input_binding.trial_id,
    prompt=security_prompt,
    attempt_ordinal=1,
    status="completed",
    raw_response_bytes=b'{"value":"' + escaped_canary + b'"} trailing',
)
security_projection = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=[security_attempt, *attempts[1:]],
)
security_composition = compose_actual_laj(security_projection.witness)
provider_projection = assemble_semantic_assessment_run(
    admission=decision,
    dimension_attempt_evidence=[
        make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=prompt_for(item.dimension_id),
            attempt_ordinal=1,
            status="failed",
            reason_code="provider_failed",
        )
        for item in attempts
    ],
)
witness_payload = assembled.witness.model_dump(mode="json")
witness_payload["run"]["run_status"] = "archive_failed"
witness_payload["witness_sha256"] = canonical_sha256(
    {
        key: value
        for key, value in witness_payload.items()
        if key != "witness_sha256"
    }
)
forged_witness = LajCompositionWitness.model_validate(witness_payload)
composition_payload = actual.model_dump(mode="json")
composition_payload["laj_run_status"] = "incomplete"
composition_payload["laj_validation_status"] = "incomplete"
composition_payload["composition_sha256"] = canonical_sha256(
    {
        key: value
        for key, value in composition_payload.items()
        if key != "composition_sha256"
    }
)
forged_composition = CompositionRecord.model_validate(composition_payload)
different_report = b"# different synthetic parity report\n"
original_source_hasher = instrument_module.source_sha256_for_module
original_profile_resource = profile_module.resource_text
original_prompt_resource = snapshot_module.resource_text


def fail_source_resolution(_module_name):
    raise EvaluatorResourceError("evaluator_source_unavailable")


instrument_module.source_sha256_for_module = fail_source_resolution
component_source_failure = {
    "admission": admission_reason({}),
    "assembly": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=attempts,
        )
    ),
    "witness": semantic_error_reason(lambda: compose_actual_laj(assembled.witness)),
}
instrument_module.source_sha256_for_module = original_source_hasher


def fail_profile_resource(*_args):
    raise OSError("/private/synthetic-customer/profile.yaml")


profile_module.resource_text = fail_profile_resource
profile_source_failure = {
    "admission": admission_reason({}),
    "assembly": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=attempts,
        )
    ),
    "witness": semantic_error_reason(lambda: compose_actual_laj(assembled.witness)),
}
profile_module.resource_text = original_profile_resource


def fail_prompt_resource(*_parts):
    raise EvaluatorResourceError("evaluator_resource_unavailable")


snapshot_module.resource_text = fail_prompt_resource
prompt_source_failure = {
    "admission": admission_reason({}),
    "assembly": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=attempts,
        )
    ),
    "witness": semantic_error_reason(lambda: compose_actual_laj(assembled.witness)),
}
snapshot_module.resource_text = original_prompt_resource

identity_failures = {}
for property_name in ("sizer_id", "sizer_version"):
    exploding_sizer = ExplodingIdentitySizer(property_name)
    identity_failures[property_name] = {
        "reason": admission_reason({}, prompt_sizer=exploding_sizer),
        "id_reads": exploding_sizer.id_reads,
        "version_reads": exploding_sizer.version_reads,
        "count_calls": exploding_sizer.calls,
    }
failure_results = {
    "admission_extra": admission_reason({"unexpected": "synthetic"}),
    "admission_empty": admission_reason({"report_bytes_hex": ""}),
    "admission_utf8": admission_reason(
        {
            "report_bytes_hex": b"\xff".hex(),
            "declared_report_sha256": sha256_bytes(b"\xff"),
        }
    ),
    "admission_sha": admission_reason({"declared_report_sha256": "0" * 64}),
    "admission_policy": admission_reason({"public_data_attestation": False}),
    "admission_private": admission_reason(
        {"private_or_confidential_material": True}
    ),
    "admission_archive": admission_reason({"archive_root": "/tmp/synthetic"}),
    "admission_sizer": admission_reason({}, prompt_sizer=None),
    "admission_profile_invalid": admission_reason({}, loaded_profile={}),
    "admission_binding_invalid": admission_reason({}, existing_binding=object()),
    "admission_trial_conflict": admission_reason(
        {
            "report_bytes_hex": different_report.hex(),
            "declared_report_sha256": sha256_bytes(different_report),
        },
        existing_binding=decision.input_binding,
    ),
    "attempt_integrity": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[tampered_attempt, *attempts[1:]],
        )
    ),
    "attempt_unknown_reason": semantic_error_reason(
        lambda: make_dimension_attempt_evidence(
            trial_id=decision.input_binding.trial_id,
            prompt=prompt_for(attempts[0].dimension_id),
            attempt_ordinal=1,
            status="failed",
            reason_code="PRIVATE_SYNTHETIC_CALLER_REASON",
        )
    ),
    "attempt_retry_not_exhausted": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[
                make_dimension_attempt_evidence(
                    trial_id=decision.input_binding.trial_id,
                    prompt=prompt_for(attempts[0].dimension_id),
                    attempt_ordinal=1,
                    status="failed",
                    reason_code="provider_retryable_failure",
                ),
                *attempts[1:],
            ],
        )
    ),
    "instrument_top_extra": semantic_error_reason(
        lambda: build_instrument_manifest(extra_config)
    ),
    "instrument_nested_extra": semantic_error_reason(
        lambda: build_instrument_manifest(nested_extra_config)
    ),
    "attempt_canary_authority": semantic_error_reason(
        lambda: assemble_semantic_assessment_run(
            admission=decision,
            dimension_attempt_evidence=[
                canary_tampered_attempt,
                *attempts[1:],
            ],
        )
    ),
    "parser_status": parser_projection.run.run_status,
    "parser_reasons": parser_projection.validation_report.reason_codes,
    "security": {
        "run_status": security_projection.run.run_status,
        "validation_status": security_projection.validation_report.validation_status,
        "reasons": security_projection.validation_report.reason_codes,
        "unit_count": len(security_projection.run.assessment_units),
        "finding_count": len(security_projection.run.findings),
        "handoff_count": len(security_projection.run.handoffs),
        "advice_count": len(security_composition.laj_advice_items),
        "event_types": [item.event_type for item in security_projection.events],
    },
    "provider_status": provider_projection.run.run_status,
    "provider_validation": provider_projection.validation_report.validation_status,
    "witness_relation": semantic_error_reason(
        lambda: compose_actual_laj(forged_witness)
    ),
    "composition_relation": semantic_error_reason(
        lambda: verify_composition_record(
            forged_composition,
            witness=assembled.witness,
        )
    ),
    "source_failure": {
        "profile": profile_source_failure,
        "component": component_source_failure,
        "prompt": prompt_source_failure,
    },
    "identity_failures": identity_failures,
}
wheel_root = Path(os.environ["SEMANTIC_EVALUATOR_WHEEL_ROOT"]).resolve()
module_files = [
    Path(inspect.getfile(module)).resolve()
    for module in (
        instrument_module,
        normalization_module,
        parser_module,
        profile_module,
        prompts_module,
        snapshot_module,
        unit_planner_module,
        validator_module,
        shadow_adapter_module,
        shadow_archive_module,
        shadow_contracts_module,
        shadow_runner_module,
        anthropic_adapter_module,
        local_proxy_adapter_module,
        study_module,
        study_contracts_module,
    )
]
payload = {
    "schema_ids": [model.schema_id for model in SEMANTIC_EVALUATOR_CONTRACT_MODELS],
    "schema_hashes": {
        model.schema_id: schema_sha256(model)
        for model in SEMANTIC_EVALUATOR_CONTRACT_MODELS
    },
    "shadow_schema_ids": [
        model.schema_id for model in SHADOW_CONTRACT_MODELS_V5
    ],
    "shadow_schema_hashes": {
        model.schema_id: canonical_sha256(model.model_json_schema())
        for model in SHADOW_CONTRACT_MODELS_V5
    },
    "study_schema_ids": [model.schema_id for model in STUDY_CONTRACT_MODELS],
    "study_schema_hashes": {
        model.schema_id: canonical_sha256(model.model_json_schema())
        for model in STUDY_CONTRACT_MODELS
    },
    "study_cli_actions": [
        build_parser().parse_args(argv).experiment_laj_action
        for argv in (
            ["experiments", "laj", "study-preflight", "--declaration", "d", "--report", "r", "--bounded-context", "c", "--instrument", "i", "--trial-id", "t", "--archive-root", "a", "--budget-policy", "b", "--output", "o"],
            ["experiments", "laj", "budgeted-shadow-run", "--authorization", "z", "--budget-policy", "b", "--report", "r", "--bounded-context", "c", "--instrument", "i", "--archive-root", "a", "--evidence-output", "e"],
            ["experiments", "laj", "study-compare", "--case", "c", "--execution-evidence", "e", "--archive", "a", "--output", "o"],
        )
    ],
    "shadow_runtime_identity": {
        "adapter_id": SYNTHETIC_ADAPTER_ID,
        "adapter_version": SYNTHETIC_ADAPTER_VERSION,
        "provider_id": SYNTHETIC_PROVIDER_ID,
        "fixture_identity": _load_fixture_manifest(),
        "archive_version": shadow_archive_module.ARCHIVE_VERSION,
        "runner_version": shadow_runner_module.RUNNER_VERSION,
    },
    "local_proxy_runtime_identity": {
        "adapter_id": CLIPROXY_ADAPTER_ID,
        "adapter_version": CLIPROXY_ADAPTER_VERSION,
        "provider_id": CLIPROXY_PROVIDER_ID,
        "base_url_sha256": canonical_sha256([CLIPROXY_BASE_URL]),
    },
    "anthropic_runtime_identity": {
        "adapter_id": ANTHROPIC_ADAPTER_ID,
        "adapter_version": ANTHROPIC_ADAPTER_VERSION,
        "provider_id": ANTHROPIC_PROVIDER_ID,
        "endpoint_setting": ANTHROPIC_ENDPOINT_SETTING,
        "api_key_setting": ANTHROPIC_API_KEY_SETTING,
        "sample_exact_model_id": "public-nonclaude-model-v1",
        "canonical_endpoints": [
            {
                "endpoint": endpoint,
                "sha256": sha256_bytes(
                    canonical_messages_endpoint_v1(endpoint).encode("ascii")
                ),
            }
            for endpoint in (
                "https://api.anthropic.com",
                "https://messages.example.test/v1",
            )
        ],
        "projection": {
            "status": project_anthropic_message_bytes_v1(
                synthetic_anthropic_message_bytes_v1(
                    stop_reason="end_turn",
                    response_id="msg-wheel-public",
                    model="public-nonclaude-model-v1",
                    content=[
                        {
                            "type": "thinking",
                            "thinking": "non-output",
                            "signature": "public-signature",
                        },
                        {"type": "text", "text": '{"findings":[]}'},
                    ],
                    input_tokens=3,
                    output_tokens=2,
                )
            ).status.state,
            "projection_output_hash": sha256_bytes(
                project_anthropic_message_bytes_v1(
                    synthetic_anthropic_message_bytes_v1(
                        stop_reason="end_turn",
                        response_id="msg-wheel-public",
                        model="public-nonclaude-model-v1",
                        content=[
                            {
                                "type": "thinking",
                                "thinking": "non-output",
                                "signature": "public-signature",
                            },
                            {"type": "text", "text": '{"findings":[]}'},
                        ],
                        input_tokens=3,
                        output_tokens=2,
                    )
                ).output.utf8_bytes
            ),
        },
    },
    "anthropic_archive_probe": anthropic_archive_probe(),
    "manifest": build_instrument_manifest(config).model_dump(mode="json"),
    "prompts": [
        {
            "dimension_id": item.dimension_id,
            "system_text": item.system_text,
            "user_text": item.user_text,
            "forbidden_canary_values": list(item.forbidden_canary_values),
            "request_sha256": item.request_sha256,
        }
        for item in decision.prompts
    ],
    "witness": assembled.witness.model_dump(mode="json"),
    "baseline": baseline.model_dump(mode="json"),
    "matched_composition": matched.model_dump(mode="json"),
    "actual_composition": actual.model_dump(mode="json"),
    "failure_results": failure_results,
    "resources": {
        "/".join(parts): resource_sha256(*parts)
        for parts in resource_paths
    },
    "loaded_from_extracted_wheel": all(
        str(path).startswith(str(wheel_root)) for path in module_files
    ),
}
print(canonical_json_text(payload))
"""


def _run_wheel_probe(
    *,
    cwd: Path,
    env: dict[str, str],
    optimized: bool,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable]
    if optimized:
        command.append("-O")
    command.append("-")
    probe = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=WHEEL_PROBE,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    return probe


def _source_identity() -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(REPO_ROOT)
    probe = _run_wheel_probe(
        cwd=REPO_ROOT,
        env=env,
        optimized=False,
    )
    return json.loads(probe.stdout.splitlines()[-1])


def _source_probe(*, optimized: bool) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(REPO_ROOT)
    probe = _run_wheel_probe(
        cwd=REPO_ROOT,
        env=env,
        optimized=optimized,
    )
    return probe.stdout.splitlines()[-1]


def test_se2r_14_wheel_probe_uses_exact_stdin_without_long_argv(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def run_probe(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, "{}\n", "")

    monkeypatch.setattr(subprocess, "run", run_probe)
    env = {"PYTHONPATH": "synthetic"}
    for optimized in (False, True):
        _run_wheel_probe(cwd=REPO_ROOT, env=env, optimized=optimized)

    assert [call["command"] for call in calls] == [
        [sys.executable, "-"],
        [sys.executable, "-O", "-"],
    ]
    for call in calls:
        command = call["command"]
        assert "-c" not in command
        assert WHEEL_PROBE not in command
        assert call["input"] == WHEEL_PROBE
        assert call["cwd"] == REPO_ROOT
        assert call["env"] is env
        assert call["check"] is False
        assert call["capture_output"] is True
        assert call["text"] is True


def test_se2r_14_source_probe_is_byte_identical_under_python_optimization() -> None:
    normal = _source_probe(optimized=False)
    assert normal == _source_probe(optimized=True)
    probe = json.loads(normal)["anthropic_archive_probe"]
    assert probe["real_provider_calls"] == 0
    assert probe["mocked_adapter_calls"] == {
        "http_captured_empty": 9,
        "http_unreadable_none": 9,
        "malformed_missing_id": 9,
        "parse_failure_invalid_utf8": 9,
        "refusal": 9,
        "retry_then_success": 18,
        "stop_sequence_surrogate": 9,
        "success": 9,
        "terminal_transport": 9,
        "truncation": 9,
    }
    assert probe["archive_results"]["success"]["ok"] is True
    assert probe["archive_results"]["retry_then_success"]["ok"] is True
    assert probe["archive_results"]["truncation"]["reason_codes"] == [
        "provider_incomplete"
    ]
    assert probe["archive_results"]["refusal"]["reason_codes"] == ["provider_refused"]
    assert probe["archive_results"]["terminal_transport"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["archive_results"]["malformed_missing_id"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["archive_results"]["parse_failure_invalid_utf8"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["archive_results"]["http_captured_empty"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["archive_results"]["http_unreadable_none"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["actual_invoke_boundary_calls"] == {
        "parse": 9,
        "provider": 9,
    }
    assert probe["http_actual_invoke_boundary_calls"] == {
        "captured_empty": {"parse": 0, "provider": 9},
        "unreadable_none": {"parse": 0, "provider": 9},
    }
    assert probe["http_captured_evidence"] == {
        "exact_empty_retained": True,
        "present_invalid": True,
        "response_count": 9,
        "sdk_body_invalid": True,
        "sentinel_absent": True,
    }
    assert probe["http_unreadable_evidence"] == {
        "absent_envelope_without_raw_hash": True,
        "empty_sha_absent": True,
        "response_count": 0,
        "sdk_body_invalid": True,
        "sentinel_absent": True,
    }
    assert probe["parse_failure_evidence"] == {
        "exact_raw_retained": True,
        "present_invalid": True,
        "response_count": 9,
        "sdk_body_invalid": True,
        "sentinel_absent": True,
    }
    assert probe["archive_results"]["stop_sequence_surrogate"]["reason_codes"] == [
        "provider_failed"
    ]
    assert probe["archive_replay_unchanged"] is True
    assert probe["replayed"] is True
    assert probe["malformed_replayed"] is True
    assert probe["malformed_replay_reason_codes"] == ["provider_failed"]
    assert probe["stop_sequence_replayed"] is True
    assert probe["stop_sequence_replay_reason_codes"] == ["provider_failed"]
    assert probe["parse_failure_replayed"] is True
    assert probe["parse_failure_replay_reason_codes"] == ["provider_failed"]
    assert probe["http_captured_replayed"] is True
    assert probe["http_captured_replay_reason_codes"] == ["provider_failed"]
    assert probe["http_unreadable_replayed"] is True
    assert probe["http_unreadable_replay_reason_codes"] == ["provider_failed"]
    assert probe["replay_metadata_calls"] == 0
    assert probe["receipt_id"] == probe["replay_receipt_id"]
    assert probe["sdk_status_tamper_reason_codes"] == ["shadow_archive_invalid"]
    assert probe["invalid_raw_sdk_tamper_reason_codes"] == ["shadow_archive_invalid"]
    assert probe["study_identity"]["count_semantics"] == (
        "conservative_utf8_byte_upper_bound"
    )
    assert probe["study_identity"]["endpoint_changes_authorization"] is True
    assert probe["study_identity"]["endpoint_changes_execution"] is True
    assert probe["study_identity"]["model_changes_authorization"] is True
    assert probe["study_identity"]["model_changes_execution"] is True
    assert probe["study_identity"]["raw_endpoint_absent"] is True


def test_se2r_14_wheel_contains_all_resources_and_matches_source_identity(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "README.md", build_root / "README.md")
    shutil.copytree(REPO_ROOT / "src", build_root / "src")
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build_python = os.environ.get(
        "SEMANTIC_EVALUATOR_BUILD_PYTHON",
        sys.executable,
    )
    build = subprocess.run(
        [
            build_python,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    extract_root = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert WHEEL_RESOURCE_NAMES <= names
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        wheel_metadata = archive.read(metadata_name).decode("utf-8")
        assert (
            'Requires-Dist: anthropic<0.105,>=0.104; extra == "semantic-evaluator-anthropic"'
            in wheel_metadata
        )
        archive.extractall(extract_root)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(extract_root)
    env["SEMANTIC_EVALUATOR_WHEEL_ROOT"] = str(extract_root)
    source_identity = _source_identity()
    for optimized in (False, True):
        probe = _run_wheel_probe(
            cwd=tmp_path,
            env=env,
            optimized=optimized,
        )
        wheel_identity = json.loads(probe.stdout.splitlines()[-1])
        assert wheel_identity == source_identity
