from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent_brief.orchestrator.runtime_state import (
    E_FACT_LAYER_IMPORT_INVALID,
    E_STAGE_MISMATCH,
    RuntimeStateError,
    complete_finalize_transaction,
    import_fact_layer_transaction,
    show_runtime_state,
)


ROOT = Path(__file__).resolve().parent.parent
CLEAN_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_clean_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-public0001"
    / "manifest.json"
)
SOURCE_PLAN_ONLY_FIXTURE_MANIFEST = (
    ROOT
    / "tests"
    / "fixtures"
    / "fast_rerun_source_candidates_only_archive"
    / "output"
    / "runs"
    / "mabw-20260614T000000Z-planonly0001"
    / "manifest.json"
)


def _write_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "config.yaml").write_text(
        """
project:
  name: "Public Fast Rerun Fixture Test"
report:
  date: "2026-06-20"
  max_source_age_days: 14
  fail_on_stale_source: true
input:
  path: "input"
output:
  path: "output"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text("manual:\n  sources: []\n", encoding="utf-8")
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    return ws


def test_public_fast_rerun_fixture_imports_without_delivery(tmp_path):
    ws = _write_workspace(tmp_path)

    state = import_fact_layer_transaction(
        workspace=ws,
        archive=CLEAN_FIXTURE_MANIFEST,
        runtime="codex",
        repo_workdir=ROOT,
    )

    assert state["manifest"]["recipe"] == "fast-rerun"
    assert state["workflow_state"]["current_stage"] == "analyst"
    assert show_runtime_state(workspace=ws)["fact_layer_import"]["status"] == "valid"
    assert (ws / "input" / "sources" / "source-001.md").exists()
    assert (ws / "output" / "input_classification.json").exists()
    assert (ws / "output" / "intermediate" / "candidate_claims.json").exists()
    assert (ws / "output" / "intermediate" / "screened_candidates.json").exists()
    assert (ws / "output" / "intermediate" / "claim_ledger.json").exists()
    assert not (ws / "output" / "delivery" / "brief.md").exists()


def test_public_fast_rerun_fixture_cannot_finalize_without_downstream_work(tmp_path):
    ws = _write_workspace(tmp_path)
    import_fact_layer_transaction(
        workspace=ws,
        archive=CLEAN_FIXTURE_MANIFEST,
        runtime="codex",
        repo_workdir=ROOT,
    )

    with pytest.raises(RuntimeStateError) as excinfo:
        complete_finalize_transaction(
            workspace=ws,
            repo_workdir=ROOT,
            reason="fixture import alone is not finalization",
        )

    assert excinfo.value.error_code == E_STAGE_MISMATCH
    assert show_runtime_state(workspace=ws)["workflow_state"]["current_stage"] == "analyst"
    assert not (ws / "output" / "delivery" / "brief.md").exists()


def test_public_source_candidates_only_fixture_rejects_import(tmp_path):
    ws = _write_workspace(tmp_path)

    with pytest.raises(RuntimeStateError) as excinfo:
        import_fact_layer_transaction(
            workspace=ws,
            archive=SOURCE_PLAN_ONLY_FIXTURE_MANIFEST,
            runtime="codex",
            repo_workdir=ROOT,
        )

    assert excinfo.value.error_code == E_FACT_LAYER_IMPORT_INVALID
    assert "source_candidates" in str(excinfo.value)
    assert not (ws / "source_candidates.yaml").exists()
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
