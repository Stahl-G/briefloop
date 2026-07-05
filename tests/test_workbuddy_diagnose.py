"""Tests for WorkBuddy read-only diagnosis Run Card."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "output" / "intermediate").mkdir(parents=True)
    (ws / "config.yaml").write_text("project:\n  name: Test\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  profile: conservative\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  enabled: true\n"
        "  sources:\n"
        "    - name: Local\n"
        "      path: input/\n",
        encoding="utf-8",
    )
    return ws


def test_workbuddy_diagnose_json_reports_run_card_and_secret_risk(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)
    secret_value = "tvly-secret-value"
    (ws / ".env").write_text(f"TAVILY_API_KEY={secret_value}\n", encoding="utf-8")
    (ws / "output" / "intermediate" / "runtime_manifest.json").write_text(
        json.dumps({"runtime": "codebuddy", "runtime_capabilities": {"delegation_supported": True}}),
        encoding="utf-8",
    )
    (ws / "output" / "intermediate" / "workflow_state.json").write_text(
        json.dumps(
            {
                "current_stage": "finalize",
                "blocked": False,
                "run_integrity": {"status": "contaminated"},
            }
        ),
        encoding="utf-8",
    )
    (ws / "output" / "intermediate" / "artifact_registry.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "audited_brief": {"status": "valid", "validation_result": "valid_minimum"},
                    "input_classification": {
                        "status": "invalid",
                        "validation_result": "input_classification_schema_error:context[0].path",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws), "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    assert secret_value not in raw
    payload = json.loads(raw)
    assert payload["schema_version"] == "briefloop.workbuddy_diagnose.v1"
    assert payload["runtime_effect"] == "read_only_diagnostic"
    assert payload["run_card"] == {
        "runtime": "codebuddy",
        "current_stage": "finalize",
        "run_integrity": "contaminated",
        "blocked": False,
        "latest_gate_status": "unknown",
        "finalize_report": "missing",
        "delivery_dir": "missing",
        "next_allowed_action": "stop_run_integrity_not_clean",
    }
    assert payload["secret_risk"]["env_present"] is True
    assert payload["secret_risk"]["nonempty_env_keys"] == ["TAVILY_API_KEY"]
    assert payload["secret_risk"]["secret_values_reported"] is False
    assert payload["secret_risk"]["share_workspace_zip_allowed"] is False
    assert payload["artifacts"]["invalid_or_stale"] == [
        {
            "artifact_id": "input_classification",
            "status": "invalid",
            "validation_result": "input_classification_schema_error:context[0].path",
        }
    ]
    assert payload["finalize"]["exists"] is False
    assert payload["delivery"]["exists"] is False


def test_workbuddy_diagnose_text_prints_run_card_without_delivery_claim(
    tmp_path: Path,
    capsys,
) -> None:
    ws = _workspace(tmp_path)

    rc = main(["workbuddy", "diagnose", "--workspace", str(ws)])

    assert rc == 0
    output = capsys.readouterr().out
    for field in (
        "runtime:",
        "current_stage:",
        "run_integrity:",
        "blocked:",
        "latest_gate_status:",
        "finalize_report:",
        "delivery_dir:",
        "next_allowed_action:",
    ):
        assert field in output
    assert "delivery complete" not in output.lower()
    assert "delivered" not in output.lower()
    assert "read_only_workbuddy_run_card_not_gate_delivery_release_or_semantic_proof" in output
