"""Pre-submit content lint for owned brief artifacts.

Runs the same deterministic checks the auditor gate batch will run —
number-without-source, reader residue, section skeleton, price-narrative
divergence, chart placement, and executive-summary visibility — at
invocation-validate time so violations surface before accept.  The rule
bodies are shared with the gates (single source of truth); this module
only assembles them for read-only linting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.contracts.v2 import RunDirection
from multi_agent_brief.core.claim_ledger import ClaimLedger
from multi_agent_brief.core.citations import remove_src_marker_spans
from multi_agent_brief.outputs.reader_final_gate import detect_reader_residue
from multi_agent_brief.outputs.reader_projection import (
    reader_projection_source_markdown,
)


def _core_market_context(snapshot: Any, workspace: Path | None):
    """(core_ticker, return_1w, multiples_available, peer_count) or Nones."""

    universe = None
    if workspace is not None:
        try:
            from multi_agent_brief.sources.equity_universe import (
                load_equity_universe,
            )

            universe = load_equity_universe(workspace)
        except Exception:
            universe = None
    records = list(getattr(snapshot, "market_data_snapshots", ()) or ())
    if not records or universe is None:
        return None, None, False, 0
    latest = max(
        records,
        key=lambda item: (
            item.as_of_date,
            item.recorded_at,
            item.market_data_snapshot_id,
        ),
    )
    core_tickers = set(universe.core_tickers)
    core_ticker = None
    return_1w = None
    core_multiple_available = False
    peer_count = 0
    multiple_fields = ("ps_ttm", "ev_ebitda_ttm", "pe_ttm")

    def _has_multiple(security) -> bool:
        return any(
            field.field_id in multiple_fields
            and field.status == "available"
            and field.value_number is not None
            for field in security.fields
        )

    for security in latest.securities:
        if security.ticker in core_tickers:
            core_ticker = security.ticker
            core_multiple_available = _has_multiple(security)
            for field in security.fields:
                if (
                    field.field_id == "return_1w_pct"
                    and field.status == "available"
                    and field.value_number is not None
                ):
                    return_1w = float(field.value_number)
        elif _has_multiple(security):
            peer_count += 1
    return core_ticker, return_1w, core_multiple_available, peer_count


def _summary_visibility_findings(
    markdown: str,
    direction: RunDirection,
) -> list[str]:
    from multi_agent_brief.quality_gates.section_contract import (
        section_matching,
        sections_from_markdown,
    )

    summary = section_matching(
        sections_from_markdown(markdown), "executive_summary"
    )
    if summary is None:
        return ["executive summary section is missing"]
    lowered = summary.body.lower()
    terms = [direction.subject_name, *direction.target_terms]
    if not any(str(term).lower() in lowered for term in terms if term):
        return [
            "executive summary does not mention the configured target "
            "entity or topic"
        ]
    return []


def brief_content_lint(
    markdown: str,
    *,
    ledger: ClaimLedger | None,
    direction: RunDirection,
    workspace: Path | None,
    snapshot: Any,
) -> list[dict[str, str]]:
    """Typed lint findings for one owned brief artifact."""

    messages: list[dict[str, str]] = []

    def _add(finding_type: str, description: str) -> None:
        messages.append(
            {"finding_type": finding_type, "description": description}
        )

    from multi_agent_brief.audit.deterministic import run_deterministic_audit

    if ledger is not None:
        audit = run_deterministic_audit(
            markdown,
            ledger,
            report_date=direction.report_date,
            max_source_age_days=direction.max_source_age_days,
            report_window_start=direction.report_window_start or "",
        )
        for finding in audit.findings:
            if finding.finding_type in {"number_without_source", "missing_claim"}:
                _add(
                    finding.finding_type,
                    f"line {finding.line_number}: {finding.description}",
                )

    try:
        reader_source = reader_projection_source_markdown(markdown)
    except Exception:
        reader_source = markdown
    residue = detect_reader_residue(remove_src_marker_spans(reader_source), "lint")
    for finding in residue.findings:
        _add("reader_residue", f"line {finding.line}: {finding.message}")

    for message in _summary_visibility_findings(markdown, direction):
        _add("target_relevance", message)

    from multi_agent_brief.quality_gates.section_contract import (
        required_section_findings,
    )

    core_ticker, return_1w, core_multiple, peers = _core_market_context(
        snapshot, workspace
    )
    for finding in required_section_findings(
        markdown,
        required_intents=list(direction.required_section_intents),
        report_date=direction.report_date,
        core_ticker=core_ticker,
        core_multiple_available=core_multiple,
        peer_multiples_count=peers,
    ):
        _add(str(finding["finding_type"]), str(finding["description"]))

    from multi_agent_brief.quality_gates.narrative_coherence import (
        price_narrative_findings,
    )

    for finding in price_narrative_findings(
        markdown,
        core_ticker=core_ticker,
        return_1w=return_1w,
        ledger=ledger,
        threshold_pct=direction.market_divergence_threshold_pct,
    ):
        _add(str(finding["finding_type"]), str(finding["description"]))

    if workspace is not None and direction.required_section_intents:
        from multi_agent_brief.quality_gates.chart_placement import (
            chart_placement_findings,
            manifest_chart_ids,
        )

        manifest_ids = manifest_chart_ids(
            workspace
            / "output"
            / "intermediate"
            / "market_data_chart_manifest.json"
        )
        for finding in chart_placement_findings(
            markdown,
            manifest_ids=manifest_ids,
            required_intents=list(direction.required_section_intents),
        ):
            _add(str(finding["finding_type"]), str(finding["description"]))

    return messages


__all__ = ["brief_content_lint"]
