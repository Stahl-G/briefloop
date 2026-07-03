from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.improvement.contract import canonical_json, revision_sha256
from multi_agent_brief.improvement.memory import (
    IMPROVEMENT_MEMORY_FILE,
    IMPROVEMENT_MEMORY_SCHEMA,
    IMPROVEMENT_MEMORY_SNAPSHOT_FILE,
    freeze_improvement_memory_for_run,
    rebuild_improvement_memory,
    sha256_file,
)
from multi_agent_brief.improvement.state import (
    ImprovementLedgerError,
    approve_improvement,
    improvement_ledger_path,
    propose_improvement,
    revert_improvement,
)
from multi_agent_brief.orchestrator.runtime_state import initialize_runtime_state
from tests.helpers import write_minimal_workspace_under


ROOT = Path(__file__).resolve().parent.parent


_workspace = partial(
    write_minimal_workspace_under,
    project_name="Improvement Memory Test",
    user_text="# User\n\nNeed concise management guidance.\n",
    include_input_dir=True,
    input_path="input",
    output_path="output",
)


def _propose_and_approve(
    ws: Path,
    *,
    guidance: str = "Lead with the decision-relevant number when evidence supports it.",
    category: str = "audience_mismatch",
    scope: str = "brief",
    source_summary: str = "RAW SECRET SUMMARY SHOULD NOT APPEAR.",
    supersedes: str | None = None,
) -> str:
    state = propose_improvement(
        workspace=ws,
        guidance=guidance,
        category=category,
        scope=scope,
        source_summary=source_summary,
        supersedes=supersedes,
    )
    entry_id = str(state["entry"]["entry_id"])
    approve_improvement(workspace=ws, entry_id=entry_id, approved_by="stahl")
    return entry_id


def _valid_revision(
    *,
    entry_id: str,
    status: str = "proposed",
    revision: int = 1,
    previous_revision_sha256=None,
) -> dict:
    payload = {
        "schema_version": "multi-agent-brief-improvement-ledger/v1",
        "entry_id": entry_id,
        "revision": revision,
        "previous_revision_sha256": previous_revision_sha256,
        "created_at": "2026-06-10T00:00:00Z",
        "status": status,
        "level": 2,
        "target_kind": "audience_guidance",
        "change": {
            "category": "audience_mismatch",
            "scope": "brief",
            "guidance_text": "Keep delivery-format defects out of audience memory.",
        },
        "source_evidence": [{
            "source_type": "feedback_issue",
            "summary": "Raw format finding should not appear.",
            "run_id": "mabw-legacy-run",
            "issue_id": "fi-format",
            "origin": {
                "finding_type": "format_field_missing",
                "issue_category": "format_field_missing",
                "issue_source": "audit",
                "control_file": "audit_report.json",
            },
        }],
    }
    if status == "approved":
        payload["approved_by"] = "stahl"
        payload["approved_at"] = "2026-06-10T00:01:00Z"
    return payload


def _append_non_materializable_approved_entry(
    ws: Path,
    *,
    entry_id: str = "AG-0001",
    supersedes_id: str | None = None,
) -> None:
    first = _valid_revision(entry_id=entry_id)
    first["supersedes_id"] = supersedes_id
    second = _valid_revision(
        entry_id=entry_id,
        status="approved",
        revision=2,
        previous_revision_sha256=revision_sha256(first),
    )
    second["supersedes_id"] = supersedes_id
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(first))
        handle.write("\n")
        handle.write(canonical_json(second))
        handle.write("\n")


def test_state_check_strict_preserves_improvement_manifest_after_freeze(tmp_path):
    ws = _workspace(tmp_path)
    state = initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    run_id = str(state["manifest"]["run_id"])
    entry_id = _propose_and_approve(ws)

    freeze_improvement_memory_for_run(workspace=ws, run_id=run_id)
    before = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8")
    )["improvement"]

    assert main([
        "state",
        "check",
        "--workspace", str(ws),
        "--repo-workdir", str(ROOT),
        "--strict",
    ]) == 0
    after = json.loads(
        (ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8")
    )["improvement"]

    assert before["materialized_entry_ids"] == [entry_id]
    assert after == before


def _append_approved_feedback_issue_entry_with_refs(
    ws: Path,
    *,
    issue_id: str,
    run_id: str,
    origin_runtime: str | None = None,
) -> None:
    first = _valid_revision(entry_id="AG-0001")
    first["source_evidence"][0]["issue_id"] = issue_id
    first["source_evidence"][0]["run_id"] = run_id
    first["source_evidence"][0]["origin"] = {
        "finding_type": "audience_mismatch",
        "issue_category": "audience_mismatch",
        "issue_source": "human",
        "source_item_id": "FINDING_001",
    }
    if origin_runtime is not None:
        first["source_evidence"][0]["origin"]["origin_runtime"] = origin_runtime
    second = json.loads(json.dumps(first))
    second["revision"] = 2
    second["previous_revision_sha256"] = revision_sha256(first)
    second["status"] = "approved"
    second["approved_by"] = "stahl"
    second["approved_at"] = "2026-06-10T00:01:00Z"
    path = improvement_ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(first))
        handle.write("\n")
        handle.write(canonical_json(second))
        handle.write("\n")


