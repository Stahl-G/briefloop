"""Tests verifying docx_generation metadata is persisted in audit_report.json."""
from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.agents.formatter import FormatterAgent
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.schemas import AuditReport, PipelineContext


def test_formatter_persists_docx_generation_metadata_when_docx_missing(tmp_path):
    """When python-docx is missing and docx is in output_formats,
    audit_report.json must contain metadata.docx_generation = 'skipped_missing_dependency'."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    context = PipelineContext(
        project_name="Test",
        input_dir=str(tmp_path / "input"),
        output_dir=str(output_dir),
        output_formats=["markdown", "docx"],
    )
    # Set a minimal audit report so it gets written
    context.report_state.audit_report = AuditReport(
        audit_status="pass",
        audit_score=100,
        findings=[],
        metadata={},
    )
    context.report_state.prepared_markdown = "# Test Brief\n\nSome content.\n"

    # Run formatter — docx will fail because python-docx may not be installed,
    # or it will succeed. Either way, docx_generation must be in the JSON.
    FormatterAgent().run(context, ClaimLedger())

    audit_json_path = output_dir / "audit_report.json"
    assert audit_json_path.exists(), "audit_report.json should be written"

    data = json.loads(audit_json_path.read_text(encoding="utf-8"))
    assert "docx_generation" in data.get("metadata", {}), (
        "metadata.docx_generation should be persisted in audit_report.json"
    )
    assert data["metadata"]["docx_generation"] in (
        "generated",
        "skipped_missing_dependency",
        "failed",
    )


def test_formatter_no_docx_when_not_in_formats(tmp_path):
    """When docx is NOT in output_formats, metadata should NOT have docx_generation."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    context = PipelineContext(
        project_name="Test",
        input_dir=str(tmp_path / "input"),
        output_dir=str(output_dir),
        output_formats=["markdown"],
    )
    context.report_state.audit_report = AuditReport(
        audit_status="pass",
        audit_score=100,
        findings=[],
        metadata={},
    )
    context.report_state.prepared_markdown = "# Test Brief\n\nContent.\n"

    FormatterAgent().run(context, ClaimLedger())

    audit_json_path = output_dir / "audit_report.json"
    data = json.loads(audit_json_path.read_text(encoding="utf-8"))
    assert "docx_generation" not in data.get("metadata", {}), (
        "docx_generation should not appear when docx is not in output_formats"
    )


def test_source_map_includes_date_metadata(tmp_path):
    """source_map.md should include Published At, Retrieved At, Source Name from claim metadata."""
    from multi_agent_brief.core.schemas import Claim
    from multi_agent_brief.outputs.source_map import render_source_map

    ledger = ClaimLedger()
    ledger.add_claim(Claim(
        claim_id="SMAP001TEST",
        statement="Test claim with dates",
        source_id="SRC1",
        evidence_text="Evidence text",
        source_url="https://example.com",
        metadata={
            "published_at": "2026-06-01",
            "retrieved_at": "2026-06-03T10:00:00Z",
            "source_name": "Reuters",
        },
    ))
    ledger.add_claim(Claim(
        claim_id="SMAP002TEST",
        statement="Test claim without dates",
        source_id="SRC2",
        evidence_text="Evidence text",
        metadata={},
    ))

    result = render_source_map(ledger)

    # Claim with dates
    assert "Published At: 2026-06-01" in result
    assert "Retrieved At: 2026-06-03T10:00:00Z" in result
    assert "Source Name: Reuters" in result

    # Claim without dates should not have empty lines
    assert "Published At:" not in result.split("SMAP002TEST")[1].split("Evidence")[0]


def test_scout_web_search_claim_has_retrieved_at():
    """Web search claims should include retrieved_at from source.retrieved_at."""
    from multi_agent_brief.agents.scout import _extract_web_search_claim
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import SourceItem

    source = SourceItem(
        source_id="WS_TEST001",
        source_name="Reuters",
        source_type="web_search",
        title="Test News Title",
        content="This is a substantive news snippet about solar industry developments.",
        url="https://example.com/news",
        published_at="",
        retrieved_at="2026-06-03T10:00:00Z",
        metadata={"backend": "tavily", "query": "solar news"},
    )

    ledger = ClaimLedger()
    claim = _extract_web_search_claim(source, ledger, "scout")

    assert claim is not None
    assert claim.metadata.get("retrieved_at") == "2026-06-03T10:00:00Z"
    assert claim.metadata.get("source_name") == "Reuters"
    assert claim.metadata.get("published_at") == ""


def test_scout_local_file_claim_has_retrieved_at():
    """Local file claims should include retrieved_at from source.retrieved_at."""
    from multi_agent_brief.agents.scout import ScoutAgent
    from multi_agent_brief.core.claim_ledger import ClaimLedger
    from multi_agent_brief.core.schemas import PipelineContext, SourceItem

    # Create a source with retrieved_at but no published_at
    source = SourceItem(
        source_id="LOCAL_TEST",
        source_name="Local File",
        source_type="local_file",
        title="Local Test",
        content="This is a long enough line for the scout to extract as a candidate claim.",
        published_at="",
        retrieved_at="2026-06-03T12:00:00Z",
    )

    context = PipelineContext(
        project_name="Test",
        input_dir=str(Path("/nonexistent")),
        output_dir=str(Path("/nonexistent")),
    )
    context.sources = [source]

    ledger = ClaimLedger()
    agent = ScoutAgent()
    agent.run(context, ledger)

    claims = list(ledger)
    assert len(claims) >= 1
    claim = claims[0]
    assert claim.metadata.get("retrieved_at") == "2026-06-03T12:00:00Z"
    assert claim.metadata.get("source_name") == "Local File"
