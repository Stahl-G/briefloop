from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - test env includes PyYAML
    yaml = None

from multi_agent_brief.cli.main import main


ROOT = Path(__file__).resolve().parent.parent


def _write_public_safe_ledger(path: Path) -> None:
    claims = [
        {
            "claim_id": "SYN_CLAIM_001",
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SYN_SRC_001",
            "evidence_text": "Full synthetic evidence text must not render.",
            "source_url": "https://example.com/exampleco-demo",
            "source_type": "web_search",
            "claim_type": "fact",
            "confidence": "high",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        }
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _enable_source_appendix(config_path: Path) -> None:
    if yaml is None:
        raise AssertionError("PyYAML is required for this E2E test")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    output = config.setdefault("output", {})
    output["path"] = output.get("path") or "output"
    output["formats"] = ["markdown", "source_appendix"]
    output["source_appendix"] = {"enabled": True, "mode": "append"}
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_public_safe_runtime_handoff_control_selection_and_finalize_e2e(tmp_path: Path) -> None:
    workspace = tmp_path / "public-safe-e2e"

    assert main(["init", str(workspace), "--demo", "--force"]) == 0
    _enable_source_appendix(workspace / "config.yaml")

    assert main([
        "run",
        "--workspace",
        str(workspace),
        "--repo-workdir",
        str(ROOT),
        "--runtime",
        "operator",
        "--skip-doctor",
    ]) == 0

    intermediate = workspace / "output" / "intermediate"
    assert (intermediate / "agent_handoff.json").exists()
    assert (intermediate / "workflow_state.json").exists()
    assert (intermediate / "artifact_registry.json").exists()
    assert (intermediate / "audience_profile_snapshot.md").exists()
    assert (intermediate / "orchestrator_control_switchboard.json").exists()

    assert not (intermediate / "claim_ledger.json").exists()
    assert not (workspace / "output" / "brief.md").exists()
    assert not (workspace / "output" / "source_appendix.md").exists()

    assert main([
        "controls",
        "select",
        "--workspace",
        str(workspace),
        "--control",
        "quality_gates",
        "--selection",
        "enable",
        "--reason",
        "Synthetic E2E requires deterministic gates before delivery.",
        "--json",
    ]) == 0
    assert (intermediate / "control_selections.json").exists()
    assert not (intermediate / "quality_gate_report.json").exists()

    _write_public_safe_ledger(intermediate / "claim_ledger.json")
    (intermediate / "audited_brief.md").write_text(
        "# ExampleCo Demo Brief\n\n"
        "ExampleCo opened a public demo facility in June 2026. [src:SYN_CLAIM_001]\n",
        encoding="utf-8",
    )

    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 0

    reader = (workspace / "output" / "brief.md").read_text(encoding="utf-8")
    appendix = (workspace / "output" / "source_appendix.md").read_text(encoding="utf-8")
    finalize_report = json.loads((intermediate / "finalize_report.json").read_text(encoding="utf-8"))

    assert "Source Appendix" in reader
    assert "ExampleCo Opens Demo Facility" in reader
    assert "https://example.com/exampleco-demo" in appendix
    assert "SYN_CLAIM" not in reader
    assert "SYN_SRC" not in reader
    assert "Full synthetic evidence" not in reader
    assert "file://" not in reader
    assert str(tmp_path) not in reader
    assert finalize_report["source_appendix_generation"] == "generated"
    assert finalize_report["source_appendix_source_count"] == 1
