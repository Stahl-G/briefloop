"""Product-layer ReportPack and ReportSpec CLI surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecLoadError,
    load_report_spec,
    validate_report_spec_payload,
)


def register_packs(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "packs",
        help="List and inspect experimental ReportPack contracts.",
    )
    actions = parser.add_subparsers(dest="packs_action", required=True)

    list_parser = actions.add_parser("list", help="List packaged ReportPacks.")
    list_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser("show", help="Show a packaged ReportPack.")
    show_parser.add_argument("pack_id", help="ReportPack id, for example market_weekly.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_validate_report_spec(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-report-spec",
        help="Validate an experimental ReportSpec YAML file.",
    )
    parser.add_argument("report_spec", help="Path to report_spec.yaml.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle_packs(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    if args.packs_action == "list":
        payload = registry.to_list_payload()
        _print_payload("packs list", payload, as_json=getattr(args, "json", False))
        return 0 if payload["ok"] else 1

    if args.packs_action == "show":
        pack = registry.get(args.pack_id)
        if pack is None:
            payload = {
                "ok": False,
                "error": f"unknown report pack: {args.pack_id}",
                "available_packs": sorted(registry.pack_ids()),
            }
            _print_payload("packs show", payload, as_json=getattr(args, "json", False))
            return 1
        payload = {
            "ok": True,
            "pack": dict(pack.payload),
            "source": "packaged_report_pack",
        }
        _print_payload("packs show", payload, as_json=getattr(args, "json", False))
        return 0

    return 1


def handle_validate_report_spec(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    path = Path(args.report_spec)
    try:
        payload = load_report_spec(path)
    except OSError as exc:
        result = {"ok": False, "errors": [{"field": str(path), "error": str(exc), "severity": "error"}]}
        _print_payload("validate-report-spec", result, as_json=getattr(args, "json", False))
        return 1
    except ReportSpecLoadError as exc:
        result = {
            "ok": False,
            "errors": [{"field": str(exc.path), "error": exc.message, "severity": "error"}],
        }
        _print_payload("validate-report-spec", result, as_json=getattr(args, "json", False))
        return 1

    validation = validate_report_spec_payload(
        payload,
        known_report_packs=registry.pack_ids(),
        report_type_by_pack=registry.report_type_by_pack(),
    )
    result = validation.to_dict()
    result["path"] = str(path)
    _print_payload("validate-report-spec", result, as_json=getattr(args, "json", False))
    return 0 if validation.ok else 1


def _print_payload(label: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print(f"[{label}] ok: {payload.get('ok')}")
    if label == "packs list":
        for item in payload.get("packs", []):
            print(f"- {item.get('pack_id')}: {item.get('display_name')} ({item.get('status')})")
        for error in payload.get("errors", []):
            print(f"[error] {error.get('field')}: {error.get('error')}")
    elif label == "packs show":
        if payload.get("ok"):
            pack = payload.get("pack", {})
            print(f"pack_id: {pack.get('pack_id')}")
            print(f"report_type: {pack.get('report_type')}")
            print(f"status: {pack.get('status')}")
            print("boundary: experimental product-layer contract only")
        else:
            print(payload.get("error"))
    else:
        print(f"report_pack: {payload.get('report_pack')}")
        print(f"report_type: {payload.get('report_type')}")
        for error in payload.get("errors", []):
            print(f"[error] {error.get('field')}: {error.get('error')}")
        for warning in payload.get("warnings", []):
            print(f"[warning] {warning.get('field')}: {warning.get('error')}")
