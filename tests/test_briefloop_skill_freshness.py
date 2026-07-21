from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_briefloop_skill_freshness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_briefloop_skill_freshness_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_briefloop_skill_freshness_script_runs_clean() -> None:
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
    assert payload["runtime_effect"] == "readiness_check_only"
    assert checks["canonical.references/version-matrix.md.freshness"]["status"] == "pass"
    assert checks["packaged_codex.SKILL.md.freshness"]["status"] == "pass"
    assert (
        checks["packaged_codex.references/controlstore-v2.md.freshness"]["status"]
        == "pass"
    )
    assert checks["hermes_plugin.briefloop_skill_projection"]["status"] == "pass"


def test_briefloop_skill_freshness_rejects_missing_required_phrase(tmp_path, monkeypatch) -> None:
    module = _load_module()
    canonical = tmp_path / "canonical"
    hermes = tmp_path / "hermes"
    canonical_refs = canonical / "references"
    hermes_refs = hermes / "references"
    canonical_refs.mkdir(parents=True)
    hermes_refs.mkdir(parents=True)

    for rel_path, phrases in module.REQUIRED_REFERENCE_PHRASES.items():
        text = "\n".join(phrases)
        if rel_path == "references/version-matrix.md":
            text = text.replace("Codex is the only active fresh runtime", "")
        target = canonical / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        mirror = hermes / rel_path
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(text, encoding="utf-8")

    monkeypatch.setattr(module, "CANONICAL", canonical)
    monkeypatch.setattr(module, "HERMES_PLUGIN_PROJECTION", hermes)

    checks: list[dict[str, str]] = []
    module._check_required_phrases(checks)
    by_id = {item["id"]: item for item in checks}
    assert by_id["canonical.references/version-matrix.md.freshness"]["status"] == "fail"
    assert "Codex is the only active fresh runtime" in by_id[
        "canonical.references/version-matrix.md.freshness"
    ]["detail"]


def test_briefloop_skill_freshness_rejects_projection_drift(tmp_path, monkeypatch) -> None:
    module = _load_module()
    canonical = tmp_path / "canonical"
    hermes = tmp_path / "hermes"
    canonical.mkdir()
    hermes.mkdir()
    (canonical / "SKILL.md").write_text("canonical\n", encoding="utf-8")
    (hermes / "SKILL.md").write_text("drifted\n", encoding="utf-8")

    monkeypatch.setattr(module, "CANONICAL", canonical)
    monkeypatch.setattr(module, "HERMES_PLUGIN_PROJECTION", hermes)

    checks: list[dict[str, str]] = []
    module._check_projection_parity(checks)
    assert checks == [{
        "id": "hermes_plugin.briefloop_skill_projection",
        "status": "fail",
        "detail": f"differs: {hermes / 'SKILL.md'}",
    }]


def test_briefloop_skill_contract_and_runtime_asset_parity_run_clean() -> None:
    for relative in (
        "scripts/check_skill_contract.py",
        "scripts/check_runtime_asset_parity.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / relative)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