def _manifest(ws: Path) -> dict:
    return json.loads((ws / "output" / "intermediate" / "runtime_manifest.json").read_text(encoding="utf-8"))


def _handoff_json(ws: Path) -> dict:
    return json.loads((ws / "output" / "intermediate" / "agent_handoff.json").read_text(encoding="utf-8"))


def _event_types(ws: Path) -> list[str]:
    path = ws / "output" / "intermediate" / "event_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)["event_type"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_rebuild_projects_memory_only_and_sanitizes_evidence(tmp_path):
    ws = _workspace(tmp_path)
    entry_id = _propose_and_approve(ws)

    projection = rebuild_improvement_memory(workspace=ws)

    memory_path = ws / IMPROVEMENT_MEMORY_FILE
    assert projection["selected_entry_ids"] == [entry_id]
    assert projection["eligible_count"] == 1
    assert projection["memory_path"] == IMPROVEMENT_MEMORY_FILE
    assert projection["memory_sha256"] == sha256_file(memory_path)
    assert memory_path.exists()
    assert b"\r\n" not in memory_path.read_bytes()
    assert not (ws / "output" / "intermediate").exists()
    assert not (ws / "output" / "intermediate" / "runtime_manifest.json").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()

    text = memory_path.read_text(encoding="utf-8")
    assert f"schema: {IMPROVEMENT_MEMORY_SCHEMA}" in text
    assert f"selected_entry_ids: {entry_id}" in text
    assert "RAW SECRET SUMMARY" not in text
    assert "generated_at" not in text
    assert "snapshot_sha" not in text


def test_rebuild_reports_non_materializable_and_reverted_entries(tmp_path):
    ws = _workspace(tmp_path)
    _append_non_materializable_approved_entry(ws)

    projection = rebuild_improvement_memory(workspace=ws)

    assert projection["selected_entry_ids"] == []
    assert projection["eligible_count"] == 0
    assert projection["skipped_entries"] == [{
        "entry_id": "AG-0001",
        "reason_code": "repair_only_issue_category",
        "message": "This feedback issue category is machine-checkable or repair-only; use feedback/repair for this run, or rewrite a persistent preference as explicit human_feedback guidance with --source-summary.",
    }]
    assert "format_field_missing" not in (ws / IMPROVEMENT_MEMORY_FILE).read_text(encoding="utf-8")

    ws2 = _workspace(tmp_path / "reverted")
    entry_id = _propose_and_approve(ws2)
    revert_improvement(workspace=ws2, entry_id=entry_id, reverted_by="stahl", reason="No longer desired.")
    reverted_projection = rebuild_improvement_memory(workspace=ws2)
    assert reverted_projection["selected_entry_ids"] == []
    assert reverted_projection["skipped_entries"] == []
    assert '"status":"applied"' not in improvement_ledger_path(ws2).read_text(encoding="utf-8")


def test_rebuild_rejects_ledger_with_unsafe_feedback_issue_refs(tmp_path):
    ws = _workspace(tmp_path)
    _append_approved_feedback_issue_entry_with_refs(
        ws,
        issue_id="fi-1\n# Injected Heading",
        run_id="mabw-legacy-run",
    )

    with pytest.raises(ImprovementLedgerError, match="not valid for memory projection"):
        rebuild_improvement_memory(workspace=ws)

    assert not (ws / IMPROVEMENT_MEMORY_FILE).exists()


def test_rebuild_renders_origin_runtime_in_source_line(tmp_path):
    ws = _workspace(tmp_path)
    _append_approved_feedback_issue_entry_with_refs(
        ws,
        issue_id="fi-001",
        run_id="mabw-legacy-run",
        origin_runtime="hermes",
    )

    rebuild_improvement_memory(workspace=ws)

    text = (ws / IMPROVEMENT_MEMORY_FILE).read_text(encoding="utf-8")
    assert "runtime: hermes" in text
    assert "run mabw-legacy-run / issue fi-001 / runtime: hermes" in text


