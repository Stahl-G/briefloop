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
    assert "wider_product_os_support_promotion" in payload["non_goals"]
    assert "release_authority" in payload["non_goals"]
    assert checks["docs.README.md"]["status"] == "pass"
    assert checks["docs.README_en.md"]["status"] == "pass"
    assert checks["docs.README.zh-CN.md"]["status"] == "pass"
    assert checks["docs.README_en.md.pointer_shape"]["status"] == "pass"
    assert checks["new.industry-weekly"]["status"] == "pass"
    assert "report_pack=market_weekly" in checks["new.industry-weekly"]["detail"]
    assert checks["new.management-monthly"]["status"] == "pass"
    assert "report_pack=management_monthly" in checks["new.management-monthly"]["detail"]
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
    assert checks["first_user_docs.docs/getting-started.md"]["status"] == "pass"
    assert checks["first_user_docs.docs/weekly-loop.md"]["status"] == "pass"
    assert checks["first_user_docs.docs/troubleshooting.md"]["status"] == "pass"
    assert checks["first_user_docs.README.md.first_screen_links"]["status"] == "pass"
    assert checks["support_matrix.v0_11_product_facing_workspace_entries"]["status"] == "pass"
    assert checks["support_matrix.reportspec_reportpack_baseline_contracts"]["status"] == "pass"
    assert checks["support_matrix.wider_product_os_extensions"]["status"] == "pass"
    assert (
        checks["topology_convergence.docs/control-surfaces.md.required_current_contract"]["status"]
        == "pass"
    )
    assert (
        checks["topology_convergence.docs/control-surfaces.md.no_stale_planned_wording"]["status"]
        == "pass"
    )
    assert (
        checks["topology_convergence.docs/control-surfaces.zh-CN.md.required_current_contract"]["status"]
        == "pass"
    )
    assert (
        checks["topology_convergence.docs/control-surfaces.zh-CN.md.no_stale_planned_wording"]["status"]
        == "pass"
    )
    assert checks["golden_path.docs/golden-path.md.required_product_entries"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.md.no_experiment_surface"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.zh-CN.md.required_product_entries"]["status"] == "pass"
    assert checks["golden_path.docs/golden-path.zh-CN.md.no_experiment_surface"]["status"] == "pass"
    assert checks["reference_run_surface_count"]["status"] == "pass"
    readme_en = (ROOT / "README_en.md").read_text(encoding="utf-8")
    assert "English README has moved to [README.md](README.md)." in readme_en


