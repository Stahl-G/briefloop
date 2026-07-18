"""Public-safe source-clone E2E rows for MU-LAJ-1."""

from __future__ import annotations

import json
import os
from pathlib import Path

from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-18T00:00:00Z"


def _invocation(tmp_path: Path) -> dict[str, object]:
    return {
        "report": FIXTURES / "report.md",
        "bounded_context": FIXTURES / "bounded_context.json",
        "profile": PROFILE_ID,
        "instrument": FIXTURES / "instrument.json",
        "trial_id": "trial-public-synthetic-e2e-v4",
        "archive_root": (tmp_path / "archives").resolve(),
        "clock": lambda: FIXED_TIME,
        "sleep": lambda _seconds: None,
    }


def test_se2r_01_public_synthetic_archive_is_complete_and_nonqualifying(
    tmp_path: Path,
) -> None:
    result = run_shadow(**_invocation(tmp_path))
    assert result.to_dict() == {
        "ok": True,
        "replayed": False,
        "archive_complete": True,
        "archive_path": result.archive_path,
        "receipt_id": result.receipt_id,
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "qualification_eligible": False,
    }
    archive = Path(result.archive_path or "")
    assert (archive / "COMPLETE").is_file()
    assert len(list((archive / "prompts").glob("*.json"))) == 9
    assert len(list((archive / "attempts").glob("*/*/response.body"))) == 9
    assert len(list((archive / "attempts").glob("*/*/boundary_facts.json"))) == 9
    receipt = json.loads((archive / "receipt.json").read_bytes())
    assert receipt["qualification_eligible"] is False


def test_se2r_12_exact_replay_precedes_credentials_adapter_and_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invocation = _invocation(tmp_path)
    first = run_shadow(**invocation)
    assert first.ok is True
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter_touched = False

    def forbidden_factory(_execution):
        nonlocal adapter_touched
        adapter_touched = True
        raise AssertionError("replay touched adapter/network")

    replay = run_shadow(**invocation, adapter_factory=forbidden_factory)
    assert replay.ok is True
    assert replay.replayed is True
    assert replay.receipt_id == first.receipt_id
    assert replay.archive_path == first.archive_path
    assert adapter_touched is False
    assert "OPENAI_API_KEY" not in os.environ


def test_exact_replay_is_byte_stable_and_does_not_add_archive_members(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    first = run_shadow(**invocation)
    archive = Path(first.archive_path or "")
    before = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    }
    replay = run_shadow(**invocation)
    after = {
        path.relative_to(archive).as_posix(): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    }
    assert replay.replayed is True
    assert after == before
