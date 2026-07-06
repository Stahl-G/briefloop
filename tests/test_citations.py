from __future__ import annotations

from multi_agent_brief.core.citations import (
    parse_internal_citation_markers,
    resolved_internal_citation_ids,
    unresolved_internal_citation_markers,
)


def test_parse_internal_citation_markers_resolves_supported_marker_shapes() -> None:
    text = (
        "Alpha [src:CL-001]\n"
        "Beta [source:SYN_CLAIM_001]\n"
        "Gamma src:SOURCEA_ABC123\n"
        "Delta source:TEST_123456\n"
        "Epsilon CLAIM_FREEFORM\n"
    )

    markers = parse_internal_citation_markers(
        text,
        valid_claim_ids={
            "CL-001",
            "SYN_CLAIM_001",
            "SOURCEA_ABC123",
            "TEST_123456",
            "CLAIM_FREEFORM",
        },
    )

    assert [(marker.kind, marker.claim_id, marker.status) for marker in markers] == [
        ("bracketed_source_marker", "CL-001", "resolved"),
        ("bracketed_source_marker", "SYN_CLAIM_001", "resolved"),
        ("bare_source_marker", "SOURCEA_ABC123", "resolved"),
        ("bare_source_marker", "TEST_123456", "resolved"),
        ("bare_claim_id", "CLAIM_FREEFORM", "resolved"),
    ]


def test_parse_internal_citation_markers_uses_ledger_membership_not_id_family() -> None:
    text = "Generated ID resolves if the ledger owns it. [src:SOURCEA_9F8E7D6C]"

    assert resolved_internal_citation_ids(
        text,
        valid_claim_ids={"SOURCEA_9F8E7D6C"},
    ) == ["SOURCEA_9F8E7D6C"]


def test_parse_internal_citation_markers_does_not_prefix_match_bare_markers() -> None:
    text = "Bad suffixes src:CL-001-extra and src:CL-001A must not project a prefix."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("src:CL-001-extra", "CL-001-extra", "unresolved"),
        ("src:CL-001A", "CL-001A", "unresolved"),
    ]
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == []


def test_parse_internal_citation_markers_reports_empty_and_unknown_markers() -> None:
    text = "Empty [src:] and unknown [source:CL-404]."

    unresolved = unresolved_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.status) for marker in unresolved] == [
        ("[src:]", "malformed"),
        ("[source:CL-404]", "unresolved"),
    ]


def test_parse_internal_citation_markers_preserves_prose_and_urls() -> None:
    text = (
        "Primary source: company filing.\n"
        "Source: FDA clinical registry.\n"
        "The source: company filing supports this statement.\n"
        "For setup visit https://example.com/source:CL-001 path.\n"
        "Legit marker source:CL-001.\n"
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_treats_trailing_punctuation_as_delimiter() -> None:
    text = 'Trailing punctuation source:CL-001: and quoted "source:CL-001".'

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_rejects_pathlike_bare_suffixes() -> None:
    text = (
        "Path suffix source:CL-001/path "
        "query suffix source:CL-001?x "
        "fragment suffix source:CL-001#frag "
        "sentence marker source:CL-001."
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001/path", "CL-001/path", "unresolved"),
        ("source:CL-001?x", "CL-001?x", "unresolved"),
        ("source:CL-001#frag", "CL-001#frag", "unresolved"),
        ("source:CL-001", "CL-001", "resolved"),
    ]
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]


def test_parse_internal_citation_markers_bracketed_and_bare_share_candidate_rules() -> None:
    text = "[source:CL-001/path] source:CL-001/path [source:CL-001.] source:CL-001."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.kind, marker.claim_id, marker.status) for marker in markers] == [
        ("bracketed_source_marker", "CL-001/path", "unresolved"),
        ("bare_source_marker", "CL-001/path", "unresolved"),
        ("bracketed_source_marker", "CL-001", "resolved"),
        ("bare_source_marker", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_does_not_match_bare_id_inside_longer_token() -> None:
    text = "CL-001-extra and CL-001A are not the claim CL-001, but (CL-001) is."

    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]
