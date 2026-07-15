"""Hidden fresh-v2 core-run harness; not wired to active runtimes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from multi_agent_brief.contracts.v2 import (
    AuditPromotionRequest,
    ClaimFreezeRequest,
    CoreRunInitializeRequest,
    GateCheckRequest,
    IntegrityCheckRequest,
    InvocationStartRequest,
    OwnedArtifactSubmitRequest,
    StageCompleteRequest,
    StrictModel,
)
from multi_agent_brief.core_run_v2 import (
    ArtifactAcceptanceService,
    ClaimFreezeService,
    CoreRunError,
    CoreRunResult,
    CoreRunService,
    GateEvaluationService,
    RunIntegrityService,
)
from multi_agent_brief.intake_v2.errors import IntakeError
from multi_agent_brief.intake_v2.scratch import ScratchReader, parse_json_object
from multi_agent_brief.core_run_v2.service import workspace_input_fingerprints


_RequestT = TypeVar("_RequestT", bound=StrictModel)
_ACTIONS = (
    "initialize",
    "doctor-check",
    "invocation-start",
    "artifact-submit",
    "claim-freeze",
    "audit-promote",
    "gate-check",
    "stage-complete",
    "integrity-check",
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "core-v2",
        help=argparse.SUPPRESS,
        description="Internal fresh-v2 core run harness.",
    )
    actions = parser.add_subparsers(dest="core_v2_action", required=True)
    for action in _ACTIONS:
        command = actions.add_parser(action, help=argparse.SUPPRESS)
        command.add_argument("--workspace", required=True)
        command.add_argument("--request", required=True)
        command.add_argument(
            "--json",
            action="store_true",
            required=True,
            help="Emit exactly one machine-readable result object.",
        )


def handle(args: argparse.Namespace) -> int:
    try:
        result = _handle(args)
    except CoreRunError as exc:
        result = CoreRunResult(
            status="failed_uncommitted",
            error_code=exc.code,
        )
    print(json.dumps(_result_payload(result), sort_keys=True, separators=(",", ":")))
    return _result_exit_code(result)


def _handle(args: argparse.Namespace) -> CoreRunResult | dict[str, object]:
    workspace = _workspace(args.workspace)
    action = args.core_v2_action
    if action == "initialize":
        payload = _read_request_payload(workspace, args.request)
        config_sha256, sources_sha256 = workspace_input_fingerprints(workspace)
        payload["workspace_config_sha256"] = config_sha256
        payload["sources_config_sha256"] = sources_sha256
        request = _validate(CoreRunInitializeRequest, payload)
        return CoreRunService(workspace).initialize(request)
    if action in {"doctor-check", "integrity-check"}:
        request = _read_request(
            workspace,
            args.request,
            IntegrityCheckRequest,
        )
        if action == "doctor-check":
            return CoreRunService(workspace).doctor_check(request)
        try:
            return RunIntegrityService(workspace).inspect(request)
        except CoreRunError:
            raise
    if action == "invocation-start":
        request = _read_request(workspace, args.request, InvocationStartRequest)
        return CoreRunService(workspace).start_invocation(request)
    if action == "artifact-submit":
        request = _read_request(workspace, args.request, OwnedArtifactSubmitRequest)
        return ArtifactAcceptanceService(workspace).submit_owned_artifact(request)
    if action == "claim-freeze":
        request = _read_request(workspace, args.request, ClaimFreezeRequest)
        return ClaimFreezeService(workspace).freeze(request)
    if action == "audit-promote":
        request = _read_request(workspace, args.request, AuditPromotionRequest)
        return ArtifactAcceptanceService(workspace).promote_audit_proposal(request)
    if action == "gate-check":
        request = _read_request(workspace, args.request, GateCheckRequest)
        return GateEvaluationService(workspace).evaluate(request)
    if action == "stage-complete":
        request = _read_request(workspace, args.request, StageCompleteRequest)
        return CoreRunService(workspace).complete_stage(request)
    raise CoreRunError("core_run_request_invalid")


def _read_request(
    workspace: Path,
    request_path: str,
    model_type: type[_RequestT],
) -> _RequestT:
    return _validate(model_type, _read_request_payload(workspace, request_path))


def _read_request_payload(workspace: Path, request_path: str) -> dict[str, object]:
    try:
        reader = ScratchReader(workspace)
        return parse_json_object(reader.read_request(request_path))
    except IntakeError as exc:
        raise CoreRunError("core_run_request_invalid") from exc


def _validate(model_type: type[_RequestT], payload: dict[str, object]) -> _RequestT:
    try:
        return model_type.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise CoreRunError("core_run_request_invalid") from exc


def _workspace(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError
        return path
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreRunError("core_run_request_invalid") from exc


def _result_payload(result: CoreRunResult | dict[str, object]) -> dict[str, object]:
    return result.to_dict() if isinstance(result, CoreRunResult) else result


def _result_exit_code(result: CoreRunResult | dict[str, object]) -> int:
    if isinstance(result, CoreRunResult):
        return result.exit_code
    return 0 if result.get("status") == "clean" else 1


__all__ = ["handle", "register"]