def test_first_user_docs_guard_rejects_architecture_first_readme_links(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_FIRST_USER_DOC_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(phrases), encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[Getting Started](docs/getting-started.md) · "
        "[Weekly Loop](docs/weekly-loop.md) · "
        "[Troubleshooting](docs/troubleshooting.md) · "
        "[Reference Workspace](examples/reference-workspaces/industry-weekly-demo/README.md)\n"
        "[Architecture Status](docs/architecture-status.md)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_first_user_docs_surface(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["first_user_docs.docs/getting-started.md"]["status"] == "pass"
    assert checks_by_id["first_user_docs.docs/weekly-loop.md"]["status"] == "pass"
    assert checks_by_id["first_user_docs.docs/troubleshooting.md"]["status"] == "pass"
    readme_check = checks_by_id["first_user_docs.README.md.first_screen_links"]
    assert readme_check["status"] == "fail"
    assert "docs/architecture-status.md" in readme_check["detail"]


def test_first_user_docs_overclaims_fail_public_claim_scan(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_DOC_BOUNDARY_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "README_en.md":
            text = module.README_EN_POINTER
        path.write_text(text, encoding="utf-8")
    for rel_path, phrases in module.REQUIRED_FIRST_USER_DOC_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "docs/getting-started.md":
            text += "\nBriefLoop proves every claim is true.\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_cli_and_docs_boundaries(checks)
    checks_by_id = {item["id"]: item for item in checks}

    overclaim_check = checks_by_id["docs.public_claims.no_forbidden_positive_claims"]
    assert overclaim_check["status"] == "fail"
    assert "docs/getting-started.md:" in overclaim_check["detail"]
    assert "proves_truth" in overclaim_check["detail"]
    assert "proves every claim is true" in overclaim_check["detail"]


def test_golden_path_guard_rejects_experiment_surface_drift(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_GOLDEN_PATH_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "docs/golden-path.md":
            text += "\nUse experiments 080 score-run for the product golden path.\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_golden_path_surface(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["golden_path.docs/golden-path.md.required_product_entries"]["status"] == "pass"
    drift_check = checks_by_id["golden_path.docs/golden-path.md.no_experiment_surface"]
    assert drift_check["status"] == "fail"
    assert "experiments 080" in drift_check["detail"]
    assert "score-run" in drift_check["detail"]
    assert checks_by_id["golden_path.docs/golden-path.zh-CN.md.no_experiment_surface"]["status"] == "pass"


def test_golden_path_guard_rejects_non_executable_shell_shorthand(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_GOLDEN_PATH_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "docs/golden-path.md":
            text += "\nbriefloop run ./weekly-brief\nbriefloop feedback ./weekly-brief \"Fix this.\"\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_golden_path_surface(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["golden_path.docs/golden-path.md.required_product_entries"]["status"] == "pass"
    command_check = checks_by_id["golden_path.docs/golden-path.md.no_experiment_surface"]
    assert command_check["status"] == "fail"
    assert "briefloop run ./" in command_check["detail"]
    assert "briefloop feedback ./" in command_check["detail"]


def test_golden_path_guard_allows_slash_command_workspace_shorthand(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_GOLDEN_PATH_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "docs/golden-path.md":
            text += "\n/briefloop run ./weekly-brief\n/briefloop status ./weekly-brief\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_golden_path_surface(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["golden_path.docs/golden-path.md.required_product_entries"]["status"] == "pass"
    assert checks_by_id["golden_path.docs/golden-path.md.no_experiment_surface"]["status"] == "pass"


def test_support_matrix_alignment_rejects_product_os_overpromotion(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    support_matrix = tmp_path / "docs" / "support-matrix.md"
    support_matrix.parent.mkdir(parents=True, exist_ok=True)
    support_matrix.write_text(
        "| Capability | Status |\n"
        "|---|---|\n"
        "| v0.11 product-facing workspace entries | Supported |\n"
        "| ReportSpec / ReportPack baseline contracts | Supported |\n"
        "| Wider Product OS extensions | Supported |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_support_matrix_alignment(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["support_matrix.v0_11_product_facing_workspace_entries"]["status"] == "pass"
    assert checks_by_id["support_matrix.reportspec_reportpack_baseline_contracts"]["status"] == "pass"
    extension_check = checks_by_id["support_matrix.wider_product_os_extensions"]
    assert extension_check["status"] == "fail"
    assert "expected='Experimental'" in extension_check["detail"]


def test_topology_convergence_guard_rejects_stale_planned_wording(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_TOPOLOGY_CONVERGENCE_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "docs/control-surfaces.md":
            text += (
                "\n| Mode registry / role topology | Planned v0.8+; not eligible "
                "for v0.11.0 freeze until role convergence has been tested. |\n"
            )
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_topology_convergence_surface(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert (
        checks_by_id["topology_convergence.docs/control-surfaces.md.required_current_contract"]["status"]
        == "pass"
    )
    stale_check = checks_by_id["topology_convergence.docs/control-surfaces.md.no_stale_planned_wording"]
    assert stale_check["status"] == "fail"
    assert "Planned v0.8+" in stale_check["detail"]
    assert (
        checks_by_id["topology_convergence.docs/control-surfaces.zh-CN.md.no_stale_planned_wording"]["status"]
        == "pass"
    )


def test_public_overclaim_detector_rejects_contradictory_readme_claims() -> None:
    module = _load_product_baseline_module()

    findings = module._public_overclaim_findings(
        "README.md",
        "BriefLoop proves semantic truth and can authorize public release.\n"
        "BriefLoop can prove semantic truth for every claim.\n"
        "BriefLoop proves truth.\n"
        "BriefLoop can prove truth.\n"
        "BriefLoop proves every claim is true.\n"
        "BriefLoop guarantees every claim is true.\n"
        "BriefLoop publishes reports automatically.\n"
        "BriefLoop automatically approves delivery.\n"
        "Improvement Memory improves output quality.\n"
        "Python judges semantic manifestation.\n"
        "BriefLoop implements Claim-Support Matrix support sufficiency.\n"
        "It eliminates hallucinations and is automatically ready to send.\n",
    )

    assert any("proves_truth" in finding for finding in findings)
    assert any("proves truth" in finding for finding in findings)
    assert any("can prove truth" in finding for finding in findings)
    assert any("proves every claim is true" in finding for finding in findings)
    assert any("guarantees_truth" in finding for finding in findings)
    assert any("guarantees every claim is true" in finding for finding in findings)
    assert any("publishes reports automatically" in finding for finding in findings)
    assert any("approve_delivery" in finding for finding in findings)
    assert any("improvement_memory_quality" in finding for finding in findings)
    assert any("python_semantic_judgment" in finding for finding in findings)
    assert any("support_sufficiency_implemented" in finding for finding in findings)
    assert any("authorize_public_release" in finding for finding in findings)
    assert any("eliminates_hallucinations" in finding for finding in findings)
    assert any("automatically_ready_to_send" in finding for finding in findings)


def test_public_overclaim_guard_fails_doc_boundary_check(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_DOC_BOUNDARY_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "README_en.md":
            text = module.README_EN_POINTER
        if rel_path == "README.md":
            text += "\nBriefLoop proves truth and can authorize public release.\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_cli_and_docs_boundaries(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["docs.README.md"]["status"] == "pass"
    overclaim_check = checks_by_id["docs.public_claims.no_forbidden_positive_claims"]
    assert overclaim_check["status"] == "fail"
    assert "proves_truth" in overclaim_check["detail"]
    assert "authorize_public_release" in overclaim_check["detail"]


def test_readme_en_pointer_shape_rejects_extra_legacy_body(tmp_path, monkeypatch) -> None:
    module = _load_product_baseline_module()
    for rel_path, phrases in module.REQUIRED_DOC_BOUNDARY_PHRASES.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(phrases)
        if rel_path == "README_en.md":
            text = module.README_EN_POINTER + "\nCurrent version: **v0.1.0**\nOld README body.\n"
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    checks: list[dict[str, str]] = []
    module._check_cli_and_docs_boundaries(checks)
    checks_by_id = {item["id"]: item for item in checks}

    assert checks_by_id["docs.README_en.md"]["status"] == "pass"
    pointer_shape = checks_by_id["docs.README_en.md.pointer_shape"]
    assert pointer_shape["status"] == "fail"


def test_public_overclaim_detector_rejects_chinese_positive_claims() -> None:
    module = _load_product_baseline_module()

    findings = module._public_overclaim_findings(
        "README.zh-CN.md",
        "BriefLoop 可以证明语义真实性并授权公开发布。\n"
        "系统会自动发布报告并绕过人工审核。\n",
    )

    assert any("zh_public_overclaim" in finding for finding in findings)
    assert any("zh_auto_publish_report" in finding for finding in findings)


def test_public_overclaim_detector_allows_negative_boundary_language() -> None:
    module = _load_product_baseline_module()

    findings = module._public_overclaim_findings(
        "README.md",
        "BriefLoop does not prove truth, prove semantic truth, publish reports automatically, "
        "approve delivery, authorize public release, implement support-sufficiency structures, "
        "or judge semantic manifestation.\n"
        "Improvement Memory does not improve output quality as a general fact.\n",
    )
    bullet_findings = module._public_overclaim_findings(
        "README.md",
        "It is not the right tool if you only want:\n\n"
        "- a system that proves every claim is true;\n",
    )
    zh_findings = module._public_overclaim_findings(
        "README.zh-CN.md",
        "BriefLoop 不自动发布报告，不绕过人工审核，也不代表系统能证明语义真实性。\n",
    )

    assert findings == []
    assert bullet_findings == []
    assert zh_findings == []


def test_public_overclaim_detector_does_not_treat_without_as_negation() -> None:
    module = _load_product_baseline_module()

    findings = module._public_overclaim_findings(
        "README.md",
        "Without human review, BriefLoop can publish reports automatically.\n",
    )

    assert any("publish_reports_automatically" in finding for finding in findings)
