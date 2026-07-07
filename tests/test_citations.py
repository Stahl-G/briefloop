from __future__ import annotations

from multi_agent_brief.core.citations import (
    extract_src_ref_ids,
    parse_internal_citation_markers,
    resolved_internal_citation_ids,
    unresolved_internal_citation_markers,
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


def test_parse_internal_citation_markers_uses_ledger_membership_not_id_family() -> None:
    text = "Generated ID resolves if the ledger owns it. [src:SOURCEA_9F8E7D6C]"

    assert resolved_internal_citation_ids(
        text,
        valid_claim_ids={"SOURCEA_9F8E7D6C"},
    ) == ["SOURCEA_9F8E7D6C"]


def test_parse_internal_citation_markers_reports_empty_and_unknown_src_markers() -> None:
    text = "Empty [src:] and unknown [src:CL-404]."

    unresolved = unresolved_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status, marker.message) for marker in unresolved] == [
        ("[src:]", "", "malformed", "source marker is empty"),
        (
            "[src:CL-404]",
            "CL-404",
            "unresolved",
            "source marker does not resolve to a Claim Ledger ID",
        ),
    ]


def test_parse_internal_citation_markers_reports_malformed_src_claim_id() -> None:
    text = "Malformed [src:CL-001/path], [src:CL 001], [src: CL-001], [src:CL-001 ], and [src:\tCL-001]."

    unresolved = unresolved_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in unresolved] == [
        ("[src:CL-001/path]", "CL-001/path", "malformed"),
        ("[src:CL 001]", "CL 001", "malformed"),
        ("[src: CL-001]", " CL-001", "malformed"),
        ("[src:CL-001 ]", "CL-001 ", "malformed"),
        ("[src:\tCL-001]", "\tCL-001", "malformed"),
    ]
    assert extract_src_ref_ids(text) == []


def test_parse_internal_citation_markers_does_not_let_broken_marker_hide_later_citation() -> None:
    text = "Broken [src:CL-404\nNext [src:CL-001]."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("[src:CL-404", "CL-404", "malformed"),
        ("[src:CL-001]", "CL-001", "resolved"),
    ]
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == ["CL-001"]


def test_parse_internal_citation_markers_reports_nested_marker_without_consuming_later_marker() -> None:
    text = "Nested [src:CL-404 [src:CL-001]."

    markers = parse_internal_citation_markers(text, valid_claim_ids={"CL-001"})

    assert [(marker.raw, marker.claim_id, marker.status) for marker in markers] == [
        ("[src:CL-404 ", "CL-404 ", "malformed"),
        ("[src:CL-001]", "CL-001", "resolved"),
    ]


def test_parse_internal_citation_markers_leaves_noncanonical_forms_unparsed() -> None:
    text = (
        "[source:CL-001]\n"
        "src:CL-001\n"
        "source:CL-001\n"
        "CL-001\n"
        "CLM-001\n"
        "CLAIM_001\n"
        "SYN_CLAIM_001\n"
        "SOURCEA_ABC123\n"
    )

    assert parse_internal_citation_markers(
        text,
        valid_claim_ids={
            "CL-001",
            "CLM-001",
            "CLAIM_001",
            "SYN_CLAIM_001",
            "SOURCEA_ABC123",
        },
    ) == []
    assert resolved_internal_citation_ids(text, valid_claim_ids={"CL-001"}) == []


def test_parse_internal_citation_markers_preserves_ordinary_source_prose() -> None:
    text = (
        "Primary source: company filing.\n"
        "Primary source:10-K filing.\n"
        "Source: Q2-2026 report.\n"
        "URL label source:https://example.com/report.\n"
        "For setup visit https://example.com/source:CL-001 path.\n"
    )

    assert parse_internal_citation_markers(text, valid_claim_ids={"CL-001"}) == []


def test_parse_internal_citation_markers_ignores_include_bare_claim_ids_compat_arg() -> None:
    text = "Bare CL-001 is not projectable even with the legacy compatibility flag."

    assert (
        parse_internal_citation_markers(
            text,
            valid_claim_ids={"CL-001"},
            include_bare_claim_ids=True,
        )
        == []
    )