def test_rebuild_excludes_superseded_entries(tmp_path):
    ws = _workspace(tmp_path)
    old_id = _propose_and_approve(ws, guidance="Original audience guidance.")
    new_id = _propose_and_approve(
        ws,
        guidance="Replacement audience guidance.",
        supersedes=old_id,
    )

    projection = rebuild_improvement_memory(workspace=ws)
    text = (ws / IMPROVEMENT_MEMORY_FILE).read_text(encoding="utf-8")

    assert projection["selected_entry_ids"] == [new_id]
    assert projection["skipped_entries"] == [{
        "entry_id": old_id,
        "reason_code": "superseded_by_active_guidance",
        "message": f"Entry is superseded by {new_id}.",
    }]
    assert "Replacement audience guidance." in text
    assert "Original audience guidance." not in text


def test_rebuild_chain_reexposes_middle_after_revert(tmp_path):
    ws = _workspace(tmp_path)
    first_id = _propose_and_approve(ws, guidance="A guidance.")
    second_id = _propose_and_approve(ws, guidance="B guidance.", supersedes=first_id)
    third_id = _propose_and_approve(ws, guidance="C guidance.", supersedes=second_id)

    projection = rebuild_improvement_memory(workspace=ws)
    assert projection["selected_entry_ids"] == [third_id]

    revert_improvement(workspace=ws, entry_id=third_id, reverted_by="stahl", reason="No longer desired.")
    projection_after_revert = rebuild_improvement_memory(workspace=ws)
    text = (ws / IMPROVEMENT_MEMORY_FILE).read_text(encoding="utf-8")

    assert projection_after_revert["selected_entry_ids"] == [second_id]
    assert "B guidance." in text
    assert "A guidance." not in text
    assert "C guidance." not in text


def test_non_materializable_superseder_suppresses_target_with_warning(tmp_path):
    ws = _workspace(tmp_path)
    old_id = _propose_and_approve(ws, guidance="Original materializable guidance.")
    _append_non_materializable_approved_entry(ws, entry_id="AG-0002", supersedes_id=old_id)

    projection = rebuild_improvement_memory(workspace=ws)

    assert projection["selected_entry_ids"] == []
    assert {
        "entry_id": old_id,
        "reason_code": "superseded_by_active_guidance",
        "message": "Entry is superseded by AG-0002.",
    } in projection["skipped_entries"]
    assert projection["warnings"] == [{
        "entry_id": "AG-0002",
        "reason_code": "non_materializable_superseder_suppresses_target",
        "message": f"AG-0002 suppresses {old_id} but is not materializable.",
    }]


def test_freeze_rejects_unsafe_runtime_run_id(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws)

    with pytest.raises(ImprovementLedgerError, match="run_id is not safe"):
        freeze_improvement_memory_for_run(workspace=ws, run_id="run-1\n# Injected Run")

    assert not (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).exists()


def test_run_records_no_ledger_manifest_semantics_without_snapshot(tmp_path):
    ws = _workspace(tmp_path)

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0

    improvement = _manifest(ws)["improvement"]
    memory_sha = sha256_file(ws / IMPROVEMENT_MEMORY_FILE)
    assert improvement == {
        "ledger_sha256": None,
        "memory_sha256": memory_sha,
        "snapshot_path": None,
        "snapshot_sha256": None,
        "materialized_entry_ids": [],
    }
    assert (ws / IMPROVEMENT_MEMORY_FILE).exists()
    assert not (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).exists()
    assert _handoff_json(ws)["improvement_memory_files"] == {}


def test_run_records_zero_eligible_manifest_semantics(tmp_path):
    ws = _workspace(tmp_path)
    _append_non_materializable_approved_entry(ws)
    ledger_sha = sha256_file(improvement_ledger_path(ws))

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0

    improvement = _manifest(ws)["improvement"]
    memory_sha = sha256_file(ws / IMPROVEMENT_MEMORY_FILE)
    assert improvement == {
        "ledger_sha256": ledger_sha,
        "memory_sha256": memory_sha,
        "snapshot_path": None,
        "snapshot_sha256": None,
        "materialized_entry_ids": [],
    }
    assert not (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).exists()
    assert _handoff_json(ws)["improvement_memory_files"] == {}


