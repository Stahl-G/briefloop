from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.experiments import scaffold_condition, validate_case_dir


ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = ROOT / "experiments" / "080" / "cases" / "solar_public_001"
PUBLIC_SAFETY_SCRIPT = ROOT / "scripts" / "check_public_safety.py"


def _write_scaffold_workspace(ws: Path) -> None:
    ws.mkdir(parents=True)
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        "project:\n"
        "  name: \"080 Public Pilot Condition Workspace\"\n"
        "language:\n"
        "  interface: \"en-US\"\n"
        "  output: \"en-US\"\n"
        "  source_handling: \"preserve_original\"\n"
        "input:\n"
        "  path: \"input\"\n"
        "output:\n"
        "  path: \"output\"\n"
        "report:\n"
        "  title: \"Synthetic Solar Fixture Brief\"\n"
        "  date: \"2026-06-18\"\n"
        "  max_source_age_days: 14\n"
        "  fail_on_stale_source: false\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# User\n\nPrepare a concise synthetic solar fixture brief.\n", encoding="utf-8")
    (ws / "audience_profile.md").write_text("# Audience\n\nManagement-facing public fixture reader.\n", encoding="utf-8")


def _load_public_safety_module():
    spec = importlib.util.spec_from_file_location("check_public_safety_pilot_test", PUBLIC_SAFETY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_080_public_pilot_case_validates():
    result = validate_case_dir(CASE_DIR)

    assert result["ok"] is True
    assert result["case_id"] == "solar_public_001"
    assert result["errors"] == []


def test_080_public_pilot_case_contains_no_completed_condition_results():
    result_files = [
        path.relative_to(CASE_DIR).as_posix()
        for path in CASE_DIR.rglob("*.json")
        if any(token in path.name for token in ("run_record", "scorecard", "case_summary"))
    ]

    assert result_files == []


def test_080_public_pilot_case_passes_public_safety_scan():
    module = _load_public_safety_module()

    assert module.scan([CASE_DIR], banned_terms=[]) == []


def test_080_public_pilot_seed_archive_scaffolds_baseline(tmp_path):
    ws = tmp_path / "baseline-workspace"
    _write_scaffold_workspace(ws)

    payload = scaffold_condition(
        case_dir=CASE_DIR,
        condition="baseline",
        workspace=ws,
        runtime="operator",
        repo_workdir=ROOT,
    )

    assert payload["ok"] is True
    assert payload["case_id"] == "solar_public_001"
    assert payload["condition"] == "baseline"
    assert payload["metadata"]["treatment"]["improvement_memory"] == "disabled"
    assert payload["fact_layer_import"]["source_run_id"] == "mabw-20260618T000000Z-solarseed0001"
    assert (ws / "input" / "sources" / "source-001.md").exists()
    assert (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "delivery" / "brief.md").exists()


def test_080_public_pilot_scaffold_condition_cli_is_retired(tmp_path, capsys):
    """Retired public scaffold-condition CLI: typed rejection, zero writes."""
    ws = tmp_path / "baseline-workspace"
    _write_scaffold_workspace(ws)
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main([
        "experiments",
        "080",
        "scaffold-condition",
        "--case",
        str(CASE_DIR),
        "--condition",
        "baseline",
        "--workspace",
        str(ws),
        "--runtime",
        "operator",
        "--repo-workdir",
        str(ROOT),
        "--json",
    ])

    # retired public `experiments 080 scaffold-condition` CLI
    # surface; the deterministic scaffold_condition seam keeps the scaffold
    # invariant in test_080_public_pilot_seed_archive_scaffolds_baseline.
    assert rc == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
