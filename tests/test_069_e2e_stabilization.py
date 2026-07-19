from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - test env includes PyYAML
    yaml = None

from multi_agent_brief.cli.main import main
from multi_agent_brief.control_store import SQLiteControlStore


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


def test_public_safe_runtime_handoff_control_selection_and_finalize_e2e(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "public-safe-e2e"

    assert (
        main(
            [
                "new",
                "industry-weekly",
                str(workspace),
                "--web-search-mode",
                "disabled",
            ]
        )
        == 0
    )
    capsys.readouterr()
    public_input = workspace / "input" / "public-safe-source.md"
    public_input.write_text(
        "ExampleCo opened a synthetic public demo facility in June 2026.\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "run",
                "--workspace",
                str(workspace),
                "--runtime",
                "codex",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["runtime", "next", "--workspace", str(workspace)]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["schema_version"] == "briefloop.core_run_next_action.v2"
    assert action["effect_kind"] == "doctor_check"
    assert action["action_fingerprint"]

    intermediate = workspace / "output" / "intermediate"
    legacy_controls = (
        "agent_handoff.json",
        "runtime_manifest.json",
        "workflow_state.json",
        "artifact_registry.json",
        "event_log.jsonl",
        "finalize_report.json",
    )
    assert all(not (intermediate / name).exists() for name in legacy_controls)
    assert (workspace / "briefloop.db").is_file()
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        before_revision = store.current_revision
        snapshot = store.load_snapshot(action["run_id"])
    assert snapshot.run.runtime == "codex"
    assert snapshot.transactions[-1].committed_revision == before_revision

    database_before = (workspace / "briefloop.db").read_bytes()
    assert main(["finalize", "--config", str(workspace / "config.yaml")]) == 1
    assert capsys.readouterr().out == "runtime_command_unsupported\n"
    assert (workspace / "briefloop.db").read_bytes() == database_before
    assert all(not (intermediate / name).exists() for name in legacy_controls)
