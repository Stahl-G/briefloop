"""Tests for the v0.11 product-baseline readiness guard."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_product_baseline.py"


def _load_product_baseline_module():
    spec = importlib.util.spec_from_file_location("check_product_baseline_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_baseline_check_runs_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Product Baseline Readiness Check" in result.stdout
    assert "ALL CHECKS PASSED" in result.stdout


def test_product_baseline_json_locks_v011_entrypoints_and_boundaries() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    checks = {item["id"]: item for item in payload["checks"]}

    assert payload["ok"] is True
    assert payload["baseline_target"] == "v0.11.0"
    assert payload["runtime_effect"] == "readiness_check_only"
    assert "support_status_promotion" in payload["non_goals"]
    assert "release_authority" in payload["non_goals"]
    assert checks["docs.README_en.md.current_release_baseline"]["status"] == "pass"
    assert checks["new.industry-weekly"]["status"] == "pass"
    assert "report_pack=market_weekly" in checks["new.industry-weekly"]["detail"]
    assert checks["new.management-monthly"]["status"] == "pass"
    assert "report_pack=management_monthly" in checks["new.management-monthly"]["detail"]
    assert checks["new.document-review"]["status"] == "pass"
    assert "report_pack=evidence_extract" in checks["new.document-review"]["detail"]
    assert checks["entry.market-weekly"]["status"] == "pass"
    assert checks["entry.evidence-extract"]["status"] == "pass"
    assert checks["packs_list_cli.ok"]["status"] == "pass"
    assert checks["packs_list_cli.product_entries"]["status"] == "pass"
    assert checks["packs_list_cli.aliases"]["status"] == "pass"
    assert checks["packs_unknown_cli.error"]["status"] == "pass"
    assert checks["packs_unknown_cli.product_entries"]["status"] == "pass"
    assert checks["packs_unknown_cli.internal_pack_ids"]["status"] == "pass"
    assert checks["no_force_deliver_cli"]["status"] == "pass"
    assert checks["reference_run_surface_count"]["status"] == "pass"


def test_current_release_baseline_parser_rejects_stale_boundary() -> None:
    module = _load_product_baseline_module()

    stale = "# BriefLoop\n\nCurrent release baseline: v0.10.7\n\n- v0.11.0 roadmap mention\n"

    assert module._extract_current_release_baseline(stale) == "v0.10.7"
    assert module._extract_current_release_baseline(stale) != module.BASELINE_TARGET
