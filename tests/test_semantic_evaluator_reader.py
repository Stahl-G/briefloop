"""Human-facing LAJ reader projections remain deterministic and advisory-only."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_BOUNDARY,
    LajReaderView,
    build_laj_reader_view,
    render_laj_reader_html,
    render_laj_reader_markdown,
    write_laj_reader_artifacts,
)
from multi_agent_brief.semantic_evaluator.adapters.synthetic_fixture import (
    SyntheticFixtureAdapterV4,
)
from multi_agent_brief.semantic_evaluator.runner import PROFILE_ID, run_shadow
from multi_agent_brief.semantic_evaluator.serialization import canonical_sha256


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
        output_dir=tmp_path / "reader-one",
    )
    second = write_laj_reader_artifacts(
        archive_path=archive,
        output_dir=tmp_path / "reader-two",
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


def test_complete_provider_failure_is_bound_unavailable_and_has_zero_advice(
    tmp_path: Path,
) -> None:
    current = SyntheticFixtureAdapterV4()

    class FailingAdapter:
        adapter_id = current.adapter_id
        adapter_version = current.adapter_version
        provider_sdk_name = current.provider_sdk_name
        provider_sdk_version = current.provider_sdk_version
        qualification_eligible = current.qualification_eligible

        def invoke(self, _request):
            raise RuntimeError("PRIVATE_PROVIDER_FAILURE_48152")

    result = run_shadow(
        report=FIXTURES / "report.md",
        bounded_context=FIXTURES / "bounded_context.json",
        profile=PROFILE_ID,
        instrument=FIXTURES / "instrument.json",
        trial_id="trial-public-laj-reader-failure-v1",
        archive_root=(tmp_path / "archives").resolve(),
        adapter_factory=lambda _execution: FailingAdapter(),
        clock=lambda: FIXED_TIME,
        sleep=lambda _seconds: None,
    )
    assert result.archive_complete is True
    view = build_laj_reader_view(result.archive_path or "")
    assert view.status == "unavailable"
    assert view.archive_verified is True
    assert view.binding is not None
    assert view.findings == []
    assert view.finding_count == 0
    rendered = render_laj_reader_html(view).decode("utf-8")
    assert "PRIVATE_PROVIDER_FAILURE_48152" not in rendered
    assert "Runtime authority: none" in rendered


def test_html_escapes_finding_text_instead_of_creating_active_content() -> None:
    finding = {
        "assessment_unit_id": "AU-000000000001",
        "scope_class": "O1",
        "dimension_id": "cross_section_consistency",
        "severity": "major",
        "impact_scope": "supporting_text",
        "report_spans": [
            {
                "report_sha256": "1" * 64,
                "block_id": "B000001",
                "start_char": 0,
                "end_char": 5,
                "excerpt_sha256": "2" * 64,
            }
        ],
        "context_requirement_ids": [],
        "observation": "<script>alert(1)</script>",
        "rationale": "<img src=x onerror=alert(1)> [click](javascript:alert(1))",
        "severity_basis": "Human review required.",
        "confidence_basis": "direct_single_span",
        "external_premise_disclosure": "none",
        "recommended_human_action": "inspect_manually",
        "suggested_rewrite": None,
        "finding_id": "F-000000000001",
        "status": "proposal",
    }
    payload = {
        "schema_version": "briefloop.semantic_evaluator.reader_view.v1",
        "status": "available",
        "boundary": LAJ_READER_BOUNDARY,
        "advisory_only": True,
        "shadow_only": True,
        "runtime_authority": False,
        "authority_effect": "none",
        "archive_verified": True,
        "binding": {
            "artifact_id": "reader-test",
            "report_sha256": "1" * 64,
            "trial_id": "trial-reader-test",
            "shadow_receipt_id": "receipt-reader-test",
            "instrument_sha256": "2" * 64,
            "execution_sha256": "3" * 64,
            "execution_origin": "synthetic_fixture",
            "model_id": "synthetic-fixture-v4",
            "model_version": "synthetic-fixture-v4",
            "archive_manifest_sha256": "4" * 64,
            "presentation_sha256": "5" * 64,
        },
        "run_status": "completed",
        "validation_status": "accepted",
        "reason_codes": [],
        "assessed_unit_count": 1,
        "finding_count": 1,
        "withheld_finding_count": 0,
        "abstention_count": 0,
        "findings": [finding],
        "disclaimer": "Experimental advisory finding.",
    }
    view = LajReaderView.model_validate(
        {**payload, "view_sha256": canonical_sha256(payload)}
    )
    html = render_laj_reader_html(view).decode("utf-8")
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    markdown = render_laj_reader_markdown(view).decode("utf-8")
    assert "[click](javascript:" not in markdown
    assert "\\[click\\]\\(javascript:alert\\(1\\)\\)" in markdown


def test_reader_has_no_runtime_authority_import_or_workspace_effect(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    authority_files = {
        tmp_path / "control_store.sqlite3": b"SQLITE-AUTHORITY-SENTINEL",
        tmp_path / "workflow_state.json": b"WORKFLOW-AUTHORITY-SENTINEL",
        tmp_path / "finalize_report.json": b"FINALIZE-AUTHORITY-SENTINEL",
        tmp_path / "delivery.json": b"DELIVERY-AUTHORITY-SENTINEL",
    }
    for path, data in authority_files.items():
        path.write_bytes(data)
    before = {path: path.read_bytes() for path in authority_files}
    write_laj_reader_artifacts(
        archive_path=archive,
        output_dir=tmp_path / "reader",
    )
    assert {path: path.read_bytes() for path in authority_files} == before

    source = Path("src/multi_agent_brief/semantic_evaluator/reader.py").read_text(
        encoding="utf-8"
    )
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {
        "control_store",
        "quality_panel",
        "quality_gates",
        "finalize",
        "delivery",
        "runtime_state",
    }
    assert not any(any(token in name for token in forbidden) for name in imported)
