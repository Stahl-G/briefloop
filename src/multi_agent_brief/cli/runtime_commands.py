"""Runtime workspace kit install commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from multi_agent_brief.runtime_assets import (
    RuntimeAssetInstallError,
    install_runtime_kit,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    runtime_parser = subparsers.add_parser(
        "runtime",
        help="Install runtime-discoverable workspace assets.",
    )
    actions = runtime_parser.add_subparsers(dest="runtime_action", required=True)

    install = actions.add_parser(
        "install",
        help="Install OpenCode/Claude Code/Codex runtime kit files into a workspace.",
    )
    install.add_argument(
        "--workspace",
        required=True,
        help="MABW workspace directory.",
    )
    install.add_argument(
        "--runtime",
        required=True,
        choices=("opencode", "claude", "codex", "all"),
        help="Runtime kit to install.",
    )
    install.add_argument(
        "--repo-workdir",
        help=(
            "MABW source repository root. Required when the package install "
            "cannot discover source-clone runtime assets."
        ),
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
        "invocation-accept",
        "apply",
    ):
        command = actions.add_parser(
            action,
            help=f"ControlStore v2 runtime {action}.",
        )
        command.add_argument("--workspace", required=True)
        if action == "invocation-accept":
            command.add_argument("--invocation-id", required=True)


def handle(args: argparse.Namespace) -> int:
    if args.runtime_action == "install":
        try:
            result = install_runtime_kit(
                workspace=args.workspace,
                runtime=args.runtime,
                repo_workdir=getattr(args, "repo_workdir", None),
                force=bool(getattr(args, "force", False)),
                dry_run=bool(getattr(args, "dry_run", False)),
            )
        except RuntimeAssetInstallError as exc:
            print(f"[runtime install] {exc}")
            return 1
        verb = "would write" if result["dry_run"] else "wrote"
        for path in result["written"]:
            print(f"[runtime install] {verb} {path}")
        status = "Planned" if result["dry_run"] else "Installed"
        print(
            f"[runtime install] {status} workspace runtime kit "
            f"for {result['runtime']} ({result['count']} files)."
        )
        if result["runtime"] in {"codex", "all"}:
            print(
                "[runtime install] Codex note: open and trust this workspace in Codex "
                "so project .codex/config.toml and custom agents are loaded."
            )
        return 0
    if args.runtime_action in {
        "next",
        "diagnose",
        "invocation-start",
        "invocation-accept",
        "apply",
    }:
        from multi_agent_brief.runtime_host_v2.codex import (
            load_codex_adapter_binding,
        )
        from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
        from multi_agent_brief.runtime_host_v2.service import RuntimeHostService

        try:
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
            service = RuntimeHostService(
                workspace,
                adapter_loader=load_codex_adapter_binding,
            )
            if args.runtime_action == "next":
                payload = service.next_action().model_dump(
                    mode="json", exclude_unset=False
                )
            elif args.runtime_action == "diagnose":
                payload = service.diagnose().model_dump(
                    mode="json", exclude_unset=False
                )
            elif args.runtime_action == "invocation-start":
                dispatch = service.start_current_invocation()
                payload = dispatch.envelope.model_dump(mode="json", exclude_unset=False)
            elif args.runtime_action == "invocation-accept":
                payload = service.accept_invocation(args.invocation_id).model_dump(
                    mode="json", exclude_unset=False
                )
            else:
                payload = service.apply_current().to_dict()
        except (OSError, RuntimeHostError) as exc:
            print(f"[runtime {args.runtime_action}] {exc}")
            return 1
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    return 1
