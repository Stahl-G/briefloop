"""Human-facing LAJ reader projections remain deterministic and advisory-only."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    build_laj_reader_view,
    write_laj_reader_artifacts,
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"
FIXED_TIME = "2026-07-18T00:00:00Z"


def _archive(tmp_path: Path) -> Path:
    result = run_shadow(
        report=FIXTURES / "report.md",
        bounded_context=FIXTURES / "bounded_context.json",
        profile=PROFILE_ID,
        instrument=FIXTURES / "instrument.json",
        trial_id="trial-public-laj-reader-v1",
        archive_root=(tmp_path / "archives").resolve(),
        clock=lambda: FIXED_TIME,
        sleep=lambda _seconds: None,
    )
    assert result.ok is True
    return Path(result.archive_path or "")


def test_verified_archive_renders_byte_stable_json_markdown_and_html(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    first = write_laj_reader_artifacts(
        archive_path=archive,
        output_dir=tmp_path / "laj-advisory-reader-one",
    )
    second = write_laj_reader_artifacts(
        archive_path=archive,
        output_dir=tmp_path / "laj-advisory-reader-two",
    )

    assert first.view.status == "available"
    assert first.view.advisory_only is True
    assert first.view.runtime_authority is False
    assert first.view.authority_effect == "none"
    assert first.view.archive_verified is True
    assert first.view.assessed_unit_count == 25
    assert first.view.finding_count == 0
    assert first.view.binding is not None
    assert len(first.view.binding.report_sha256) == 64
    assert first.json_sha256 == second.json_sha256
    assert first.markdown_sha256 == second.markdown_sha256
    assert first.html_sha256 == second.html_sha256
    for name in ("laj.html", "laj.json", "laj.md"):
        assert (first.output_dir / name).read_bytes() == (
            second.output_dir / name
        ).read_bytes()
    markdown = (first.output_dir / "laj.md").read_text(encoding="utf-8")
    html = (first.output_dir / "laj.html").read_text(encoding="utf-8")
    payload = json.loads((first.output_dir / "laj.json").read_bytes())
    assert "Advisory only" in markdown
    assert "Runtime authority: `none`" in markdown
    assert "Experimental · Offline shadow · Advisory only" in html
    assert payload["boundary"] == LAJ_READER_BOUNDARY
    assert payload["runtime_authority"] is False


def test_missing_tampered_and_stale_archives_never_display_findings(
    tmp_path: Path,
) -> None:
    missing = build_laj_reader_view(tmp_path / "missing")
    assert missing.status == "not_available"
    assert missing.archive_verified is False
    assert missing.binding is None
    assert missing.findings == []

    archive = _archive(tmp_path)
    stale = build_laj_reader_view(
        archive,
        expected_report_sha256="0" * 64,
    )
    assert stale.status == "stale"
    assert stale.binding is not None
    assert stale.findings == []
    assert "report_binding_stale" in stale.reason_codes

    presentation = archive / "presentation_actual.json"
    presentation.write_bytes(presentation.read_bytes() + b" ")
    invalid = build_laj_reader_view(archive)
    assert invalid.status == "invalid"
    assert invalid.binding is None
    assert invalid.findings == []
    assert invalid.finding_count == 0
