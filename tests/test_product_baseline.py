"""Tests for the v0.11 product-baseline readiness guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_product_baseline.py"


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
    assert "wider_product_os_support_promotion" in payload["non_goals"]
    assert "release_authority" in payload["non_goals"]
    assert checks["docs.README.md"]["status"] == "pass"
    assert checks["docs.README_en.md"]["status"] == "pass"
    assert checks["docs.README.zh-CN.md"]["status"] == "pass"
    assert checks["docs.docs/packaging-pipx.md"]["status"] == "pass"
    assert checks["docs.README_en.md.pointer_shape"]["status"] == "pass"
    assert checks["new.industry-weekly"]["status"] == "pass"
    assert "report_pack=market_weekly" in checks["new.industry-weekly"]["detail"]
    assert checks["new.management-monthly"]["status"] == "pass"
    assert (
        "report_pack=management_monthly" in checks["new.management-monthly"]["detail"]
    )
    assert checks["new.document-review"]["status"] == "pass"
    assert "report_pack=evidence_extract" in checks["new.document-review"]["detail"]
    assert "new.solar-periodic" not in checks
    assert checks["entry.solar-periodic"]["status"] == "pass"
    assert checks["entry.market-weekly"]["status"] == "pass"
    assert checks["entry.evidence-extract"]["status"] == "pass"
    assert checks["packs_list_cli.ok"]["status"] == "pass"
    assert checks["packs_list_cli.product_entries"]["status"] == "pass"
    assert checks["packs_list_cli.aliases"]["status"] == "pass"
    assert checks["packs_list_cli.support_statuses"]["status"] == "pass"
    assert checks["market_weekly.status"]["status"] == "pass"
    assert checks["management_monthly.status"]["status"] == "pass"
    assert checks["evidence_extract.status"]["status"] == "pass"
    assert checks["solar_industry_periodic.status"]["status"] == "pass"
    assert checks["packs_unknown_cli.error"]["status"] == "pass"
    assert checks["packs_unknown_cli.product_entries"]["status"] == "pass"
    assert checks["packs_unknown_cli.internal_pack_ids"]["status"] == "pass"
    assert checks["no_force_deliver_cli"]["status"] == "pass"
    assert checks["docs.public_claims.no_forbidden_positive_claims"]["status"] == "pass"
    assert checks["first_user_docs.docs/15-minute-pilot.md"]["status"] == "pass"
    assert checks["first_user_docs.docs/15-minute-pilot.zh-CN.md"]["status"] == "pass"
    assert checks["first_user_docs.docs/getting-started.md"]["status"] == "pass"
    assert (
        checks["first_user_docs.docs/getting-started.md.unix_venv_activation"]["status"]
        == "pass"
    )
    assert checks["first_user_docs.README.md.unix_venv_activation"]["status"] == "pass"
    assert checks["first_user_docs.no_current_pipx_install"]["status"] == "pass"
    assert (
        checks["first_user_docs.no_archived_experiment_namespace"]["status"] == "pass"
    )
    assert checks["first_user_docs.docs/weekly-loop.md"]["status"] == "pass"
    assert checks["first_user_docs.docs/troubleshooting.md"]["status"] == "pass"
    assert checks["first_user_docs.README.md.first_screen_links"]["status"] == "pass"
    assert checks["first_user_docs.README.md.three_page_block"]["status"] == "pass"
    assert (
        checks["first_user_docs.README.zh-CN.md.three_page_block"]["status"] == "pass"
    )
    assert checks["first_user_route.README.md"]["status"] == "pass"
    assert checks["first_user_route.README.zh-CN.md"]["status"] == "pass"
    assert checks["first_user_route.docs/getting-started.md"]["status"] == "pass"
    assert checks["first_user_route.docs/weekly-loop.md"]["status"] == "pass"
    assert (
        checks["support_matrix.v0_11_product_facing_workspace_entries"]["status"]
        == "pass"
    )
    assert (
        checks["support_matrix.reportspec_reportpack_baseline_contracts"]["status"]
        == "pass"
    )
    assert checks["support_matrix.wider_product_os_extensions"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.md.required_product_entries"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.md.no_experiment_surface"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.zh-CN.md.required_product_entries"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.zh-CN.md.no_experiment_surface"]["status"] == "pass"
    assert checks["reference_run_surface_count"]["status"] == "pass"
    assert checks["reference_run_archived_experiment_framing"]["status"] == "pass"
    readme_en = (ROOT / "README_en.md").read_text(encoding="utf-8")
    assert "English README has moved to [README.md](README.md)." in readme_en
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "[15 分钟试用](docs/15-minute-pilot.zh-CN.md)" in readme_zh
