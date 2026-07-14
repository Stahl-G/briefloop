"""Product-layer ReportPack and ReportSpec CLI surfaces."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.schemas.evidence_span_registry import EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION
from multi_agent_brief.contracts.source_metadata import normalize_source_category
from multi_agent_brief.inputs.contracts import extracted_markdown_path
from multi_agent_brief.orchestrator_contract import RUNTIME_CLI_CHOICE_PLACEHOLDER
from multi_agent_brief.product.bundle_projection import (
    ReportBundleProjectionError,
    write_report_bundle_manifest,
)
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.policy_resolver import PolicyProfileResolution, resolve_policy_profile
from multi_agent_brief.product.quality_closeout import (
    QUALITY_PANEL_CLOSEOUT_COMMAND,
    QualityPanelCloseoutError,
    display_quality_panel_closeout,
    materialize_quality_panel_closeout,
)
from multi_agent_brief.product.report_pack_aliases import (
    RECOMMENDED_REPORT_PACK_ENTRIES,
    aliases_for_report_pack,
    recommended_entries_for_pack_ids,
    resolve_report_pack_id,
)
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecLoadError,
    load_report_spec,
    validate_report_spec_payload,
)
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

SPECIALIZED_REPORT_PACK_POLICY_PROFILES = {
    "evidence_extract": "evidence_extract_default",
    "solar_industry_periodic": "solar_manufacturing_default",
}
EVIDENCE_EXTRACT_BINARY_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg"}
EVIDENCE_EXTRACT_TEXT_EXTENSIONS = {".md", ".txt", ".json"}
EVIDENCE_EXTRACT_SPAN_EXCERPT_LIMIT = 1200
EVIDENCE_EXTRACT_SOURCE_LOCK_SCHEMA_VERSION = "briefloop.evidence_extract_source_lock.v1"
EVIDENCE_EXTRACT_PAGE_INVENTORY_SCHEMA_VERSION = "briefloop.evidence_extract_page_inventory.v1"
PRODUCT_WORKSPACE_SELECTOR_MAX_ITEMS = 20


def register_new_workspace(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "new",
        help="Create a conservative zero-config workspace from a ReportPack.",
    )
    parser.add_argument("report_pack", help="Product entry or ReportPack id, for example industry-weekly.")
    parser.add_argument("workspace", help="Target workspace directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing init files.")
    parser.add_argument(
        "--company",
        default="Your Organization",
        help="Organization name placeholder.",
    )
    parser.add_argument(
        "--industry",
        help=(
            "Industry or theme hint for deterministic PolicyProfile resolution. "
            "Low-confidence or ambiguous matches use the ReportPack default."
        ),
    )
    parser.add_argument(
        "--policy-profile",
        help="Explicit PolicyProfile override, for example finance_default.",
    )
    parser.add_argument("--title", help="Brief title. Defaults to the pack title.")
    parser.add_argument(
        "--audience",
        help="Target reader label. Defaults to the pack audience label.",
    )
    parser.add_argument(
        "--language",
        choices=["en-US", "zh-CN", "bilingual"],
        help="Brief language. Defaults to the pack audience language.",
    )
    parser.add_argument(
        "--web-search-mode",
        choices=["disabled", "runtime_tool", "external_api", "configure_later"],
        help="How web search is provided. Defaults to configure_later.",
    )
    parser.add_argument(
        "--search-backend",
        choices=["tavily", "exa", "brave", "firecrawl", "serper"],
        help="Opt into an external API search backend, for example tavily.",
    )


def register_packs(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "packs",
        help="List and inspect supported and experimental ReportPack contracts.",
    )
    actions = parser.add_subparsers(dest="packs_action", required=True)

    list_parser = actions.add_parser("list", help="List packaged ReportPacks.")
    list_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    show_parser = actions.add_parser("show", help="Show a packaged ReportPack.")
    show_parser.add_argument("pack_id", help="Product entry or ReportPack id, for example industry-weekly.")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    templates_parser = actions.add_parser(
        "templates",
        help="List packaged ReportTemplate contracts.",
    )
    templates_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    bundle_parser = actions.add_parser(
        "bundle",
        help="Write a delivery/audit bundle projection for a finalized workspace.",
    )
    bundle_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    bundle_parser.add_argument(
        "--output",
        help="Manifest output path. Defaults to <workspace>/output/report_bundle_manifest.json.",
    )
    bundle_parser.add_argument(
        "--write-archives",
        action="store_true",
        help="Write clean delivery_bundle.zip and audit_bundle.zip from the manifest artifacts.",
    )
    bundle_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_validate_report_spec(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-report-spec",
        help="Validate a ReportSpec YAML file.",
    )
    parser.add_argument("report_spec", help="Path to report_spec.yaml.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_extract(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "extract",
        help="Register an explicit evidence-extract scope and local source files.",
    )
    parser.add_argument("--workspace", required=True, help="Path to an evidence_extract workspace.")
    parser.add_argument("--scope", required=True, help="Explicit extraction scope.")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Local source file path. May be repeated.",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=[],
        help="One or more local source file paths or shell-style globs.",
    )
    parser.add_argument(
        "--source-category",
        default="other",
        help="Reader-facing source_category to write into manual source entries. Defaults to other.",
    )
    parser.add_argument("--language", default="en", help="Source language hint for manual source entries.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace previously registered evidence-extract source copies.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_quality(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "quality",
        help="Write Quality Panel projection artifacts.",
    )
    actions = parser.add_subparsers(dest="quality_action", required=True)

    summarize_parser = actions.add_parser(
        "summarize",
        help="Write Quality Panel JSON, Markdown, and static HTML projection artifacts.",
    )
    summarize_parser.add_argument("--workspace", required=True, help="Path to workspace directory.")
    summarize_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle_new_workspace(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    requested_pack_id = resolve_report_pack_id(args.report_pack)
    pack = registry.get(requested_pack_id)
    if pack is None:
        payload = {
            "ok": False,
            "error": f"unknown report pack: {args.report_pack}",
            **_report_pack_entrypoint_payload(registry),
        }
        _print_payload("new", payload, as_json=False)
        return 1

    target = Path(args.workspace)
    try:
        creation = _create_report_pack_workspace(target=target, pack=pack, args=args)
    except (FileExistsError, OSError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "workspace": str(target),
            "report_pack": pack.pack_id,
        }
        _print_payload("new", payload, as_json=False)
        return 1

    payload = {
        "ok": True,
        "workspace": str(target),
        "report_pack": pack.pack_id,
        "report_spec": str(target / "report_spec.yaml"),
        "policy_profile": creation.get("policy_profile"),
        "policy_profile_resolution": creation.get("policy_profile_resolution"),
        "web_search_mode": creation.get("web_search_mode"),
        "search_backend": creation.get("search_backend"),
        "search_api_key_env": creation.get("search_api_key_env"),
        "boundary": "zero_config_workspace_skeleton_only",
    }
    _print_payload("new", payload, as_json=False)
    return 0


def handle_packs(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    if args.packs_action == "list":
        payload = _with_report_pack_aliases(registry.to_list_payload())
        _print_payload("packs list", payload, as_json=getattr(args, "json", False))
        return 0 if payload["ok"] else 1

    if args.packs_action == "show":
        requested_pack_id = resolve_report_pack_id(args.pack_id)
        pack = registry.get(requested_pack_id)
        if pack is None:
            payload = {
                "ok": False,
                "error": f"unknown report pack: {args.pack_id}",
                **_report_pack_entrypoint_payload(registry),
            }
            _print_payload("packs show", payload, as_json=getattr(args, "json", False))
            return 1
        payload = {
            "ok": True,
            "pack": dict(pack.payload),
            "aliases": aliases_for_report_pack(pack.pack_id),
            "recommended_entry": RECOMMENDED_REPORT_PACK_ENTRIES.get(pack.pack_id),
            "source": "packaged_report_pack",
        }
        _print_payload("packs show", payload, as_json=getattr(args, "json", False))
        return 0

    if args.packs_action == "templates":
        template_registry = ReportTemplateRegistry.from_package()
        payload = template_registry.to_list_payload()
        _print_payload("packs templates", payload, as_json=getattr(args, "json", False))
        return 0 if payload["ok"] else 1

    if args.packs_action == "bundle":
        try:
            payload = write_report_bundle_manifest(
                workspace=getattr(args, "workspace"),
                output_path=getattr(args, "output", None),
                write_archives=getattr(args, "write_archives", False),
            )
        except ReportBundleProjectionError as exc:
            payload = {
                "ok": False,
                "error": str(exc),
                "workspace": str(getattr(args, "workspace")),
            }
            _print_payload("packs bundle", payload, as_json=getattr(args, "json", False))
            return 1
        payload["ok"] = True
        _print_payload("packs bundle", payload, as_json=getattr(args, "json", False))
        return 0

    return 1


def handle_validate_report_spec(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    policy_registry = PolicyProfileRegistry.from_package()
    path = Path(args.report_spec)
    try:
        payload = load_report_spec(path)
    except OSError as exc:
        result = {
            "ok": False,
            "errors": [{"field": str(path), "error": str(exc), "severity": "error"}],
        }
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
        known_policy_profiles=policy_registry.profile_ids(),
        default_policy_profile_by_pack=registry.default_policy_profile_by_pack(),
    )
    result = validation.to_dict()
    result["path"] = str(path)
    _print_payload("validate-report-spec", result, as_json=getattr(args, "json", False))
    return 0 if validation.ok else 1


def handle_extract(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    try:
        payload = _register_evidence_extract_scope(workspace=workspace, args=args)
    except (OSError, ReportSpecLoadError, ValueError, yaml.YAMLError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "workspace": str(workspace),
            "boundary": "evidence_extract_scope_source_and_text_span_registration_only",
        }
        _print_payload("extract", payload, as_json=getattr(args, "json", False))
        return 1

    _print_payload("extract", payload, as_json=getattr(args, "json", False))
    return 0


def handle_quality(args: argparse.Namespace) -> int:
    action = getattr(args, "quality_action", "")
    if action != "summarize":
        return 1

    workspace = Path(args.workspace).expanduser().resolve()
    as_json = bool(getattr(args, "json", False))
    try:
        _require_existing_briefloop_workspace(workspace)
        materialization = materialize_quality_panel_closeout(
            workspace=workspace,
            actor="cli",
        )
    except (QualityPanelCloseoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "reason_code": getattr(exc, "reason_code", "quality_projection_generation_failed"),
            "details": getattr(exc, "details", {}),
            "workspace": str(workspace),
            "repair_command": QUALITY_PANEL_CLOSEOUT_COMMAND,
            "boundary": "quality_projection_only_not_gate_or_release_authority",
        }
        _print_payload("quality summarize", payload, as_json=as_json)
        return 1

    artifacts = materialization["artifacts"]
    browser_display = display_quality_panel_closeout(
        materialization,
        as_json=as_json,
        is_interactive=_stdout_is_interactive(),
    )
    payload = {
        "ok": True,
        "workspace": str(workspace),
        "quality_panel": artifacts["quality_panel"]["path"],
        "quality_panel_sha256": artifacts["quality_panel"]["sha256"],
        "quality_summary": artifacts["quality_summary"]["path"],
        "quality_summary_sha256": artifacts["quality_summary"]["sha256"],
        "quality_panel_html": artifacts["quality_panel_html"]["path"],
        "quality_panel_html_sha256": artifacts["quality_panel_html"]["sha256"],
        "registry_refresh": materialization["registry_refresh"],
        "browser_display": browser_display,
        "overall_status": materialization.get("overall_status"),
        "recommended_actions": materialization.get("recommended_actions", []),
        "repair_command": materialization["repair_command"],
        "boundary": "quality_projection_only_not_gate_or_release_authority",
        "non_claims": [
            "not_a_quality_score",
            "not_a_gate_report_replacement",
            "not_release_authorization",
            "not_delivery_approval",
            "not_semantic_truth_proof",
            "not_repair_execution",
        ],
    }
    _print_payload("quality summarize", payload, as_json=as_json)
    return 0


def _require_existing_briefloop_workspace(workspace: Path) -> None:
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"workspace does not exist: {workspace}")
    if not (workspace / "config.yaml").exists() and not (
        workspace / "output" / "intermediate" / "runtime_manifest.json"
    ).exists():
        raise ValueError(f"not a BriefLoop workspace: {workspace}")


def _stdout_is_interactive() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, OSError):
        return False


def _print_payload(label: str, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print(f"[{label}] ok: {payload.get('ok')}")
    if label == "packs list":
        for item in payload.get("packs", []):
            entry = item.get("recommended_entry") or item.get("pack_id")
            print(
                f"- {entry}: {item.get('display_name')} ({item.get('status')}; "
                f"internal: {item.get('pack_id')})"
            )
        for error in payload.get("errors", []):
            print(f"[error] {error.get('field')}: {error.get('error')}")
    elif label == "packs templates":
        for item in payload.get("templates", []):
            print(f"- {item.get('template_id')}: {item.get('display_name')} ({item.get('status')})")
        for error in payload.get("errors", []):
            print(f"[error] {error.get('field')}: {error.get('error')}")
    elif label == "packs show":
        if payload.get("ok"):
            pack = payload.get("pack", {})
            print(f"pack_id: {pack.get('pack_id')}")
            if payload.get("recommended_entry"):
                print(f"recommended_entry: {payload.get('recommended_entry')}")
            if payload.get("aliases"):
                print(f"aliases: {', '.join(payload.get('aliases') or [])}")
            print(f"report_type: {pack.get('report_type')}")
            print(f"status: {pack.get('status')}")
            print(f"boundary: {_report_pack_boundary_text(pack)}")
        else:
            print(payload.get("error"))
            recommended = payload.get("recommended_entries") or []
            if recommended:
                print(f"try: {', '.join(recommended)}")
            internal = payload.get("internal_pack_ids") or []
            if internal:
                print(f"internal_pack_ids: {', '.join(internal)}")
    elif label == "new":
        if payload.get("ok"):
            workspace = payload.get("workspace")
            print(f"Created BriefLoop workspace: {workspace}")
            print(f"report_pack: {payload.get('report_pack')}")
            print(f"report_spec: {payload.get('report_spec')}")
            resolution = payload.get("policy_profile_resolution")
            resolution = resolution if isinstance(resolution, dict) else {}
            print(f"policy_profile: {payload.get('policy_profile') or 'unknown'}")
            print(f"policy_profile_source: {resolution.get('source') or 'unknown'}")
            print(
                "boundary: product workspace skeleton only; no stages, gates,"
                " rendering, or delivery were run"
            )
            web_search_mode = str(payload.get("web_search_mode") or "")
            search_backend = str(payload.get("search_backend") or "")
            search_api_key_env = str(payload.get("search_api_key_env") or "")
            if web_search_mode == "external_api" and search_backend:
                print()
                print("Online search:")
                print(f"  Online search is enabled via {search_backend}.")
                if search_api_key_env:
                    print(f"  Set {search_api_key_env} before running doctor or source discovery.")
                print("  To run without online search, recreate with --web-search-mode disabled.")
            elif web_search_mode == "configure_later":
                print()
                print("Online search:")
                print("  Online search is recommended but not active by default.")
                print("  Tavily is the recommended external API backend.")
                print("  To enable it, recreate with --search-backend tavily and set TAVILY_API_KEY.")
                print("  To stay offline, recreate with --web-search-mode disabled.")
            elif web_search_mode == "disabled":
                print()
                print("Online search: disabled.")
            print()
            print("Next:")
            print(f"  Add local evidence files under {workspace}/input/sources/")
            print(f"  briefloop doctor --config {workspace}/config.yaml")
            print(
                f"  briefloop run --workspace {workspace}"
                f" --runtime {RUNTIME_CLI_CHOICE_PLACEHOLDER}"
            )
        else:
            print(payload.get("error"))
            available = payload.get("available_packs") or []
            if available:
                print(f"available_packs: {', '.join(available)}")
            recommended = payload.get("recommended_entries") or []
            if recommended:
                print(f"try: {', '.join(recommended)}")
            internal = payload.get("internal_pack_ids") or []
            if internal:
                print(f"internal_pack_ids: {', '.join(internal)}")
    elif label == "packs bundle":
        if payload.get("ok"):
            print(f"manifest: {payload.get('manifest_path')}")
            print(f"delivery_artifacts: {payload.get('delivery_bundle', {}).get('artifact_count')}")
            print(f"audit_artifacts: {payload.get('audit_bundle', {}).get('artifact_count')}")
            archives = payload.get("bundle_archives") if isinstance(payload.get("bundle_archives"), dict) else {}
            if archives.get("status") == "generated":
                delivery_archive = archives.get("delivery") if isinstance(archives.get("delivery"), dict) else {}
                audit_archive = archives.get("audit") if isinstance(archives.get("audit"), dict) else {}
                print(f"delivery_archive: {delivery_archive.get('path')}")
                print(f"audit_archive: {audit_archive.get('path')}")
            cleanup_warning = payload.get("publication_cleanup_warning")
            if isinstance(cleanup_warning, dict):
                print(
                    "[warning] "
                    f"{cleanup_warning.get('reason_code') or 'publication_cleanup_warning'}"
                )
            print("boundary: bundle projection/export only; no render, delivery approval, or gate bypass")
        else:
            print(payload.get("error"))
    elif label == "extract":
        if payload.get("ok"):
            print(f"workspace: {payload.get('workspace')}")
            print(f"scope: {payload.get('scope')}")
            print(f"registered_sources: {payload.get('source_count')}")
            print(f"extraction_scope: {payload.get('extraction_scope')}")
            print(f"audit_extraction_scope: {payload.get('audit_extraction_scope')}")
            if payload.get("evidence_span_registry"):
                print(f"evidence_span_registry: {payload.get('evidence_span_registry')}")
                print(f"evidence_span_registry_span_count: {payload.get('evidence_span_registry_span_count')}")
            for warning in payload.get("warnings", []):
                print(f"[warning] {warning.get('code')}: {warning.get('message')}")
            print(
                "boundary: source/scope/text-span registration only; no binary parsing,"
                " semantic support assessment, legal conclusion, delivery, or gate bypass"
            )
        else:
            print(payload.get("error"))
    elif label == "quality summarize":
        if payload.get("ok"):
            print(f"workspace: {payload.get('workspace')}")
            print(f"quality_panel: {payload.get('quality_panel')}")
            print(f"quality_summary: {payload.get('quality_summary')}")
            print(f"quality_panel_html: {payload.get('quality_panel_html')}")
            print(f"overall_status: {payload.get('overall_status')}")
            actions = payload.get("recommended_actions")
            action_count = len(actions) if isinstance(actions, list) else 0
            print(f"recommended_actions: {action_count}")
            registry_refresh = payload.get("registry_refresh")
            registry_refresh = registry_refresh if isinstance(registry_refresh, dict) else {}
            print(f"registry_refresh: {registry_refresh.get('status') or 'unknown'}")
            browser_display = payload.get("browser_display")
            browser_display = browser_display if isinstance(browser_display, dict) else {}
            print(f"browser_display: {browser_display.get('status') or 'unknown'}")
            print(
                "boundary: quality projection only; no gates were run, no repair was started,"
                " no delivery was approved, and no release was authorized"
            )
        else:
            print(payload.get("error"))
    else:
        print(f"report_pack: {payload.get('report_pack')}")
        print(f"resolved_policy_profile: {payload.get('resolved_policy_profile')}")
        print(f"policy_profile_source: {payload.get('policy_profile_source')}")
        print(f"report_type: {payload.get('report_type')}")
        for error in payload.get("errors", []):
            print(f"[error] {error.get('field')}: {error.get('error')}")
        for warning in payload.get("warnings", []):
            print(f"[warning] {warning.get('field')}: {warning.get('error')}")


def _report_pack_entrypoint_payload(registry: ReportPackRegistry) -> dict[str, Any]:
    pack_ids = sorted(registry.pack_ids())
    return {
        "available_packs": pack_ids,
        "internal_pack_ids": pack_ids,
        "recommended_entries": recommended_entries_for_pack_ids(pack_ids),
    }


def _with_report_pack_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(payload)
    packs = []
    for item in enriched.get("packs", []):
        if not isinstance(item, dict):
            packs.append(item)
            continue
        pack_id = str(item.get("pack_id") or "")
        updated = dict(item)
        updated["aliases"] = aliases_for_report_pack(pack_id)
        recommended_entry = RECOMMENDED_REPORT_PACK_ENTRIES.get(pack_id)
        if recommended_entry:
            updated["recommended_entry"] = recommended_entry
        packs.append(updated)
    enriched["packs"] = packs
    return enriched


def _report_pack_boundary_text(pack: dict[str, Any]) -> str:
    status = str(pack.get("status") or "").strip()
    if status == "supported":
        return "supported product-layer contract; control spine and gates remain required"
    return "experimental product-layer contract only"


def _resolve_report_pack_policy_profile(
    *,
    pack: Any,
    args: argparse.Namespace,
    known_policy_profiles: set[str],
) -> PolicyProfileResolution:
    explicit_profile = getattr(args, "policy_profile", None)
    resolution = resolve_policy_profile(
        default_policy_profile=pack.default_policy_profile,
        explicit_policy_profile=explicit_profile,
        industry=getattr(args, "industry", None),
        company=getattr(args, "company", None),
        known_policy_profiles=known_policy_profiles,
    )
    if explicit_profile:
        return resolution

    specialized_default = SPECIALIZED_REPORT_PACK_POLICY_PROFILES.get(pack.pack_id)
    if (
        specialized_default
        and pack.default_policy_profile == specialized_default
        and resolution.policy_profile != specialized_default
    ):
        return PolicyProfileResolution(
            policy_profile=specialized_default,
            source="report_pack.default_policy_profile",
            input=resolution.input,
            matched_rule="specialized_report_pack_default",
            confidence="default_specialized_pack",
            alternatives=(resolution.policy_profile,),
        )
    return resolution


def _create_report_pack_workspace(*, target: Path, pack: Any, args: argparse.Namespace) -> dict[str, Any]:
    from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

    policy_registry = PolicyProfileRegistry.from_package()
    spec = deepcopy(dict(pack.default_report_spec))
    policy_resolution = _resolve_report_pack_policy_profile(
        pack=pack,
        args=args,
        known_policy_profiles=policy_registry.profile_ids(),
    )
    spec["policy_profile"] = policy_resolution.policy_profile
    spec["policy_profile_resolution"] = policy_resolution.to_dict()
    audience = spec.get("audience") if isinstance(spec.get("audience"), dict) else {}
    title = args.title or str(spec.get("title") or pack.display_name or "BriefLoop Report")
    language = args.language or str(audience.get("language") or "en-US")
    reader_label = args.audience or str(audience.get("label") or "business reader")
    industry_hint = getattr(args, "industry", None)
    industry_text = industry_hint if isinstance(industry_hint, str) and industry_hint.strip() else pack.display_name
    spec["title"] = title
    spec["audience"] = dict(audience)
    spec["audience"]["label"] = reader_label
    spec["audience"]["language"] = language
    cadence = str(spec.get("cadence") or "weekly")
    outputs = (
        spec.get("outputs")
        if isinstance(spec.get("outputs"), list)
        else ["markdown", "docx"]
    )

    profile = InitProfile(
        interface_language=language,
        output_language=language,
        company=args.company,
        role="report_owner",
        industry=industry_text,
        industry_text=industry_text,
        brief_title=title,
        audience=reader_label,
        audience_profile="management",
        focus_areas=[pack.display_name, "source-backed claims", "reader-ready brief"],
        task_objective=(
            f"Prepare a {pack.display_name} using local-first sources and the "
            "BriefLoop control spine."
        ),
        forbidden_sources=[
            "confidential material not approved for this workspace",
            "private messages",
            "credentials",
            "material non-public information",
        ],
        cadence=cadence,
        selector_max_items=PRODUCT_WORKSPACE_SELECTOR_MAX_ITEMS,
        output_formats=[str(item) for item in outputs],
        source_profile="conservative",
        tavily_enabled=False,
        web_search_enabled=True,
        web_search_mode="configure_later",
        search_backend="",
    )
    web_search_mode = getattr(args, "web_search_mode", None)
    if web_search_mode:
        profile.web_search_mode = web_search_mode
        profile.web_search_enabled = web_search_mode != "disabled"
        if web_search_mode == "disabled":
            profile.tavily_enabled = False
            profile.search_backend = ""
        elif web_search_mode == "external_api" and not profile.search_backend:
            profile.tavily_enabled = True
            profile.search_backend = "tavily"
        elif web_search_mode in {"runtime_tool", "configure_later"}:
            profile.tavily_enabled = False
            profile.search_backend = ""
    search_backend = getattr(args, "search_backend", None)
    if search_backend:
        profile.search_backend = search_backend
        profile.web_search_mode = "external_api"
        profile.web_search_enabled = True
        profile.tavily_enabled = search_backend == "tavily"
    search_api_key_env = {
        "tavily": "TAVILY_API_KEY",
        "exa": "EXA_API_KEY",
        "brave": "BRAVE_SEARCH_API_KEY",
        "firecrawl": "FIRECRAWL_API_KEY",
        "serper": "SERPER_API_KEY",
    }.get(profile.search_backend, "")
    spec_path = target / "report_spec.yaml"
    if spec_path.exists() and not getattr(args, "force", False):
        raise FileExistsError(
            f"Refusing to overwrite existing file: {spec_path}. Use --force to overwrite."
        )
    create_workspace(target, profile, force=bool(getattr(args, "force", False)))
    spec_path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {
        "policy_profile": policy_resolution.policy_profile,
        "policy_profile_resolution": policy_resolution.to_dict(),
        "web_search_mode": profile.web_search_mode,
        "search_backend": profile.search_backend,
        "search_api_key_env": search_api_key_env,
    }


def _register_evidence_extract_scope(*, workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"workspace does not exist: {workspace}")
    spec_path = workspace / "report_spec.yaml"
    if not spec_path.exists():
        raise ValueError("report_spec.yaml not found. Run `briefloop new evidence-extract <workspace>` first.")
    spec = load_report_spec(spec_path)
    if spec.get("report_pack") != "evidence_extract":
        raise ValueError(
            "extract is only supported for evidence_extract workspaces in this release."
        )
    scope = str(getattr(args, "scope", "") or "").strip()
    if not scope:
        raise ValueError("--scope must be non-empty")

    source_paths = _resolve_extract_source_paths(
        [*getattr(args, "source", []), *getattr(args, "sources", [])]
    )
    if not source_paths:
        raise ValueError("at least one --source or --sources path is required")
    resolved_sources: list[Path] = []
    seen_resolved: set[Path] = set()
    for source_path in source_paths:
        resolved = source_path.expanduser().resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"source file not found: {source_path}")
        resolved_sources.append(resolved)
    resolved_sources = _filter_paired_mineru_markdown_sources(resolved_sources)

    sources_dir = workspace / "input" / "sources" / "evidence_extract"
    warnings: list[dict[str, str]] = []
    registered: list[dict[str, Any]] = []
    derived_inputs_by_source: dict[Path, Path] = {}
    for idx, resolved in enumerate(resolved_sources, start=1):
        target = sources_dir / f"{idx:03d}-{_safe_filename(resolved.name)}"
        rel_target = _workspace_relative(workspace, target)
        extension = target.suffix.lower()
        derived_input = (
            _adjacent_mineru_markdown_for_source(resolved)
            if extension in EVIDENCE_EXTRACT_BINARY_EXTENSIONS
            else None
        )
        derived_target = extracted_markdown_path(target) if derived_input is not None else None
        derived_rel_target = _workspace_relative(workspace, derived_target) if derived_target is not None else ""
        manual_enabled = extension in EVIDENCE_EXTRACT_TEXT_EXTENSIONS or derived_input is not None
        if extension in EVIDENCE_EXTRACT_BINARY_EXTENSIONS:
            if derived_input is None:
                warnings.append(
                    {
                        "code": "binary_source_registered_only",
                        "message": (
                            f"{rel_target} was registered as durable source bytes; "
                            "BriefLoop does not parse binary/PDF spans in this command."
                        ),
                    }
                )
                warnings.append(
                    {
                        "code": "requires_mineru_extraction",
                        "message": (
                            f"{rel_target} has no adjacent MinerU Markdown representation; "
                            f"expected {extracted_markdown_path(resolved).name} before text-span registration."
                        ),
                    }
                )
            else:
                derived_inputs_by_source[resolved] = derived_input
                warnings.append(
                    {
                        "code": "mineru_derived_markdown_registered",
                        "message": (
                            f"{rel_target} remains the locked source bytes; "
                            f"{derived_rel_target} is used as the MinerU-derived text representation."
                        ),
                    }
                )
        elif extension and extension not in EVIDENCE_EXTRACT_TEXT_EXTENSIONS:
            warnings.append(
                {
                    "code": "unknown_text_support",
                    "message": f"{rel_target} was registered, but automatic text ingestion may not support it.",
                }
            )
        elif not extension:
            warnings.append(
                {
                    "code": "unknown_text_support",
                    "message": f"{rel_target} was registered, but automatic text ingestion may not support it.",
                }
            )
        registered.append(
            {
                "source_id": f"SRC-{idx:03d}",
                "name": resolved.stem,
                "path": rel_target,
                "filename": target.name,
                "extension": extension,
                "source_sha256": _sha256_file(resolved),
                "source_size_bytes": resolved.stat().st_size,
                "manual_enabled": manual_enabled,
            }
        )
        if derived_input is not None and derived_target is not None:
            registered[-1].update(
                {
                    "derived_markdown_path": derived_rel_target,
                    "derived_markdown_filename": derived_target.name,
                    "derived_markdown_sha256": _sha256_file(derived_input),
                    "derived_markdown_size_bytes": derived_input.stat().st_size,
                    "derived_markdown_derivation": "mineru_adjacent_markdown",
                    "derived_markdown_extractor": "mineru",
                    "text_evidence_path": derived_rel_target,
                    "text_evidence_sha256": _sha256_file(derived_input),
                    "text_evidence_size_bytes": derived_input.stat().st_size,
                    "text_evidence_basis": "mineru_derived_markdown",
                }
            )

    if sources_dir.exists() and any(sources_dir.iterdir()) and not getattr(args, "force", False):
        raise FileExistsError(
            f"Refusing to overwrite existing evidence-extract sources: {sources_dir}. Use --force."
        )
    _build_sources_yaml_text(
        workspace=workspace,
        sources=registered,
        source_category=normalize_source_category(getattr(args, "source_category", None), default="other"),
        language=str(getattr(args, "language", "") or "en").strip() or "en",
    )
    staged_sources: dict[Path, Path] = {}
    staging_parent = workspace / "output" / "intermediate"
    managed_candidates = [*resolved_sources, *derived_inputs_by_source.values()]
    managed_sources = (
        _managed_extract_source_inputs(managed_candidates, sources_dir=sources_dir)
        if getattr(args, "force", False)
        else []
    )
    if managed_sources:
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".briefloop-evidence-extract-",
            dir=staging_parent,
        ) as staging_dir_text:
            staged_sources = _stage_extract_source_files(
                managed_sources,
                staging_dir=Path(staging_dir_text),
            )
            _copy_extract_sources(
                workspace=workspace,
                sources_dir=sources_dir,
                sources=resolved_sources,
                registered=registered,
                derived_inputs_by_source=derived_inputs_by_source,
                staged_sources=staged_sources,
                force=bool(getattr(args, "force", False)),
            )
    else:
        _copy_extract_sources(
            workspace=workspace,
            sources_dir=sources_dir,
            sources=resolved_sources,
            registered=registered,
            derived_inputs_by_source=derived_inputs_by_source,
            staged_sources={},
            force=bool(getattr(args, "force", False)),
        )

    _refresh_registered_source_digests(workspace=workspace, registered=registered)
    sources_yaml_text = _build_sources_yaml_text(
        workspace=workspace,
        sources=registered,
        source_category=normalize_source_category(getattr(args, "source_category", None), default="other"),
        language=str(getattr(args, "language", "") or "en").strip() or "en",
    )
    source_lock_path = _write_evidence_extract_source_lock(
        workspace=workspace,
        sources=registered,
        scope=scope,
        warnings=warnings,
    )
    page_inventory_path, page_count, page_inventory_source_count = _write_evidence_extract_page_inventory(
        workspace=workspace,
        sources=registered,
        scope=scope,
        warnings=warnings,
        source_lock_path=source_lock_path,
    )
    span_registry_path, span_count, span_source_count = _write_evidence_extract_span_registry(
        workspace=workspace,
        sources=registered,
        warnings=warnings,
    )
    extraction_scope_text = _extraction_scope_text(scope=scope, sources=registered, warnings=warnings)
    _write_extraction_scope(workspace=workspace, text=extraction_scope_text)
    (workspace / "sources.yaml").write_text(sources_yaml_text, encoding="utf-8")
    payload = {
        "ok": True,
        "workspace": str(workspace),
        "scope": scope,
        "source_count": len(registered),
        "sources": registered,
        "extraction_scope": "extraction_scope.yaml",
        "audit_extraction_scope": "output/audit/extraction_scope.yaml",
        "source_lock": _workspace_relative(workspace, source_lock_path),
        "audit_source_lock": "output/audit/evidence_extract_source_lock.json",
        "page_inventory": _workspace_relative(workspace, page_inventory_path),
        "audit_page_inventory": "output/audit/evidence_extract_page_inventory.json",
        "page_inventory_source_count": page_inventory_source_count,
        "page_inventory_page_count": page_count,
        "evidence_span_registry": _workspace_relative(workspace, span_registry_path) if span_registry_path else "",
        "evidence_span_registry_source_count": span_source_count,
        "evidence_span_registry_span_count": span_count,
        "warnings": warnings,
        "boundary": "evidence_extract_scope_source_and_text_span_registration_only",
        "non_claims": [
            "no_binary_span_extraction",
            "no_claim_support_matrix_generation",
            "no_semantic_support_assessment",
            "no_legal_conclusion",
            "no_disclosure_readiness",
            "no_delivery_or_publication_authority",
        ],
    }
    return payload


def _write_evidence_extract_source_lock(
    *,
    workspace: Path,
    sources: list[dict[str, Any]],
    scope: str,
    warnings: list[dict[str, str]],
) -> Path:
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    locked_sources: list[dict[str, Any]] = []
    for record in sources:
        source_record = {
            "source_id": str(record.get("source_id") or ""),
            "path": str(record.get("path") or ""),
            "filename": str(record.get("filename") or ""),
            "extension": str(record.get("extension") or ""),
            "source_sha256": str(record.get("source_sha256") or ""),
            "source_size_bytes": int(record.get("source_size_bytes") or 0),
            "registered_only": not bool(record.get("manual_enabled")),
            "lock_status": "locked_source_bytes",
        }
        if record.get("derived_markdown_path"):
            source_record["derived_markdown"] = {
                "path": str(record.get("derived_markdown_path") or ""),
                "filename": str(record.get("derived_markdown_filename") or ""),
                "sha256": str(record.get("derived_markdown_sha256") or ""),
                "size_bytes": int(record.get("derived_markdown_size_bytes") or 0),
                "derivation": str(record.get("derived_markdown_derivation") or ""),
                "extractor": str(record.get("derived_markdown_extractor") or ""),
                "source_path": str(record.get("path") or ""),
            }
            source_record["text_evidence_path"] = str(record.get("text_evidence_path") or "")
            source_record["text_evidence_basis"] = str(record.get("text_evidence_basis") or "")
        locked_sources.append(source_record)
    payload = {
        "schema_version": EVIDENCE_EXTRACT_SOURCE_LOCK_SCHEMA_VERSION,
        "report_pack": "evidence_extract",
        "created_at": created_at,
        "scope_path": "extraction_scope.yaml",
        "source_count": len(sources),
        "sources": locked_sources,
        "scope_excerpt": scope[:500],
        "warnings": warnings,
        "boundary": "deterministic_registered_source_lock_only",
        "non_claims": [
            "extract_command_does_not_run_mineru",
            "derived_markdown_is_text_representation_not_original_source",
            "no_pdf_or_binary_page_extraction",
            "no_pdf_or_binary_page_extraction_without_derived_markdown",
            "no_rendered_page_visual_check",
            "no_evidence_ledger_generation",
            "no_citation_gate",
            "no_semantic_support_assessment",
            "no_legal_conclusion",
            "no_disclosure_readiness",
            "no_delivery_or_publication_authority",
        ],
    }
    lock_path = workspace / "output" / "intermediate" / "evidence_extract_source_lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    lock_path.write_text(text, encoding="utf-8")
    audit_path = workspace / "output" / "audit" / "evidence_extract_source_lock.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(text, encoding="utf-8")
    return lock_path


def _write_evidence_extract_page_inventory(
    *,
    workspace: Path,
    sources: list[dict[str, Any]],
    scope: str,
    warnings: list[dict[str, str]],
    source_lock_path: Path,
) -> tuple[Path, int, int]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    inventory_sources: list[dict[str, Any]] = []
    page_count = 0
    inventory_source_count = 0
    for record in sources:
        source_id = str(record.get("source_id") or "")
        source_path = str(record.get("path") or "")
        extension = str(record.get("extension") or "")
        source_file = workspace / source_path
        text_evidence = _text_evidence_for_record(workspace=workspace, record=record)
        source_text = _source_text_for_span_registry(text_evidence["path"]) if text_evidence else None
        page_basis = str(text_evidence["basis"]) if text_evidence else ""
        needs_visual_inspection = page_basis == "mineru_derived_markdown"
        pages: list[dict[str, Any]] = []
        if source_text is not None and source_text.strip():
            page_id = _evidence_extract_page_id(source_id)
            pages.append(
                {
                    "page_id": page_id,
                    "page_number": 1,
                    "page_label": "logical-page-1",
                    "page_basis": page_basis,
                    "has_searchable_text": True,
                    "needs_visual_inspection": needs_visual_inspection,
                    "char_start": 0,
                    "char_end": len(source_text),
                }
            )
            inventory_status = "text_logical_page_seeded"
            inventory_source_count += 1
            page_count += 1
        elif source_text is not None:
            inventory_status = "text_empty_no_pages"
            warnings.append(
                {
                    "code": "page_inventory_skipped_empty_text_source",
                    "message": f"{source_path} has no non-empty text; no logical page was generated.",
                }
            )
        else:
            inventory_status = "unsupported_source_format_registered_only"

        inventory_sources.append(
            {
                "source_id": source_id,
                "source_path": source_path,
                "extension": extension,
                "source_sha256": str(record.get("source_sha256") or ""),
                "inventory_status": inventory_status,
                "page_count": len(pages),
                "needs_external_extraction_tool": source_text is None,
                "visual_inspection_required": source_text is None or needs_visual_inspection,
                "pages": pages,
            }
        )
        if text_evidence is not None:
            inventory_sources[-1]["text_source_path"] = str(text_evidence["rel_path"])
            inventory_sources[-1]["text_source_sha256"] = str(text_evidence["sha256"])
            inventory_sources[-1]["text_evidence_basis"] = str(text_evidence["basis"])

    payload = {
        "schema_version": EVIDENCE_EXTRACT_PAGE_INVENTORY_SCHEMA_VERSION,
        "report_pack": "evidence_extract",
        "generated_at": generated_at,
        "scope_path": "extraction_scope.yaml",
        "source_lock_path": "output/intermediate/evidence_extract_source_lock.json",
        "source_lock_sha256": _sha256_file(source_lock_path),
        "source_count": len(inventory_sources),
        "page_count": page_count,
        "inventory_source_count": inventory_source_count,
        "sources": inventory_sources,
        "scope_excerpt": scope[:500],
        "warnings": warnings,
        "boundary": "deterministic_page_inventory_seed_not_document_parsing",
        "non_claims": [
            "no_pdf_or_binary_page_extraction",
            "no_rendered_page_visual_check",
            "no_table_or_figure_extraction",
            "no_evidence_ledger_generation",
            "no_citation_gate",
            "no_semantic_support_assessment",
            "no_legal_conclusion",
            "no_disclosure_readiness",
            "no_delivery_or_publication_authority",
        ],
    }
    inventory_path = workspace / "output" / "intermediate" / "evidence_extract_page_inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    inventory_path.write_text(text, encoding="utf-8")
    audit_path = workspace / "output" / "audit" / "evidence_extract_page_inventory.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(text, encoding="utf-8")
    return inventory_path, page_count, inventory_source_count


def _evidence_extract_page_id(source_id: str) -> str:
    return f"PAGE-{source_id}-001"


def _copy_extract_sources(
    *,
    workspace: Path,
    sources_dir: Path,
    sources: list[Path],
    registered: list[dict[str, Any]],
    derived_inputs_by_source: dict[Path, Path],
    staged_sources: dict[Path, Path],
    force: bool,
) -> None:
    sources_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for path in sources_dir.iterdir():
            if path.is_file():
                path.unlink()

    for source, record in zip(sources, registered):
        shutil.copy2(staged_sources.get(source, source), workspace / record["path"])
        derived_input = derived_inputs_by_source.get(source)
        if derived_input is not None and record.get("derived_markdown_path"):
            shutil.copy2(
                staged_sources.get(derived_input, derived_input),
                workspace / str(record["derived_markdown_path"]),
            )


def _refresh_registered_source_digests(*, workspace: Path, registered: list[dict[str, Any]]) -> None:
    for record in registered:
        source_path = workspace / str(record.get("path") or "")
        record["source_sha256"] = _sha256_file(source_path)
        record["source_size_bytes"] = source_path.stat().st_size
        if record.get("derived_markdown_path"):
            derived_path = workspace / str(record.get("derived_markdown_path") or "")
            derived_sha = _sha256_file(derived_path)
            derived_size = derived_path.stat().st_size
            record["derived_markdown_sha256"] = derived_sha
            record["derived_markdown_size_bytes"] = derived_size
            record["text_evidence_sha256"] = derived_sha
            record["text_evidence_size_bytes"] = derived_size


def _managed_extract_source_inputs(sources: list[Path], *, sources_dir: Path) -> list[Path]:
    managed_root = sources_dir.expanduser().resolve()
    managed: list[Path] = []
    for source in sources:
        try:
            source.expanduser().resolve().relative_to(managed_root)
        except ValueError:
            continue
        managed.append(source)
    return managed


def _stage_extract_source_files(sources: list[Path], *, staging_dir: Path) -> dict[Path, Path]:
    """Copy managed inputs before managed-source cleanup.

    `extract --force` may be rerun with source paths that already live under
    `input/sources/evidence_extract/`. Staging first prevents the cleanup step
    from deleting those managed inputs before the final copy reads them. External
    inputs are not staged through system temp.
    """

    staging_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    for idx, source in enumerate(sources, start=1):
        target = staging_dir / f"{idx:03d}-{_safe_filename(source.name)}"
        shutil.copy2(source, target)
        staged[source] = target
    return staged


def _resolve_extract_source_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        expanded = str(Path(raw).expanduser())
        if any(token in raw for token in "*?[]"):
            matches = sorted(glob.glob(expanded))
            paths.extend(Path(item) for item in matches)
        else:
            paths.append(Path(expanded))
    return paths


def _write_extraction_scope(
    *,
    workspace: Path,
    text: str,
) -> None:
    (workspace / "extraction_scope.yaml").write_text(text, encoding="utf-8")
    audit_dir = workspace / "output" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "extraction_scope.yaml").write_text(text, encoding="utf-8")


def _write_evidence_extract_span_registry(
    *,
    workspace: Path,
    sources: list[dict[str, Any]],
    warnings: list[dict[str, str]],
) -> tuple[Path | None, int, int]:
    registry_sources: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for record in sources:
        if not bool(record.get("manual_enabled")):
            continue
        text_evidence = _text_evidence_for_record(workspace=workspace, record=record)
        if text_evidence is None:
            continue
        source_path = str(text_evidence["rel_path"])
        source_text = _source_text_for_span_registry(text_evidence["path"])
        if source_text is None:
            warnings.append(
                {
                    "code": "text_span_seed_skipped",
                    "message": f"{source_path} is not readable as UTF-8 text; no evidence span was generated.",
                }
            )
            continue
        span = _first_text_span(
            source_text,
            source_id=str(record.get("source_id") or ""),
        )
        if span is None:
            warnings.append(
                {
                    "code": "text_span_seed_skipped",
                    "message": f"{source_path} has no non-empty text; no evidence span was generated.",
                }
            )
            continue
        registry_sources.append(
            {
                "source_id": record["source_id"],
                "source_type": "local_file",
                "source_tier": "registered_local_source",
                "source_path": source_path,
                "retrieved_at": generated_at,
                "spans": [span],
                "metadata": {
                    "source_sha256": record.get("source_sha256", ""),
                    "source_size_bytes": record.get("source_size_bytes", 0),
                    "text_evidence_sha256": text_evidence.get("sha256", ""),
                    "text_evidence_size_bytes": text_evidence.get("size_bytes", 0),
                    "text_evidence_basis": text_evidence.get("basis", ""),
                    "evidence_extract_source": True,
                    "extraction_scope": "extraction_scope.yaml",
                },
            }
        )
        if record.get("derived_markdown_path"):
            registry_sources[-1]["metadata"]["original_source_path"] = record.get("path", "")
            registry_sources[-1]["metadata"]["derived_markdown_path"] = record.get("derived_markdown_path", "")

    if not registry_sources:
        registry_path = workspace / "output" / "intermediate" / "evidence_span_registry.json"
        try:
            registry_path.unlink()
        except FileNotFoundError:
            pass
        warnings.append(
            {
                "code": "no_text_evidence_spans_generated",
                "message": "No text source supported deterministic evidence-span seed generation.",
            }
        )
        return None, 0, 0

    payload = {
        "schema_version": EVIDENCE_SPAN_REGISTRY_SCHEMA_VERSION,
        "sources": registry_sources,
        "metadata": {
            "source": "evidence_extract",
            "boundary": "deterministic_text_span_seed_not_semantic_support",
            "extraction_scope": "extraction_scope.yaml",
            "non_claims": [
                "no_semantic_support_assessment",
                "no_claim_support_matrix_generation",
                "no_binary_span_extraction",
                "no_legal_conclusion",
                "no_disclosure_readiness",
            ],
        },
    }
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    registry_path = intermediate / "evidence_span_registry.json"
    registry_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    span_count = sum(len(source.get("spans") or []) for source in registry_sources)
    return registry_path, span_count, len(registry_sources)


def _source_text_for_span_registry(path: Path) -> str | None:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return payload["content"]
    return raw_text


def _adjacent_mineru_markdown_for_source(path: Path) -> Path | None:
    candidate = extracted_markdown_path(path)
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _filter_paired_mineru_markdown_sources(sources: list[Path]) -> list[Path]:
    source_set = set(sources)
    consumed_markdown = {
        extracted_markdown_path(source).resolve()
        for source in sources
        if source.suffix.lower() in EVIDENCE_EXTRACT_BINARY_EXTENSIONS
        and extracted_markdown_path(source).resolve() in source_set
    }
    if not consumed_markdown:
        return sources
    return [source for source in sources if source not in consumed_markdown]


def _text_evidence_for_record(*, workspace: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    if not bool(record.get("manual_enabled")):
        return None
    rel_path = str(record.get("text_evidence_path") or record.get("path") or "")
    if not rel_path:
        return None
    return {
        "path": workspace / rel_path,
        "rel_path": rel_path,
        "sha256": str(record.get("text_evidence_sha256") or record.get("source_sha256") or ""),
        "size_bytes": int(record.get("text_evidence_size_bytes") or record.get("source_size_bytes") or 0),
        "basis": str(record.get("text_evidence_basis") or "utf8_text_file"),
    }


def _first_text_span(source_text: str, *, source_id: str) -> dict[str, Any] | None:
    start = next((idx for idx, char in enumerate(source_text) if not char.isspace()), None)
    if start is None:
        return None
    paragraph_end = source_text.find("\n\n", start)
    if paragraph_end == -1:
        paragraph_end = len(source_text)
    end = min(paragraph_end, start + EVIDENCE_EXTRACT_SPAN_EXCERPT_LIMIT)
    while end > start and source_text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    raw_excerpt = source_text[start:end]
    source_digits = source_id.removeprefix("SRC-")
    return {
        "span_id": f"ESP-{source_digits}-01",
        "page_id": _evidence_extract_page_id(source_id),
        "page_number": 1,
        "raw_excerpt": raw_excerpt,
        "hash": "sha256:" + hashlib.sha256(raw_excerpt.encode("utf-8")).hexdigest(),
        "span_role": "direct_statement",
        "char_start": start,
        "char_end": end,
    }


def _extraction_scope_text(
    *,
    scope: str,
    sources: list[dict[str, Any]],
    warnings: list[dict[str, str]],
) -> str:
    payload = {
        "schema_version": "briefloop.extraction_scope.v1",
        "report_pack": "evidence_extract",
        "scope": scope,
        "source_count": len(sources),
        "sources": sources,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "warnings": warnings,
        "boundary": "scope_source_and_text_span_registration_only",
        "non_claims": [
            "no_binary_span_extraction",
            "no_semantic_support_assessment",
            "no_legal_conclusion",
            "no_disclosure_readiness",
            "no_semantic_proof",
            "no_claim_support_matrix_generation",
            "no_delivery_or_publication_authority",
        ],
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _build_sources_yaml_text(
    *,
    workspace: Path,
    sources: list[dict[str, Any]],
    source_category: str,
    language: str,
) -> str:
    path = workspace / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data if isinstance(data, dict) else {}
    strategy = data.get("source_strategy") if isinstance(data.get("source_strategy"), dict) else {}
    enabled = strategy.get("enabled_providers")
    if isinstance(enabled, str):
        enabled = [enabled]
    elif not isinstance(enabled, list):
        enabled = []
    if "manual" not in enabled:
        enabled.append("manual")
    strategy["enabled_providers"] = enabled
    strategy.setdefault("profile", "conservative")
    data["source_strategy"] = strategy

    manual = data.get("manual") if isinstance(data.get("manual"), dict) else {}
    existing = [
        item
        for item in manual.get("sources", [])
        if isinstance(item, dict) and not item.get("evidence_extract_registered")
    ]
    for record in sources:
        text_path = str(record.get("text_evidence_path") or record["path"])
        existing.append(
            {
                "name": record["name"],
                "path": text_path,
                "category": source_category,
                "language": language,
                "enabled": bool(record.get("manual_enabled")),
                "evidence_extract_registered": True,
                "registered_only": not bool(record.get("manual_enabled")),
                "metadata": {
                    "source_id": record["source_id"],
                    "extraction_scope": "extraction_scope.yaml",
                    "source_sha256": record["source_sha256"],
                    "source_size_bytes": record["source_size_bytes"],
                    "original_source_path": record["path"],
                },
            }
        )
        if record.get("derived_markdown_path"):
            existing[-1]["metadata"].update(
                {
                    "derived_markdown_path": record["derived_markdown_path"],
                    "derived_markdown_sha256": record["derived_markdown_sha256"],
                    "derived_markdown_size_bytes": record["derived_markdown_size_bytes"],
                    "text_evidence_basis": record.get("text_evidence_basis", ""),
                }
            )
    manual["enabled"] = True
    manual["sources"] = existing
    data["manual"] = manual
    web_search = data.get("web_search") if isinstance(data.get("web_search"), dict) else {}
    web_search.setdefault("enabled", False)
    web_search.setdefault("mode", "disabled")
    data["web_search"] = web_search
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _safe_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name.strip())
    safe = safe.strip(".-")
    return safe or "source"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)
