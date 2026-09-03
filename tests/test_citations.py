from __future__ import annotations

from multi_agent_brief.core.citations import (
    extract_src_ref_ids,
    parse_internal_citation_markers,
)


def test_parse_internal_citation_markers_resolves_only_canonical_src_markers() -> None:
    text = (
        "Alpha [src:CL-001]\n"
        "Beta [src:SYN_CLAIM_001]\n"
        "Deprecated [source:CL-001]\n"
        "Bare src:CL-001 and source:CL-001 stay prose.\n"
        "Raw CL-001 and SYN_CLAIM_001 are residue, not citations.\n"
    )

    markers = parse_internal_citation_markers(
        text,
        valid_claim_ids={"CL-001", "SYN_CLAIM_001"},
    )

    assert [(marker.kind, marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("src_marker", "[src:CL-001]", "CL-001", "resolved"),
        ("src_marker", "[src:SYN_CLAIM_001]", "SYN_CLAIM_001", "resolved"),
    ]
    assert extract_src_ref_ids(text) == ["CL-001", "SYN_CLAIM_001"]


def test_parse_internal_citation_markers_preserves_ordinary_source_prose() -> None:
    text = (
        "Primary source: company filing.\n"
        "Primary source:10-K filing.\n"
        "Source: Q2-2026 report.\n"
        "URL label source:https://example.com/report.\n"
        "For setup visit https://example.com/source:CL-001 path.\n"
    )

    assert parse_internal_citation_markers(text, valid_claim_ids={"CL-001"}) == []
