"""Runtime workspace kit install commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from multi_agent_brief.runtime_assets import (
    RuntimeAssetInstallError,
    apply_runtime_kit_plan,
    install_runtime_kit,
    plan_runtime_kit,
    plan_protected_codex_observations,
    preflight_runtime_kit_plans,
)
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError


def register(subparsers: argparse._SubParsersAction) -> None:
    runtime_parser = subparsers.add_parser(
        "runtime",
        help="Install runtime-discoverable workspace assets.",
    )
    actions = runtime_parser.add_subparsers(dest="runtime_action", required=True)

    install = actions.add_parser(
        "install",
        help="Install the packaged Codex runtime kit into a workspace.",
    )
    install.add_argument(
        "--workspace",
        required=True,
        help="MABW workspace directory.",
    )
    install.add_argument(
        "--runtime",
        required=True,
        choices=("codex", "dsh", "all"),
        help="Runtime kit to install.",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-MABW runtime kit files.",
    )
    install.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned writes without changing files.",
    )
    for action in (
        "next",
        "diagnose",
        "invocation-start",
        "invocation-validate",
        "invocation-accept",
        "invocation-fail",
        "apply",
        "continue",
    ):
        command = actions.add_parser(
            action,
            help=f"ControlStore v2 runtime {action}.",
        )
        command.add_argument("--workspace", required=True)
        if action == "invocation-start":
            command.add_argument("--action")
        if action == "apply":
            command.add_argument("--action", required=True)
        if action in {"invocation-validate", "invocation-accept", "invocation-fail"}:
            command.add_argument("--envelope", required=True)
        if action == "apply":
            command.add_argument("--human-request")
            command.add_argument("--action-input")
        if action == "invocation-fail":
            command.add_argument(
                "--reason",
                required=True,
                choices=(
                    "dispatch_unavailable",
                    "child_failed",
                    "child_timed_out",
                    "session_interrupted",
                    "proposal_missing",
                    "proposal_invalid",
                ),
            )
        if action == "continue":
            command.add_argument(
                "--trace",
                action="store_true",
                help="Include the read-only Store action trace.",
            )

    successor = actions.add_parser(
        "successor-start",
        help="Start a normal same-workspace successor run.",
    )
    successor.add_argument("--workspace", required=True)
    successor.add_argument("--direction-json", required=True)
    successor.add_argument("--run-id", required=True)
    successor.add_argument(
        "--include-approved-guidance",
        action="store_true",
        help="Freeze compatible active Human-approved guidance for this successor.",
    )


def handle(args: argparse.Namespace) -> int:
    if args.runtime_action == "install":
        try:
            dry_run = bool(getattr(args, "dry_run", False))
            if args.runtime == "codex":
                from multi_agent_brief.runtime_host_v2.initialization import (
                    WorkspaceBootstrap,
                )

                result = WorkspaceBootstrap(args.workspace).install_codex_kit(
                    dry_run=dry_run
                )
            elif args.runtime == "dsh":
                from multi_agent_brief.runtime_host_v2.initialization import (
                    WorkspaceBootstrap,
                )

                result = WorkspaceBootstrap(args.workspace).install_dsh_kit(
                    dry_run=dry_run
                )
            elif args.runtime == "all":
                from multi_agent_brief.runtime_host_v2.initialization import (
                    WorkspaceBootstrap,
                )

                bootstrap = WorkspaceBootstrap(args.workspace)
                force = bool(getattr(args, "force", False))
                codex_preflight = bootstrap.install_codex_kit(dry_run=True)
                protected_observations = plan_protected_codex_observations(
                    workspace=args.workspace
                )
                retained_plan = preflight_runtime_kit_plans(
                    plans=tuple(
                        plan_runtime_kit(
                            workspace=args.workspace,
                            runtime=runtime,
                            repo_workdir=getattr(args, "repo_workdir", None),
                        )
                        for runtime in ("dsh",)
                    ),
                    force=force,
                    runtime="all",
                    protected_observations=protected_observations,
                )
                codex_result = (
                    codex_preflight
                    if dry_run
                    else bootstrap.install_codex_kit(dry_run=False)
                )
                retained_result = apply_runtime_kit_plan(
                    retained_plan,
                    force=force,
                    dry_run=dry_run,
                )
                results = [
                    codex_result,
                    retained_result,
                ]
                written = list(
                    dict.fromkeys(path for item in results for path in item["written"])
                )
                result = {
                    "runtime": "all",
                    "workspace": str(Path(args.workspace).expanduser().resolve()),
                    "repo_workdir": getattr(args, "repo_workdir", None),
                    "dry_run": dry_run,
                    "written": written,
                    "count": len(written),
                    "phase": "planned" if dry_run else "prepared",
                }
            else:
                result = install_runtime_kit(
                    workspace=args.workspace,
                    runtime=args.runtime,
                    repo_workdir=getattr(args, "repo_workdir", None),
                    force=bool(getattr(args, "force", False)),
                    dry_run=dry_run,
                )
        except (RuntimeAssetInstallError, RuntimeHostError) as exc:
            print(f"[runtime install] {exc}")
            return 1
        phase = result.get("phase")
        if phase == "verified":
            verb, status = "verified", "Verified"
        elif result["dry_run"]:
            verb, status = "would write", "Planned"
        else:
            verb, status = "wrote", "Installed"
        for path in result["written"]:
            print(f"[runtime install] {verb} {path}")
        print(
            f"[runtime install] {status} workspace runtime kit "
            f"for {result['runtime']} ({result['count']} files)."
        )
        if result["runtime"] in {"codex", "all"}:
            print(
                "[runtime install] Codex note: open and trust this workspace in Codex "
                "so project .codex/config.toml and custom agents are loaded."
            )
        if result["runtime"] in {"dsh", "all"}:
            print(
                "[runtime install] DSH note: copy the .dsh/presets/briefloop-<role> "
                "directories into your DSH preset root "
                "(${DSH_HOME:-$HOME/.dsh}/.agent-presets/) so the roster can mount them."
            )
        return 0
    if args.runtime_action == "successor-start":
        from multi_agent_brief.contracts.v2 import RunDirection
        from multi_agent_brief.runtime_host_v2.codex import (
            workspace_codex_adapter_loader,
        )
        from multi_agent_brief.runtime_host_v2.service import RuntimeHostService

        try:
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
            raw = json.loads(args.direction_json)
            if type(raw) is not dict:
                raise RuntimeHostError("runtime_successor_request_invalid")
            direction = RunDirection.model_validate(raw, strict=True)
            result = RuntimeHostService(
                workspace,
                adapter_loader=workspace_codex_adapter_loader(workspace),
            ).start_successor(
                successor_run_id=args.run_id,
                run_direction=direction,
                include_approved_guidance=bool(args.include_approved_guidance),
            )
        except (
            json.JSONDecodeError,
            OSError,
            RuntimeHostError,
            TypeError,
            ValidationError,
            ValueError,
        ) as exc:
            code = (
                str(exc)
                if isinstance(exc, RuntimeHostError)
                else "runtime_successor_request_invalid"
            )
            print(f"[runtime successor-start] {code}")
            return 1
        print(
            json.dumps(
                result.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if args.runtime_action in {
        "next",
        "diagnose",
        "invocation-start",
        "invocation-validate",
        "invocation-accept",
        "invocation-fail",
        "apply",
        "continue",
    }:
        from multi_agent_brief.runtime_host_v2.codex import (
            workspace_codex_adapter_loader,
        )
        from multi_agent_brief.runtime_host_v2.service import RuntimeHostService
        from multi_agent_brief.runtime_host_v2.scratch import read_host_contract
        from multi_agent_brief.runtime_host_v2.contracts import (
            HumanSourceMaterialRequest,
            HumanSourcePackRequest,
            RuntimeSourceAcquisitionRecoveryRequest,
            RepairContentInput,
            RoleTaskEnvelope,
        )
        from multi_agent_brief.contracts.v2 import (
            CoreRunNextAction,
            DeliveryAuthorizationRequest,
            InternalApprovalRequest,
            RunTerminationRequest,
        )

        try:
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
            service = RuntimeHostService(
                workspace,
                adapter_loader=workspace_codex_adapter_loader(workspace),
            )
            if args.runtime_action == "next":
                payload = service.next_action().model_dump(
                    mode="json", exclude_unset=False
                )
            elif args.runtime_action == "continue":
                payload = service.continue_authorized().model_dump(
                    mode="json", exclude_unset=False
                )
                if not bool(getattr(args, "trace", False)):
                    payload.pop("trace", None)
            elif args.runtime_action == "diagnose":
                payload = service.diagnose().model_dump(
                    mode="json", exclude_unset=False
                )
            elif args.runtime_action == "invocation-start":
                action = (
                    read_host_contract(
                        workspace,
                        args.action,
                        CoreRunNextAction,
                        error_code="runtime_action_invalid",
                    )
                    if args.action is not None
                    else None
                )
                dispatch = service.start_current_invocation(action)
                payload = dispatch.envelope.model_dump(mode="json", exclude_unset=False)
            elif args.runtime_action == "invocation-validate":
                envelope = read_host_contract(
                    workspace,
                    args.envelope,
                    RoleTaskEnvelope,
                    error_code="runtime_envelope_invalid",
                )
                payload = service.validate_invocation(
                    envelope.invocation_id,
                    expected_envelope=envelope,
                ).model_dump(mode="json", exclude_unset=False)
            elif args.runtime_action == "invocation-accept":
                envelope = read_host_contract(
                    workspace,
                    args.envelope,
                    RoleTaskEnvelope,
                    error_code="runtime_envelope_invalid",
                )
                payload = service.accept_invocation(
                    envelope.invocation_id,
                    expected_envelope=envelope,
                ).model_dump(mode="json", exclude_unset=False)
            elif args.runtime_action == "invocation-fail":
                envelope = read_host_contract(
                    workspace,
                    args.envelope,
                    RoleTaskEnvelope,
                    error_code="runtime_envelope_invalid",
                )
                payload = service.fail_invocation(
                    envelope.invocation_id,
                    reason_code=args.reason,
                    expected_envelope=envelope,
                ).model_dump(mode="json", exclude_unset=False)
            else:
                action = read_host_contract(
                    workspace,
                    args.action,
                    CoreRunNextAction,
                    error_code="runtime_action_invalid",
                )
                human_request = None
                action_input = None
                if action.action_kind == "human_decision":
                    request_models = {
                        HumanSourcePackRequest.schema_id: HumanSourcePackRequest,
                        RuntimeSourceAcquisitionRecoveryRequest.schema_id: (
                            RuntimeSourceAcquisitionRecoveryRequest
                        ),
                        HumanSourceMaterialRequest.schema_id: (
                            HumanSourceMaterialRequest
                        ),
                        InternalApprovalRequest.schema_id: InternalApprovalRequest,
                        DeliveryAuthorizationRequest.schema_id: (
                            DeliveryAuthorizationRequest
                        ),
                        RunTerminationRequest.schema_id: RunTerminationRequest,
                    }
                    request_model = request_models.get(action.request_schema_id)
                    if args.human_request is None or request_model is None:
                        raise RuntimeHostError("runtime_human_request_required")
                    human_request = read_host_contract(
                        workspace,
                        args.human_request,
                        request_model,
                        error_code="runtime_human_request_invalid",
                    )
                    if args.action_input is not None:
                        raise RuntimeHostError("runtime_action_input_invalid")
                elif args.human_request is not None:
                    raise RuntimeHostError("runtime_human_request_invalid")
                elif action.effect_kind == "artifact_supersede":
                    if args.action_input is None:
                        raise RuntimeHostError("runtime_action_input_required")
                    action_input = read_host_contract(
                        workspace,
                        args.action_input,
                        RepairContentInput,
                        error_code="runtime_action_input_invalid",
                    )
                elif args.action_input is not None:
                    raise RuntimeHostError("runtime_action_input_invalid")
                applied = service.apply_current(
                    action,
                    human_request,
                    action_input,
                )
                payload = (
                    applied.model_dump(mode="json", exclude_unset=False)
                    if hasattr(applied, "model_dump")
                    else applied.to_dict()
                )
        except (OSError, RuntimeHostError) as exc:
            print(f"[runtime {args.runtime_action}] {exc}")
            return 1
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        if (
            args.runtime_action == "invocation-validate"
            and payload.get("status") != "valid"
        ):
            return 1
        return 0
    return 1
