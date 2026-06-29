"""Product-layer ReportPack and ReportSpec CLI surfaces."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from multi_agent_brief.contracts.source_metadata import normalize_source_category
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
    "evidence_extract": "evidence_extract_default",
    "solar_industry_periodic": "solar_manufacturing_default",
}
EVIDENCE_EXTRACT_BINARY_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg"}
EVIDENCE_EXTRACT_TEXT_EXTENSIONS = {".md", ".txt", ".json"}


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
    bundle_parser.add_argument(
        "--write-archives",
        action="store_true",
        help="Write clean delivery_bundle.zip and audit_bundle.zip from the manifest artifacts.",
    )
    bundle_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def register_validate_report_spec(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-report-spec",
        help="Validate an experimental ReportSpec YAML file.",
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
            "boundary": "evidence_extract_scope_and_source_registration_only",
        }
        _print_payload("extract", payload, as_json=getattr(args, "json", False))
        return 1

    _print_payload("extract", payload, as_json=getattr(args, "json", False))
    return 0


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
            archives = payload.get("bundle_archives") if isinstance(payload.get("bundle_archives"), dict) else {}
            if archives.get("status") == "generated":
                delivery_archive = archives.get("delivery") if isinstance(archives.get("delivery"), dict) else {}
                audit_archive = archives.get("audit") if isinstance(archives.get("audit"), dict) else {}
                print(f"delivery_archive: {delivery_archive.get('path')}")
                print(f"audit_archive: {audit_archive.get('path')}")
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
            for warning in payload.get("warnings", []):
                print(f"[warning] {warning.get('code')}: {warning.get('message')}")
            print(
                "boundary: source/scope registration only; no parsing, span extraction,"
                " legal conclusion, delivery, or gate bypass"
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

    sources_dir = workspace / "input" / "sources" / "evidence_extract"
    warnings: list[dict[str, str]] = []
    registered: list[dict[str, Any]] = []
    for idx, resolved in enumerate(resolved_sources, start=1):
        target = sources_dir / f"{idx:03d}-{_safe_filename(resolved.name)}"
        rel_target = _workspace_relative(workspace, target)
        extension = target.suffix.lower()
        manual_enabled = extension in EVIDENCE_EXTRACT_TEXT_EXTENSIONS
        if extension in EVIDENCE_EXTRACT_BINARY_EXTENSIONS:
            warnings.append(
                {
                    "code": "binary_source_registered_only",
                    "message": (
                        f"{rel_target} was registered as durable source bytes; "
                        "BriefLoop does not parse binary/PDF spans in this command."
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
                "source_id": f"EVID-{idx:03d}",
                "name": resolved.stem,
                "path": rel_target,
                "filename": target.name,
                "extension": extension,
                "source_sha256": _sha256_file(resolved),
                "source_size_bytes": resolved.stat().st_size,
                "manual_enabled": manual_enabled,
            }
        )

    sources_yaml_text = _build_sources_yaml_text(
        workspace=workspace,
        sources=registered,
        source_category=normalize_source_category(getattr(args, "source_category", None), default="other"),
        language=str(getattr(args, "language", "") or "en").strip() or "en",
    )
    extraction_scope_text = _extraction_scope_text(scope=scope, sources=registered, warnings=warnings)

    if sources_dir.exists() and any(sources_dir.iterdir()) and not getattr(args, "force", False):
        raise FileExistsError(
            f"Refusing to overwrite existing evidence-extract sources: {sources_dir}. Use --force."
        )
    staged_sources: dict[Path, Path] = {}
    staging_parent = workspace / "output" / "intermediate"
    managed_sources = (
        _managed_extract_source_inputs(resolved_sources, sources_dir=sources_dir)
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
                staged_sources=staged_sources,
                force=bool(getattr(args, "force", False)),
            )
    else:
        _copy_extract_sources(
            workspace=workspace,
            sources_dir=sources_dir,
            sources=resolved_sources,
            registered=registered,
            staged_sources={},
            force=bool(getattr(args, "force", False)),
        )

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
        "warnings": warnings,
        "boundary": "evidence_extract_scope_and_source_registration_only",
        "non_claims": [
            "no_automatic_span_extraction",
            "no_legal_conclusion",
            "no_disclosure_readiness",
            "no_delivery_or_publication_authority",
        ],
    }
    return payload


def _copy_extract_sources(
    *,
    workspace: Path,
    sources_dir: Path,
    sources: list[Path],
    registered: list[dict[str, Any]],
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
        "boundary": "scope_and_source_registration_only",
        "non_claims": [
            "no_automatic_span_extraction",
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
        existing.append(
            {
                "name": record["name"],
                "path": record["path"],
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
                },
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