def test_run_freezes_snapshot_manifest_and_handoff_consistently(tmp_path):
    ws = _workspace(tmp_path)
    entry_id = _propose_and_approve(ws)
    (ws / IMPROVEMENT_MEMORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    (ws / IMPROVEMENT_MEMORY_FILE).write_text("stale memory\n", encoding="utf-8")

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0

    memory_text = (ws / IMPROVEMENT_MEMORY_FILE).read_text(encoding="utf-8")
    assert "stale memory" not in memory_text
    assert entry_id in memory_text

    snapshot = ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE
    assert snapshot.exists()
    snapshot_sha = sha256_file(snapshot)
    improvement = _manifest(ws)["improvement"]
    assert improvement["ledger_sha256"] == sha256_file(improvement_ledger_path(ws))
    assert improvement["memory_sha256"] == sha256_file(ws / IMPROVEMENT_MEMORY_FILE)
    assert improvement["materialized_entry_ids"] == [entry_id]
    assert improvement["snapshot_path"] == IMPROVEMENT_MEMORY_SNAPSHOT_FILE
    assert improvement["snapshot_sha256"] == snapshot_sha
    assert "applied_entry_ids" not in improvement

    handoff = _handoff_json(ws)
    assert handoff["improvement_memory_files"] == {
        "improvement_memory_snapshot": IMPROVEMENT_MEMORY_SNAPSHOT_FILE,
    }
    md_text = (ws / "output" / "intermediate" / "agent_handoff.md").read_text(encoding="utf-8")
    assert IMPROVEMENT_MEMORY_SNAPSHOT_FILE in handoff["prompt"]
    assert IMPROVEMENT_MEMORY_SNAPSHOT_FILE in md_text
    assert IMPROVEMENT_MEMORY_FILE not in handoff["prompt"]
    assert IMPROVEMENT_MEMORY_FILE not in md_text
    assert _event_types(ws).count("improvement_memory_snapshot_created") == 1


def test_run_rejects_preserved_runtime_manifest_with_unsafe_run_id(tmp_path):
    ws = _workspace(tmp_path)
    _propose_and_approve(ws)
    initialize_runtime_state(workspace=ws, repo_workdir=ROOT)
    manifest_path = ws / "output" / "intermediate" / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_id"] = "run-1\n# Injected Run"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 1
    assert not (ws / "output" / "intermediate" / "audience_profile_snapshot.md").exists()
    assert not (ws / "output" / "intermediate" / "orchestrator_control_switchboard.json").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.json").exists()
    assert not (ws / "output" / "intermediate" / "agent_handoff.md").exists()
    assert not (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).exists()


def test_repeated_run_deduplicates_event_and_ledger_change_refreshes_snapshot(tmp_path):
    ws = _workspace(tmp_path)
    first_id = _propose_and_approve(ws)

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0
    first_snapshot = (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).read_text(encoding="utf-8")
    first_sha = sha256_file(ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE)
    assert _event_types(ws).count("improvement_memory_snapshot_created") == 1

    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0
    assert (ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE).read_text(encoding="utf-8") == first_snapshot
    assert sha256_file(ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE) == first_sha
    assert _event_types(ws).count("improvement_memory_snapshot_created") == 1

    second_id = _propose_and_approve(
        ws,
        guidance="Put audience implications before methodology details.",
        scope="executive_summary",
        source_summary="Operator-created second proposal.",
    )
    assert main(["run", "--workspace", str(ws), "--skip-doctor"]) == 0

    improvement = _manifest(ws)["improvement"]
    assert improvement["materialized_entry_ids"] == sorted([first_id, second_id])
    assert improvement["memory_sha256"] == sha256_file(ws / IMPROVEMENT_MEMORY_FILE)
    assert sha256_file(ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE) != first_sha
    assert improvement["snapshot_sha256"] == sha256_file(ws / IMPROVEMENT_MEMORY_SNAPSHOT_FILE)
    assert _handoff_json(ws)["improvement_memory_files"]["improvement_memory_snapshot"] == improvement["snapshot_path"]
    assert _event_types(ws).count("improvement_memory_snapshot_created") == 2


def test_start_uses_same_improvement_snapshot_surface(tmp_path):
    ws = _workspace(tmp_path)
    entry_id = _propose_and_approve(ws)

    assert main(["start", "--workspace", str(ws), "--skip-doctor"]) == 0

    improvement = _manifest(ws)["improvement"]
    assert improvement["materialized_entry_ids"] == [entry_id]
    assert improvement["memory_sha256"] == sha256_file(ws / IMPROVEMENT_MEMORY_FILE)
    assert improvement["snapshot_path"] == IMPROVEMENT_MEMORY_SNAPSHOT_FILE
    assert _handoff_json(ws)["improvement_memory_files"] == {
        "improvement_memory_snapshot": IMPROVEMENT_MEMORY_SNAPSHOT_FILE,
    }
