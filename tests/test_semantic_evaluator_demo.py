"""Public-safe packaged LAJ demo E2E."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.semantic_evaluator.demo import run_public_safe_laj_demo


def test_packaged_synthetic_demo_runs_presents_and_exactly_replays(
    tmp_path: Path,
) -> None:
    archive_root = (tmp_path / "archives").resolve()
    first = run_public_safe_laj_demo(
        archive_root=archive_root,
        output_dir=(tmp_path / "laj-advisory-demo-one").resolve(),
    )
    assert first.ok is True
    assert first.replayed is False
    assert first.archive_complete is True
    assert first.presentation_available is True
    assert first.execution_origin == "synthetic_fixture"
    assert first.qualification_class == "synthetic_demo_only"
    assert first.qualification_eligible is False
    assert first.runtime_authority is False
    assert first.output_files == ("laj.html", "laj.json", "laj.md")

    output = tmp_path / "laj-advisory-demo-one"
    payload = json.loads((output / "laj.json").read_bytes())
    assert payload["advisory_only"] is True
    assert payload["runtime_authority"] is False
    assert payload["authority_effect"] == "none"
    assert "Not a Gate" in (output / "laj.html").read_text(encoding="utf-8")

    replay = run_public_safe_laj_demo(
        archive_root=archive_root,
        output_dir=(tmp_path / "laj-advisory-demo-two").resolve(),
    )
    assert replay.ok is True
    assert replay.replayed is True
    assert replay.receipt_id == first.receipt_id
    assert replay.view_sha256 == first.view_sha256


def test_demo_presentation_failure_preserves_archive_without_fallback(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "laj-advisory-demo-existing").resolve()
    output.mkdir()
    result = run_public_safe_laj_demo(
        archive_root=(tmp_path / "archives").resolve(),
        output_dir=output,
    )
    assert result.ok is False
    assert result.archive_complete is True
    assert result.presentation_available is False
    assert result.reader_status == "unavailable"
    assert result.reason_codes == ("laj_presentation_write_failed",)
    assert result.output_files == ()
    assert result.qualification_eligible is False
    assert result.runtime_authority is False
