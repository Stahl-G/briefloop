from __future__ import annotations

from multi_agent_brief.core.citations import (
    claim_id_mentions_for_ledger,
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


def test_parse_internal_citation_markers_resolves_explicit_alpha_ledger_id() -> None:
    text = "Explicit compact marker source:ALPHACLAIM resolves, prose source:company does not."

    markers = parse_internal_citation_markers(
        text,
        valid_claim_ids={"ALPHACLAIM"},
    )

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:ALPHACLAIM", "ALPHACLAIM", "resolved"),
    ]
    assert resolved_internal_citation_ids(
        "Free-standing ALPHACLAIM stays prose.",
        valid_claim_ids={"ALPHACLAIM"},
    ) == []


def test_parse_internal_citation_markers_reports_alpha_only_explicit_marker_without_ledger() -> None:
    text = (
        "Explicit unresolved marker source:ALPHACLAIM, "
        "but prose Source:FDA, source:company, source:10-K, and Source:Q2-2026 stay text."
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids=None)

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:ALPHACLAIM", "ALPHACLAIM", "unresolved"),
    ]


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


def test_parse_internal_citation_markers_does_not_let_broken_bracket_hide_later_citation() -> None:
    text = "Broken [src:CL-404\nNext [src:CL-001]."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("src:CL-404", "CL-404", "unresolved"),
        ("[src:CL-001]", "CL-001", "resolved"),
    ]
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]


def test_parse_internal_citation_markers_preserves_prose_and_urls() -> None:
    text = (
        "Primary source: company filing.\n"
        "Primary source:company filing.\n"
        "Source: FDA clinical registry.\n"
        "The source: company filing supports this statement.\n"
        "URL label source:https://example.com.\n"
        "For setup visit https://example.com/source:CL-001 path.\n"
        "Legit marker source:CL-001.\n"
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_treats_trailing_punctuation_as_delimiter() -> None:
    text = (
        'Trailing punctuation source:CL-001: and quoted "source:CL-001".\n'
        "Full-width punctuation source:CL-001。 and source:CL-001，\n"
        "Compact list source:CL-001,source:CL-002 and source:CL-001，source:CL-002\n"
        "Bracketed punctuation [source:CL-001。] and [src:CL-001，]"
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001", "CL-002"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-002", "CL-002", "resolved"),
        ("source:CL-001", "CL-001", "resolved"),
        ("source:CL-002", "CL-002", "resolved"),
        ("[source:CL-001。]", "CL-001", "resolved"),
        ("[src:CL-001，]", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_treats_markdown_formatting_as_delimiter() -> None:
    text = (
        "Code marker `source:SOURCEA_ABC123` and "
        "bold marker **source:SOURCEA_ABC123** and "
        "strike marker ~~source:SOURCEA_ABC123~~ and "
        "italic marker _source:SOURCEA_ABC123_ and "
        "strong marker __source:SOURCEA_ABC123__."
    )

    markers = parse_internal_citation_markers(
        text,
        valid_claim_ids={"SOURCEA_ABC123"},
    )

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:SOURCEA_ABC123", "SOURCEA_ABC123", "resolved"),
        ("source:SOURCEA_ABC123", "SOURCEA_ABC123", "resolved"),
        ("source:SOURCEA_ABC123", "SOURCEA_ABC123", "resolved"),
        ("source:SOURCEA_ABC123", "SOURCEA_ABC123", "resolved"),
        ("source:SOURCEA_ABC123", "SOURCEA_ABC123", "resolved"),
    ]


def test_parse_internal_citation_markers_rejects_pathlike_bare_suffixes() -> None:
    text = (
        "Path suffix source:CL-001/path "
        "query suffix source:CL-001?x "
        "fragment suffix source:CL-001#frag "
        "extension suffix source:CL-001.txt "
        "sentence marker source:CL-001."
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-001", "CL-001", "resolved"),
    ]
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]


def test_parse_internal_citation_markers_applies_candidate_rules_consistently() -> None:
    text = "[source:CL-001/path] source:CL-001/path [source:CL-001.] source:CL-001."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.kind, marker.claim_id, marker.status) for marker in markers] == [
        ("bracketed_source_marker", "CL-001/path", "unresolved"),
        ("bracketed_source_marker", "CL-001", "resolved"),
        ("bare_source_marker", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_does_not_match_bare_id_inside_longer_token() -> None:
    text = (
        "CL-001-extra and CL-001A are not the claim CL-001, "
        "nor are CL-001/path CL-001?x CL-001#frag CL-001.txt, "
        "but (CL-001) and CL-001. are."
    )

    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]


def test_parse_internal_citation_markers_keeps_source_like_prose_out_of_results() -> None:
    text = (
        "Primary source:company filing.\n"
        "Source:FDA clinical registry.\n"
        "Link source:https://example.com/report.\n"
        "Unknown internal source:CL-404.\n"
    )

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("source:CL-404", "CL-404", "unresolved"),
    ]


def test_claim_id_mentions_for_ledger_uses_ledger_stems_without_placeholder_false_positives() -> None:
    text = (
        "Current SOURCEA_ABC123 was audited. "
        "Stale SOURCEA_OLD999 should be caught. "
        "Explicit unknown [src:SOURCEA_MISSING777] should be caught. "
        "Template [src:<claim_id>] is documentation, not a claim. "
        "Unrelated Q2-2026 and check_id citation_format are not claim IDs."
    )

    assert set(
        claim_id_mentions_for_ledger(
            text,
            valid_claim_ids={"SOURCEA_ABC123"},
        )
    ) == {"SOURCEA_ABC123", "SOURCEA_OLD999", "SOURCEA_MISSING777"}


def test_claim_id_mentions_for_ledger_does_not_treat_control_ids_as_stale_claims() -> None:
    text = (
        "Audited CL-001 and stale CL-002. "
        "Finding CL-COVERAGE and CL-format describe the audit checklist, not claims."
    )

    assert set(
        claim_id_mentions_for_ledger(
            text,
            valid_claim_ids={"CL-001"},
        )
    ) == {"CL-001", "CL-002"}
