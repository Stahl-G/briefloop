#!/usr/bin/env python3
"""Check the v0.11 product-baseline readiness surface.

This is a pre-release readiness guard. It verifies product-facing CLI entries,
ReportPack defaults, packaged config parity, public documentation boundaries,
and reference-run surface presence. It does not promote wider Product OS
extensions to stable support status and does not run a BriefLoop workspace.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from multi_agent_brief.cli.main import build_parser, main as cli_main  # noqa: E402
from multi_agent_brief.product.report_pack_aliases import (  # noqa: E402
    RECOMMENDED_REPORT_PACK_ENTRIES,
    aliases_for_report_pack,
)

BASELINE_TARGET = "v0.11.0"
README_EN_POINTER = """# BriefLoop English README

The English README has moved to [README.md](README.md).

This file is kept as a compatibility pointer so existing external links to
`README_en.md` do not break.

For the Simplified Chinese README, see [README.zh-CN.md](README.zh-CN.md).
"""
EXPECTED_BASELINE_PRODUCT_ENTRIES = {
    "industry-weekly": "market_weekly",
    "management-monthly": "management_monthly",
    "document-review": "evidence_extract",
}
EXPECTED_EXPERIMENTAL_PRODUCT_ENTRIES = {
    "solar-periodic": "solar_industry_periodic",
}
EXPECTED_PRODUCT_ENTRIES = {
    **EXPECTED_BASELINE_PRODUCT_ENTRIES,
    **EXPECTED_EXPERIMENTAL_PRODUCT_ENTRIES,
}
EXPECTED_LEGACY_ENTRIES = {
    "market-weekly": "market_weekly",
    "evidence-extract": "evidence_extract",
    "solar-industry-periodic": "solar_industry_periodic",
}
STABLE_SCENARIO_PACKS = {
    "market_weekly",
    "management_monthly",
    "evidence_extract",
}
EXPECTED_REPORT_PACK_STATUSES = {
    "market_weekly": "supported",
    "management_monthly": "supported",
    "evidence_extract": "supported",
    "solar_industry_periodic": "experimental",
}
REQUIRED_CONTROL_SPINE = {
    "claim_ledger",
    "artifact_registry",
    "quality_gates",
    "event_log",
    "archive",
    "source_appendix",
    "support_records",
    "human_delivery_approval",
    "frozen_artifact_integrity",
}
REQUIRED_DOC_BOUNDARY_PHRASES = {
    "README.md": [
        "Writer entry:",
        "runtime",
        "supported v0.11 product-baseline surface",
        "Product OS surfaces",
        "do not parse PDFs automatically",
        "prove semantic truth",
        "publish reports",
        "authorize public release",
        "The system does not publish or bypass review",
    ],
    "README_en.md": [
        "English README has moved to [README.md](README.md).",
        "compatibility pointer",
        "README.zh-CN.md",
    ],
    "README.zh-CN.md": [
        "写作入口",
        "v0.11 支持的产品基线 surface",
        "实验性能力",
        "不自动发布报告",
        "不绕过人工审核",
        "不保证来源语义上支持每个子主张",
        "自动解析 PDF",
        "授权公开发布",
        "系统不自动发布、不绕过人",
    ],
    "docs/roadmap.md": [
        "v0.11.0 — Stable Weekly/Monthly Brief Product",
        "no force-deliver path",
        "no automatic public release or external publication command",
        "local studio preview after the cli product path works",
    ],
    "docs/support-matrix.md": [
        "Supported",
        "Experimental",
        "v0.11 product-facing workspace entries",
        "ReportSpec / ReportPack baseline contracts",
        "Wider Product OS extensions",
        "solar-periodic",
        "force-deliver",
        "Quality Panel projection",
    ],
    "docs/features.md": [
        "Current product baseline:",
        "v0.11.9 supported baseline entries",
        "industry-weekly",
        "management-monthly",
        "document-review",
        "Product OS surfaces remain experimental",
        "Traceability, not proof by itself",
    ],
    "docs/features.zh-CN.md": [
        "当前产品基线",
        "v0.11.9 支持的 baseline 入口",
        "industry-weekly",
        "management-monthly",
        "document-review",
        "更宽的 Product OS surface 仍是实验性",
        "不等于语义证明",
    ],
    "docs/packaging-pipx.md": [
        "not a current install instruction",
        "Source-clone setup remains the supported launch path",
        "Do not add `pipx install briefloop`",
        "does not prove semantic truth",
        "does not approve delivery",
    ],
}
REQUIRED_FIRST_USER_DOC_PHRASES = {
    "docs/15-minute-pilot.md": [
        "Fifteen-Minute Pilot",
        "BriefLoop helps you produce briefing packages",
        "traceability and process accountability",
        "not semantic proof",
        "bash scripts/demo.sh",
        "python scripts/demo.py",
        "output/intermediate/quality_panel.html",
        "output/intermediate/quality_summary.md",
        "output/intermediate/claim_ledger.json",
        "does not call an LLM",
        "not a semantic proof engine",
        "not authority to publish",
    ],
    "docs/15-minute-pilot.zh-CN.md": [
        "15 分钟试用",
        "BriefLoop 帮你生成",
        "可追溯性和过程问责",
        "不是语义证明",
        "bash scripts/demo.sh",
        "python scripts/demo.py",
        "output/intermediate/quality_panel.html",
        "output/intermediate/quality_summary.md",
        "output/intermediate/claim_ledger.json",
        "不会调用 LLM",
        "不是发布权限",
    ],
    "docs/getting-started.md": [
        "BriefLoop helps you produce briefing packages",
        "traceability and process accountability",
        "not semantic proof",
        "source .venv/bin/activate",
        "bash scripts/demo.sh",
        "python scripts/demo.py",
        "output/delivery/brief.md",
        "output/intermediate/claim_ledger.json",
        "output/intermediate/quality_summary.md",
        "briefloop new industry-weekly",
        "briefloop run --workspace",
        "does not call a model",
        "does not prove",
    ],
    "docs/weekly-loop.md": [
        "create or select workspace",
        "add sources",
        "run handoff",
        "inspect quality summary",
        "repair or record feedback",
        "deliver by human action",
        "briefloop status --workspace",
        "briefloop feedback ingest",
        "briefloop deliver --workspace",
        "Delivery is human-triggered",
    ],
    "docs/troubleshooting.md": [
        "briefloop status --workspace",
        "Gate Blocked",
        "Missing Sources",
        "Reader-Clean Failure",
        "Stale Or Frozen Artifact Issue",
        "No API Key Or No Runtime",
        "Active Repair",
        "When To Start A New Run Or Workspace",
        "Do not edit",
    ],
}
README_FIRST_SCREEN_REQUIRED_LINKS = [
    "docs/15-minute-pilot.md",
    "docs/getting-started.md",
    "docs/weekly-loop.md",
    "docs/troubleshooting.md",
    "examples/reference-workspaces/industry-weekly-demo/README.md",
]
README_FIRST_SCREEN_FORBIDDEN_LINKS = [
    "docs/features.md",
    "docs/golden-path.md",
    "docs/architecture-status.md",
    "docs/roadmap.md",
]
PIPX_CURRENT_INSTALL_DOCS = [
    "README.md",
    "README.zh-CN.md",
    *REQUIRED_FIRST_USER_DOC_PHRASES.keys(),
]
PIPX_INSTALL_BRIEFLOOP_RE = re.compile(r"\bpipx\s+install\s+briefloop\b", re.IGNORECASE)
PIPX_INSTALL_NEGATING_CONTEXT = (
    "do not",
    "don't",
    "not the launch path",
    "until release notes",
    "不是当前",
    "不要",
    "除非",
)
EXPECTED_SUPPORT_MATRIX_STATUSES = {
    "v0.11 product-facing workspace entries": "Supported",
    "ReportSpec / ReportPack baseline contracts": "Supported",
    "Wider Product OS extensions": "Experimental",
}
REQUIRED_TOPOLOGY_CONVERGENCE_PHRASES = {
    "docs/control-surfaces.md": [
        "Implemented topology satisfaction",
        "default topology mark Screener satisfied by Scout",
        "strict topology keeps Screener independent",
        "candidate_claims.json",
        "screened_candidates.json",
        "not a speed or output-quality claim",
    ],
    "docs/control-surfaces.zh-CN.md": [
        "已实现 topology satisfaction",
        "default topology 可由 Scout 满足 Screener",
        "strict topology 保留独立 Screener",
        "candidate_claims.json",
        "screened_candidates.json",
        "不是速度或输出质量声明",
    ],
}
FORBIDDEN_TOPOLOGY_CONVERGENCE_PHRASES = [
    "Mode registry / role topology | Planned v0.8+",
    "not eligible for v0.11.0 freeze until role convergence has been tested",
    "Role convergence remains v0.8 work",
    "角色收敛通过真实测试前不冻结",
    "角色收敛仍属 v0.8 工作",
]
REQUIRED_GOLDEN_PATH_PHRASES = {
    "docs/golden-path.md": [
        "v0.11 product baseline",
        "industry-weekly",
        "management-monthly",
        "document-review",
        "solar-periodic remains an experimental Product OS extension",
        "not an experiment harness",
        "does not prove semantic truth",
        "does not authorize public release",
        "human delivery",
        "Claim Ledger",
        "quality gates",
        "event logs",
        "briefloop run --workspace",
        "briefloop status --workspace",
        "briefloop deliver --workspace",
        "briefloop feedback ingest",
        "--source human",
        "--feedback",
    ],
    "docs/golden-path.zh-CN.md": [
        "v0.11 产品基线",
        "industry-weekly",
        "management-monthly",
        "document-review",
        "solar-periodic",
        "实验性 Product OS 扩展",
        "不是实验 harness",
        "不证明语义真实性",
        "不授权公开发布",
        "human delivery",
        "Claim Ledger",
        "quality gates",
        "briefloop run --workspace",
        "briefloop status --workspace",
        "briefloop deliver --workspace",
        "briefloop feedback ingest",
        "--source human",
        "--feedback",
    ],
}
FORBIDDEN_GOLDEN_PATH_PHRASES = [
    "experiments 080",
    "score-run",
    "A-controlled",
    "manifestation score",
    "briefloop new solar-periodic",
]
FORBIDDEN_GOLDEN_PATH_SHELL_COMMAND_PATTERNS = [
    ("briefloop run ./", re.compile(r"(?m)^\s*briefloop\s+run\s+\./")),
    ("briefloop status ./", re.compile(r"(?m)^\s*briefloop\s+status\s+\./")),
    ("briefloop deliver ./", re.compile(r"(?m)^\s*briefloop\s+deliver\s+\./")),
    ("briefloop feedback ./", re.compile(r"(?m)^\s*briefloop\s+feedback\s+\./")),
]
FORBIDDEN_PUBLIC_CLAIM_PATTERNS = [
    (
        "proves_truth",
        re.compile(
            r"\b(can|could|will|does|may)\s+prove\s+(semantic\s+)?truth\b"
            r"|\b(proves|proved|proving)\s+(semantic\s+)?truth\b"
            r"|\b(proves|proved|proving)\s+every\s+claims?\s+is\s+true\b"
            r"|\b(can|could|will|does|may)\s+prove\s+every\s+claims?\s+is\s+true\b",
            re.IGNORECASE,
        ),
    ),
    (
        "guarantees_truth",
        re.compile(
            r"\b(guarantees|guaranteed|guaranteeing)\s+(semantic\s+)?truth\b"
            r"|\b(guarantees|guaranteed|guaranteeing)\s+every\s+claims?\s+(is|are)\s+true\b",
            re.IGNORECASE,
        ),
    ),
    (
        "eliminates_hallucinations",
        re.compile(r"\beliminates?\s+hallucinations?\b", re.IGNORECASE),
    ),
    (
        "automatically_ready_to_send",
        re.compile(
            r"\bautomatically\s+ready\s+to\s+send\b"
            r"|\bready\s+to\s+send\s+automatically\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authorize_public_release",
        re.compile(
            r"\b(can|could|will|does|may)\s+authorize\s+public\s+release\b"
            r"|\b(authorizes|authorized|authorizing)\s+public\s+release\b",
            re.IGNORECASE,
        ),
    ),
    (
        "publish_reports_automatically",
        re.compile(
            r"\b(can|could|will|does|may)\s+publish\s+reports?\s+automatically\b"
            r"|\b(publishes|published|publishing)\s+reports?\s+automatically\b"
            r"|\bautomatically\s+publishes?\s+reports?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bypass_review",
        re.compile(
            r"\b(can|could|will|does|may)\s+bypass\s+(human\s+)?review\b"
            r"|\bbypasses\s+(human\s+)?review\b",
            re.IGNORECASE,
        ),
    ),
    (
        "approve_delivery",
        re.compile(
            r"\b(can|could|will|does|may)\s+approve\s+delivery\b"
            r"|\b(approves|approved|approving)\s+delivery\b"
            r"|\bautomatically\s+approves?\s+delivery\b",
            re.IGNORECASE,
        ),
    ),
    (
        "improvement_memory_quality",
        re.compile(
            r"\bImprovement\s+Memory\s+"
            r"(improves|improved|improving|can\s+improve|will\s+improve)\s+"
            r"output\s+quality\b",
            re.IGNORECASE,
        ),
    ),
    (
        "python_semantic_judgment",
        re.compile(
            r"\bPython\s+"
            r"(judges|judged|judging|can\s+judge|will\s+judge)\s+"
            r"(prose\s+quality|semantic\s+manifestation|factual\s+regression)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "support_sufficiency_implemented",
        re.compile(
            r"\b(implements|implemented|implementing|has\s+implemented)\b"
            r"[^.\n]{0,80}\bsupport[- ]sufficiency\b"
            r"|\bsupport[- ]sufficiency\s+structures?\s+(are|is)\s+implemented\b",
            re.IGNORECASE,
        ),
    ),
    (
        "zh_public_overclaim",
        re.compile(
            r"(可以|能|能够|会|将)[^。；\n]{0,16}"
            r"(证明语义真实性|证明语义真理|消除幻觉|自动发布|公开发布|绕过人工审核|绕过审核|授权公开发布)"
        ),
    ),
    ("zh_auto_publish_report", re.compile(r"自动发布报告")),
]
NEGATING_CONTEXT_TOKENS = (
    "does not",
    "do not",
    "cannot",
    "can't",
    "not ",
    "不",
    "不能",
    "不会",
    "不应",
    "不要",
    "不代表",
    "禁止",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    _check_report_pack_configs(checks)
    _check_product_entries(checks)
    _check_packs_cli_surface(checks)
    _check_workspace_creation(checks)
    _check_cli_and_docs_boundaries(checks)
    _check_first_user_docs_surface(checks)
    _check_golden_path_surface(checks)
    _check_support_matrix_alignment(checks)
    _check_topology_convergence_surface(checks)
    _check_reference_run_surface(checks)

    ok = all(item["status"] == "pass" for item in checks)
    payload = {
        "ok": ok,
        "schema_version": "briefloop.product_baseline_check.v1",
        "baseline_target": BASELINE_TARGET,
        "runtime_effect": "readiness_check_only",
        "checks": checks,
        "non_goals": [
            "version_bump",
            "wider_product_os_support_promotion",
            "stage_execution",
            "gate_execution",
            "delivery_approval",
            "semantic_truth_proof",
            "release_authority",
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Product Baseline Readiness Check")
        print("=" * 40)
        for item in checks:
            print(f"  [{item['status'].upper()}] {item['id']}: {item['detail']}")
        print()
        print("ALL CHECKS PASSED." if ok else "FAILED.")
    return 0 if ok else 1


def _check_report_pack_configs(checks: list[dict[str, str]]) -> None:
    root_dir = ROOT / "configs" / "report_packs"
    package_dir = ROOT / "src" / "multi_agent_brief" / "configs" / "report_packs"
    root_packs = _load_yaml_dir(root_dir)
    package_packs = _load_yaml_dir(package_dir)

    expected_ids = set(RECOMMENDED_REPORT_PACK_ENTRIES)
    _append_check(
        checks,
        "report_pack_ids",
        set(root_packs) == expected_ids,
        f"root packs={sorted(root_packs)} expected={sorted(expected_ids)}",
    )
    _append_check(
        checks,
        "report_pack_package_parity",
        root_packs == package_packs,
        "root report_packs match packaged copies",
    )

    stable_missing = sorted(STABLE_SCENARIO_PACKS - set(root_packs))
    _append_check(
        checks,
        "stable_scenario_packs",
        not stable_missing,
        f"stable scenarios={sorted(STABLE_SCENARIO_PACKS)} missing={stable_missing}",
    )

    for pack_id in sorted(STABLE_SCENARIO_PACKS):
        pack = root_packs.get(pack_id) or {}
        spec = pack.get("default_report_spec") if isinstance(pack.get("default_report_spec"), dict) else {}
        outputs = set(spec.get("outputs") or [])
        control_spine = spec.get("control_spine") if isinstance(spec.get("control_spine"), dict) else {}
        spine_missing = sorted(key for key in REQUIRED_CONTROL_SPINE if control_spine.get(key) is not True)
        source_policy = spec.get("source_policy") if isinstance(spec.get("source_policy"), dict) else {}
        _append_check(
            checks,
            f"{pack_id}.markdown_docx_outputs",
            {"markdown", "docx"} <= outputs,
            f"outputs={sorted(outputs)}",
        )
        _append_check(
            checks,
            f"{pack_id}.control_spine",
            not spine_missing,
            f"required control spine missing={spine_missing}",
        )
        _append_check(
            checks,
            f"{pack_id}.no_hidden_crawling",
            source_policy.get("hidden_autonomous_crawling") is False,
            "hidden_autonomous_crawling=false",
        )

    for pack_id, expected_status in sorted(EXPECTED_REPORT_PACK_STATUSES.items()):
        pack = root_packs.get(pack_id) or {}
        _append_check(
            checks,
            f"{pack_id}.status",
            pack.get("status") == expected_status,
            f"status={pack.get('status')} expected={expected_status}",
        )


def _check_product_entries(checks: list[dict[str, str]]) -> None:
    for entry, pack_id in {
        **EXPECTED_BASELINE_PRODUCT_ENTRIES,
        **EXPECTED_EXPERIMENTAL_PRODUCT_ENTRIES,
        **EXPECTED_LEGACY_ENTRIES,
    }.items():
        aliases = aliases_for_report_pack(pack_id)
        _append_check(
            checks,
            f"entry.{entry}",
            entry in aliases,
            f"{entry} resolves to {pack_id}; aliases={aliases}",
        )


def _check_packs_cli_surface(checks: list[dict[str, str]]) -> None:
    list_code, list_payload = _run_cli_json(["packs", "list", "--json"])
    packs = list_payload.get("packs") if isinstance(list_payload.get("packs"), list) else []
    packs_by_id = {
        item.get("pack_id"): item
        for item in packs
        if isinstance(item, dict) and isinstance(item.get("pack_id"), str)
    }
    _append_check(
        checks,
        "packs_list_cli.ok",
        list_code == 0 and list_payload.get("ok") is True,
        f"exit={list_code} ok={list_payload.get('ok')}",
    )
    _append_check(
        checks,
        "packs_list_cli.internal_pack_ids",
        set(packs_by_id) == set(RECOMMENDED_REPORT_PACK_ENTRIES),
        f"pack_ids={sorted(packs_by_id)} expected={sorted(RECOMMENDED_REPORT_PACK_ENTRIES)}",
    )
    missing_entries = []
    missing_aliases = []
    for product_entry, pack_id in EXPECTED_BASELINE_PRODUCT_ENTRIES.items():
        item = packs_by_id.get(pack_id) or {}
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if item.get("recommended_entry") != product_entry:
            missing_entries.append(f"{pack_id}->{product_entry}")
        if product_entry not in aliases:
            missing_aliases.append(f"{pack_id}:{product_entry}")
    for experimental_entry, pack_id in EXPECTED_EXPERIMENTAL_PRODUCT_ENTRIES.items():
        item = packs_by_id.get(pack_id) or {}
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if experimental_entry not in aliases:
            missing_aliases.append(f"{pack_id}:{experimental_entry}")
    for legacy_entry, pack_id in EXPECTED_LEGACY_ENTRIES.items():
        item = packs_by_id.get(pack_id) or {}
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if legacy_entry not in aliases:
            missing_aliases.append(f"{pack_id}:{legacy_entry}")
    _append_check(
        checks,
        "packs_list_cli.product_entries",
        not missing_entries,
        f"recommended entry mismatches={missing_entries}",
    )
    _append_check(
        checks,
        "packs_list_cli.aliases",
        not missing_aliases,
        f"missing aliases={missing_aliases}",
    )
    status_mismatches = []
    for pack_id, expected_status in EXPECTED_REPORT_PACK_STATUSES.items():
        item = packs_by_id.get(pack_id) or {}
        if item.get("status") != expected_status:
            status_mismatches.append(f"{pack_id}:{item.get('status')}!={expected_status}")
    _append_check(
        checks,
        "packs_list_cli.support_statuses",
        not status_mismatches,
        f"status mismatches={status_mismatches}",
    )

    unknown_code, unknown_payload = _run_cli_json(["packs", "show", "unknown-pack", "--json"])
    recommended = unknown_payload.get("recommended_entries")
    internal = unknown_payload.get("internal_pack_ids")
    _append_check(
        checks,
        "packs_unknown_cli.error",
        unknown_code == 1
        and unknown_payload.get("ok") is False
        and "unknown report pack" in str(unknown_payload.get("error") or ""),
        f"exit={unknown_code} ok={unknown_payload.get('ok')} error={unknown_payload.get('error')}",
    )
    _append_check(
        checks,
        "packs_unknown_cli.product_entries",
        recommended == sorted(EXPECTED_PRODUCT_ENTRIES),
        f"recommended_entries={recommended} expected={sorted(EXPECTED_PRODUCT_ENTRIES)}",
    )
    _append_check(
        checks,
        "packs_unknown_cli.internal_pack_ids",
        internal == sorted(RECOMMENDED_REPORT_PACK_ENTRIES),
        f"internal_pack_ids={internal} expected={sorted(RECOMMENDED_REPORT_PACK_ENTRIES)}",
    )


def _check_workspace_creation(checks: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory(prefix="briefloop-product-baseline-") as tmp:
        base = Path(tmp)
        for entry, expected_pack in EXPECTED_BASELINE_PRODUCT_ENTRIES.items():
            workspace = base / entry
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = cli_main(["new", entry, str(workspace)])
            spec_path = workspace / "report_spec.yaml"
            spec = _load_yaml_file(spec_path) if spec_path.exists() else {}
            _append_check(
                checks,
                f"new.{entry}",
                code == 0 and spec.get("report_pack") == expected_pack,
                f"exit={code} report_pack={spec.get('report_pack')} expected={expected_pack}",
            )
            _append_check(
                checks,
                f"new.{entry}.local_first_files",
                all((workspace / name).exists() for name in ("config.yaml", "sources.yaml", "user.md"))
                and (workspace / "input" / "sources").is_dir(),
                "workspace skeleton contains config.yaml, sources.yaml, user.md, input/sources",
            )


def _check_cli_and_docs_boundaries(checks: list[dict[str, str]]) -> None:
    help_text = build_parser().format_help()
    _append_check(
        checks,
        "no_force_deliver_cli",
        "force-deliver" not in help_text and "force deliver" not in help_text.lower(),
        "top-level CLI help does not expose force-deliver",
    )

    public_overclaims: list[str] = []
    for rel_path, phrases in REQUIRED_DOC_BOUNDARY_PHRASES.items():
        raw_text = (ROOT / rel_path).read_text(encoding="utf-8")
        text = raw_text.lower()
        missing = [phrase for phrase in phrases if phrase.lower() not in text]
        _append_check(
            checks,
            f"docs.{rel_path}",
            not missing,
            f"required boundary phrases missing={missing}",
        )
        public_overclaims.extend(_public_overclaim_findings(rel_path, raw_text))
    for rel_path in REQUIRED_FIRST_USER_DOC_PHRASES:
        path = ROOT / rel_path
        if path.exists():
            public_overclaims.extend(
                _public_overclaim_findings(rel_path, path.read_text(encoding="utf-8"))
            )

    readme_en_text = (ROOT / "README_en.md").read_text(encoding="utf-8")
    _append_check(
        checks,
        "docs.README_en.md.pointer_shape",
        readme_en_text.strip() == README_EN_POINTER.strip(),
        "README_en.md must contain only the compatibility pointer",
    )

    _append_check(
        checks,
        "docs.public_claims.no_forbidden_positive_claims",
        not public_overclaims,
        f"forbidden positive claims={public_overclaims}",
    )


def _check_first_user_docs_surface(checks: list[dict[str, str]]) -> None:
    for rel_path, phrases in REQUIRED_FIRST_USER_DOC_PHRASES.items():
        raw_text = (ROOT / rel_path).read_text(encoding="utf-8")
        text = raw_text.lower()
        missing = [phrase for phrase in phrases if phrase.lower() not in text]
        _append_check(
            checks,
            f"first_user_docs.{rel_path}",
            not missing,
            f"required first-user phrases missing={missing}",
        )

    getting_started = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    activation_reason = _unix_source_clone_activation_reason(
        getting_started,
        cli_markers=("multi-agent-brief version",),
    )
    _append_check(
        checks,
        "first_user_docs.docs/getting-started.md.unix_venv_activation",
        activation_reason == "",
        activation_reason or "setup.sh followed by source .venv/bin/activate before CLI verification",
    )

    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_activation_reason = _unix_source_clone_activation_reason(readme_text)
    _append_check(
        checks,
        "first_user_docs.README.md.unix_venv_activation",
        readme_activation_reason == "",
        readme_activation_reason or "setup.sh followed by source .venv/bin/activate before README CLI usage",
    )

    pipx_findings = _current_install_pipx_findings()
    _append_check(
        checks,
        "first_user_docs.no_current_pipx_install",
        not pipx_findings,
        f"forbidden current pipx install instructions={pipx_findings}",
    )

    readme_first_screen = _readme_first_screen_text(readme_text)
    missing_links = [link for link in README_FIRST_SCREEN_REQUIRED_LINKS if link not in readme_first_screen]
    forbidden_links = [link for link in README_FIRST_SCREEN_FORBIDDEN_LINKS if link in readme_first_screen]
    _append_check(
        checks,
        "first_user_docs.README.md.first_screen_links",
        not missing_links and not forbidden_links,
        f"missing_links={missing_links} forbidden_links={forbidden_links}",
    )


def _unix_source_clone_activation_reason(
    text: str,
    *,
    cli_markers: tuple[str, ...] = (
        "multi-agent-brief version",
        "multi-agent-brief onboard",
        "multi-agent-brief init",
        "multi-agent-brief run",
        "briefloop new",
        "briefloop run",
    ),
) -> str:
    setup_index = text.find("bash scripts/setup.sh")
    activation_index = text.find("source .venv/bin/activate")
    cli_indexes = [text.find(marker) for marker in cli_markers if text.find(marker) >= 0]
    missing = []
    if setup_index < 0:
        missing.append("bash scripts/setup.sh")
    if activation_index < 0:
        missing.append("source .venv/bin/activate")
    if not cli_indexes:
        missing.append("Unix CLI usage")
    if missing:
        return f"missing Unix setup marker(s): {missing}"
    first_cli_index = min(cli_indexes)
    if not setup_index < activation_index < first_cli_index:
        return "Unix source-clone path must activate .venv after setup.sh and before CLI usage"
    return ""


def _current_install_pipx_findings() -> list[str]:
    findings: list[str] = []
    for rel_path in PIPX_CURRENT_INSTALL_DOCS:
        path = ROOT / rel_path
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not PIPX_INSTALL_BRIEFLOOP_RE.search(line):
                continue
            context = "\n".join(lines[max(0, index - 1) : min(len(lines), index + 2)]).lower()
            if any(token in context for token in PIPX_INSTALL_NEGATING_CONTEXT):
                continue
            findings.append(f"{rel_path}:{index + 1}:{line.strip()}")
    return findings


def _readme_first_screen_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---" or stripped.startswith("## ✨ In one sentence"):
            break
        lines.append(line)
    return "\n".join(lines)


def _check_support_matrix_alignment(checks: list[dict[str, str]]) -> None:
    text = (ROOT / "docs" / "support-matrix.md").read_text(encoding="utf-8")
    for needle, expected_status in EXPECTED_SUPPORT_MATRIX_STATUSES.items():
        status = _support_matrix_status_for(text, needle)
        _append_check(
            checks,
            f"support_matrix.{_slug(needle)}",
            status == expected_status,
            f"{needle} status={status!r} expected={expected_status!r}",
        )


def _check_topology_convergence_surface(checks: list[dict[str, str]]) -> None:
    for rel_path, phrases in REQUIRED_TOPOLOGY_CONVERGENCE_PHRASES.items():
        raw_text = (ROOT / rel_path).read_text(encoding="utf-8")
        text = raw_text.lower()
        missing = [phrase for phrase in phrases if phrase.lower() not in text]
        forbidden = [
            phrase
            for phrase in FORBIDDEN_TOPOLOGY_CONVERGENCE_PHRASES
            if phrase.lower() in text
        ]
        _append_check(
            checks,
            f"topology_convergence.{rel_path}.required_current_contract",
            not missing,
            f"required phrases missing={missing}",
        )
        _append_check(
            checks,
            f"topology_convergence.{rel_path}.no_stale_planned_wording",
            not forbidden,
            f"stale topology wording={forbidden}",
        )


def _check_golden_path_surface(checks: list[dict[str, str]]) -> None:
    for rel_path, phrases in REQUIRED_GOLDEN_PATH_PHRASES.items():
        path = ROOT / rel_path
        raw_text = path.read_text(encoding="utf-8")
        text = raw_text.lower()
        missing = [phrase for phrase in phrases if phrase.lower() not in text]
        forbidden = [phrase for phrase in FORBIDDEN_GOLDEN_PATH_PHRASES if phrase.lower() in text]
        forbidden.extend(
            label
            for label, pattern in FORBIDDEN_GOLDEN_PATH_SHELL_COMMAND_PATTERNS
            if pattern.search(raw_text)
        )
        _append_check(
            checks,
            f"golden_path.{rel_path}.required_product_entries",
            not missing,
            f"required phrases missing={missing}",
        )
        _append_check(
            checks,
            f"golden_path.{rel_path}.no_experiment_surface",
            not forbidden,
            f"forbidden experiment/product-drift phrases={forbidden}",
        )


def _support_matrix_status_for(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        if not line.startswith("|") or needle not in line:
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) >= 2:
            return columns[-1]
    return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _check_reference_run_surface(checks: list[dict[str, str]]) -> None:
    files = sorted((ROOT / "docs" / "reference-runs").glob("*.md"))
    _append_check(
        checks,
        "reference_run_surface_count",
        len(files) >= 5,
        f"reference-run markdown files={len(files)}",
    )
    unsafe = []
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        if not any(marker in text for marker in ("not proof", "not prove", "not a", "public-safe", "private")):
            unsafe.append(path.name)
    _append_check(
        checks,
        "reference_run_boundary_language",
        not unsafe,
        f"files missing obvious boundary language={unsafe}",
    )


def _load_yaml_dir(path: Path) -> dict[str, dict[str, Any]]:
    packs = {}
    for file_path in sorted(path.glob("*.yaml")):
        payload = _load_yaml_file(file_path)
        pack_id = payload.get("pack_id") if isinstance(payload, dict) else None
        if isinstance(pack_id, str) and pack_id:
            packs[pack_id] = payload
    return packs


def _load_yaml_file(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _run_cli_json(argv: list[str]) -> tuple[int, dict[str, Any]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(argv)
    try:
        payload = json.loads(stdout.getvalue())
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
    return code, payload if isinstance(payload, dict) else {"ok": False, "payload": payload}


def _public_overclaim_findings(rel_path: str, text: str) -> list[str]:
    findings: list[str] = []
    lines = text.splitlines()
    for line_no, line in enumerate(lines, 1):
        for label, pattern in FORBIDDEN_PUBLIC_CLAIM_PATTERNS:
            for match in pattern.finditer(line):
                if _has_negating_context(lines, line_no - 1, match.start()):
                    continue
                findings.append(f"{rel_path}:{line_no}:{label}:{match.group(0)}")
    return findings


def _has_negating_context(lines: list[str], line_idx: int, match_start: int) -> bool:
    line = lines[line_idx]
    prefix = line[max(0, match_start - 32):match_start].lower()
    if any(token in prefix for token in NEGATING_CONTEXT_TOKENS):
        return True
    if not line.lstrip().startswith("-"):
        return False
    for prior in reversed(lines[max(0, line_idx - 4):line_idx]):
        stripped = prior.strip().lower()
        if not stripped:
            continue
        if stripped.startswith("-"):
            continue
        return "not the right tool" in stripped or "not right tool" in stripped
    return False


def _append_check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
    })


if __name__ == "__main__":
    raise SystemExit(main())
