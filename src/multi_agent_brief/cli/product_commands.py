"""Product-layer ReportPack and ReportSpec CLI surfaces."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.product.bundle_projection import (
    ReportBundleProjectionError,
    write_report_bundle_manifest,
)
from multi_agent_brief.product.policy_registry import PolicyProfileRegistry
from multi_agent_brief.product.policy_resolver import PolicyProfileResolution, resolve_policy_profile
from multi_agent_brief.product.report_registry import ReportPackRegistry
from multi_agent_brief.product.report_spec import (
    ReportSpecLoadError,
    load_report_spec,
    validate_report_spec_payload,
)
from multi_agent_brief.product.template_registry import ReportTemplateRegistry

SPECIALIZED_REPORT_PACK_POLICY_PROFILES = {
    "solar_industry_periodic": "solar_manufacturing_default",
}


def register_new_workspace(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "new",
        help="Create a conservative zero-config workspace from an experimental ReportPack.",
    )
    parser.add_argument("report_pack", help="ReportPack id, for example market-weekly.")
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

    templates_parser = actions.add_parser(
        "templates",
        help="List packaged experimental ReportTemplate contracts.",
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
    bundle_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_validate_report_spec(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-report-spec",
        help="Validate an experimental ReportSpec YAML file.",
    )
    parser.add_argument("report_spec", help="Path to report_spec.yaml.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def handle_new_workspace(args: argparse.Namespace) -> int:
    registry = ReportPackRegistry.from_package()
    requested_pack_id = _normalize_pack_id(args.report_pack)
    pack = registry.get(requested_pack_id)
    if pack is None:
        payload = {
            "ok": False,
            "error": f"unknown report pack: {args.report_pack}",
            "available_packs": sorted(registry.pack_ids()),
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
        "boundary": "zero_config_workspace_skeleton_only",
    }
    _print_payload("new", payload, as_json=False)
    return 0


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
    elif label == "packs templates":
        for item in payload.get("templates", []):
            print(f"- {item.get('template_id')}: {item.get('display_name')} ({item.get('status')})")
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
            print()
            print("Next:")
            print(f"  Add local evidence files under {workspace}/input/sources/")
            print(f"  briefloop doctor --config {workspace}/config.yaml")
            print(f"  briefloop run --workspace {workspace}")
        else:
            print(f"[new] ok: {payload.get('ok')}")
            print(payload.get("error"))
            available = payload.get("available_packs") or []
            if available:
                print(f"available_packs: {', '.join(available)}")
    elif label == "packs bundle":
        if payload.get("ok"):
            print(f"manifest: {payload.get('manifest_path')}")
            print(f"delivery_artifacts: {payload.get('delivery_bundle', {}).get('artifact_count')}")
            print(f"audit_artifacts: {payload.get('audit_bundle', {}).get('artifact_count')}")
            print("boundary: projection only; no render, delivery, approval, or gate bypass")
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


def _normalize_pack_id(value: str) -> str:
    return value.strip().replace("-", "_")


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
        selector_max_items=12,
        output_formats=[str(item) for item in outputs],
        source_profile="conservative",
        web_search_enabled=False,
        web_search_mode="disabled",
    )
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
    }
