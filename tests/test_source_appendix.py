from __future__ import annotations

import hashlib
import json
from pathlib import Path

from multi_agent_brief.outputs.source_appendix import (
    build_source_appendix,
    cited_claim_ids,
    replace_claim_citations_with_labels,
)


def _write_ledger(path: Path, claims: list[dict]) -> None:
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _span_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim(
    claim_id: str,
    *,
    source_id: str,
    source_url: str = "https://example.com/source",
    statement: str = "Synthetic statement.",
    evidence_text: str = "Full synthetic evidence text must not render.",
    metadata: dict | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "statement": statement,
        "source_id": source_id,
        "evidence_text": evidence_text,
        "source_url": source_url,
        "source_type": "web_search",
        "metadata": metadata or {
            "source_title": "Example Source",
            "publisher": "Example News",
            "published_at": "2026-06-01",
            "source_category": "news_media",
        },
    }


def test_cited_claim_ids_dedupe_in_first_appearance_order():
    markdown = (
        "Alpha [src:SYN_CLAIM_002]\n"
        "Beta [src:SYN_CLAIM_001]\n"
        "Alpha again [src:SYN_CLAIM_002]\n"
    )

    assert cited_claim_ids(markdown) == ["SYN_CLAIM_002", "SYN_CLAIM_001"]


def test_cited_claim_ids_uses_only_canonical_src_markers():
    markdown = (
        "Canonical [src:CL-001]\n"
        "Deprecated [source:CL-002]\n"
        "Bare src:CL-003 and source:CL-004 stay prose.\n"
        "Raw CL-005 stays residue, not citation.\n"
        "Malformed [src: CL-006] is not an appendix citation.\n"
        "Ordinary Source: Q2-2026 report stays prose.\n"
    )

    assert cited_claim_ids(
        markdown,
        valid_claim_ids={"CL-001", "CL-002", "CL-003", "CL-004", "CL-005", "CL-006"},
    ) == ["CL-001"]


def test_source_appendix_uses_only_cited_claims_and_dedupes_sources(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "SYN_CLAIM_001",
                source_id="SYN_SRC_SHARED",
                source_url="https://example.com/shared",
                statement="First cited statement.",
                evidence_text="Evidence for first cited statement must not render.",
                metadata={"source_title": "Shared Source", "publisher": "Example News", "source_category": "news_media"},
            ),
            _claim(
                "SYN_CLAIM_002",
                source_id="SYN_SRC_SHARED",
                source_url="https://example.com/shared",
                statement="Second cited statement.",
                evidence_text="Evidence for second cited statement must not render.",
                metadata={"source_title": "Shared Source", "publisher": "Example News", "source_category": "news_media"},
            ),
            _claim(
                "SYN_CLAIM_UNUSED",
                source_id="SYN_SRC_UNUSED",
                source_url="https://example.com/unused",
                statement="Unused statement.",
                evidence_text="Unused evidence must not render.",
                metadata={"source_title": "Unused Source", "publisher": "Example News", "source_category": "news_media"},
            ),
        ],
    )
    audited = (
        "Second cited statement. [src:SYN_CLAIM_002]\n"
        "First cited statement. [src:SYN_CLAIM_001]\n"
    )

    result = build_source_appendix(audited_markdown=audited, ledger_path=ledger)

    assert result.source_count == 1
    assert result.cited_claim_count == 2
    assert result.resolved_claim_count == 2
    assert "[S1] Shared Source" in result.markdown
    assert "Used in: 2 claim-backed statements" in result.markdown
    assert "Unused Source" not in result.markdown
    assert "SYN_CLAIM_" not in result.markdown
    assert "SYN_SRC_" not in result.markdown
    assert "Evidence for" not in result.markdown
    assert result.citation_labels == {
        "SYN_CLAIM_002": "S1",
        "SYN_CLAIM_001": "S1",
    }
    assert result.claim_source_map["SYN_CLAIM_001"]["source_label"] == "S1"
    assert result.claim_source_map["SYN_CLAIM_001"]["source_url"] == "https://example.com/shared"
    assert result.claim_source_map["SYN_CLAIM_001"]["source_title"] == "Shared Source"


def test_source_appendix_ignores_noncanonical_and_prose_source_forms(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                metadata={
                    "source_title": "Canonical Source",
                    "publisher": "Example News",
                    "source_category": "news_media",
                },
            ),
            _claim(
                "CL-002",
                source_id="SRC-002",
                metadata={
                    "source_title": "Deprecated Source",
                    "publisher": "Example News",
                    "source_category": "news_media",
                },
            ),
            _claim(
                "CL-003",
                source_id="SRC-003",
                metadata={
                    "source_title": "Bare Source",
                    "publisher": "Example News",
                    "source_category": "news_media",
                },
            ),
        ],
    )
    audited = (
        "Canonical claim. [src:CL-001]\n"
        "Deprecated marker. [source:CL-002]\n"
        "Bare marker. source:CL-003\n"
        "Primary source:10-K filing.\n"
    )

    result = build_source_appendix(audited_markdown=audited, ledger_path=ledger)

    assert result.source_count == 1
    assert result.cited_claim_count == 1
    assert result.resolved_claim_count == 1
    assert result.citation_labels == {"CL-001": "S1"}
    assert "Canonical Source" in result.markdown
    assert "Deprecated Source" not in result.markdown
    assert "Bare Source" not in result.markdown
    assert result.warnings == []


def test_replace_claim_citations_with_reader_source_labels():
    markdown = (
        "Alpha [src:claim-001]\n"
        "Beta [src:claim-missing]\n"
        "Gamma [src:claim-002]\n"
    )

    reader = replace_claim_citations_with_labels(
        markdown,
        {"claim-001": "S1", "claim-002": "S2"},
    )

    assert "Alpha [S1]" in reader
    assert "Gamma [S2]" in reader
    assert "Beta [src:claim-missing]" in reader


def test_replace_claim_citations_preserves_malformed_markers_for_residue_gate():
    markdown = "Empty [src:]\nWhitespace [src: claim-001]\n"

    reader = replace_claim_citations_with_labels(markdown, {"claim-001": "S1"})

    assert "[src:]" in reader
    assert "[src: claim-001]" in reader


def test_replace_claim_citations_dedupes_adjacent_reader_source_labels():
    markdown = "Shared source appears twice. [src:claim-001] [src:claim-002]\n"

    reader = replace_claim_citations_with_labels(
        markdown,
        {"claim-001": "S1", "claim-002": "S1"},
    )

    assert reader == "Shared source appears twice. [S1]"


def test_source_appendix_missing_cited_claim_warns_without_reader_leak(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "SYN_CLAIM_001",
                source_id="SYN_SRC_001",
                metadata={"source_title": "Visible Source", "publisher": "Example News", "source_category": "news_media"},
            ),
        ],
    )
    audited = "Known [src:SYN_CLAIM_001]\nMissing [src:SYN_CLAIM_MISSING]\n"

    result = build_source_appendix(audited_markdown=audited, ledger_path=ledger)

    assert result.status == "generated_with_warnings"
    assert result.source_count == 1
    assert any("could not be resolved" in warning for warning in result.warnings)
    assert "Visible Source" in result.markdown
    assert "SYN_CLAIM_MISSING" not in result.markdown


def test_source_appendix_filters_local_paths_and_file_urls(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "SYN_CLAIM_001",
                source_id="SYN_SRC_001",
                source_url="file:///Users/example/private/source.md",
                metadata={
                    "source_title": "/Users/example/private/source.md",
                    "publisher": "C:\\Users\\example\\Private",
                    "retrieved_at": "2026-06-01",
                    "source_category": "peer_reviewed_paper",
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="Local source claim. [src:SYN_CLAIM_001]\n",
        ledger_path=ledger,
    )

    assert result.source_count == 1
    assert "Source record" in result.markdown
    assert "/Users/" not in result.markdown
    assert "C:\\Users" not in result.markdown
    assert "file://" not in result.markdown
    assert result.warnings


def test_source_appendix_renders_local_file_source_identity_without_url(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            {
                "claim_id": "CL-001",
                "statement": "Example journal reported the result.",
                "source_id": "SRC-001",
                "evidence_text": "Evidence text must not render.",
                "source_type": "local_file",
                "metadata": {
                    "source_title": "Example Journal Article",
                    "publisher": "Example Journal",
                    "published_at": "2026-06-01",
                    "source_category": "peer_reviewed_paper",
                },
            }
        ],
    )

    result = build_source_appendix(
        audited_markdown="Example journal reported the result. [src:CL-001]\n",
        ledger_path=ledger,
    )

    assert result.status == "generated"
    assert "### [S1] Example Journal Article" in result.markdown
    assert "- Source category: Peer-reviewed paper" in result.markdown
    assert "- Publisher/Institution: Example Journal" in result.markdown
    assert "- Provider type: local_file" in result.markdown
    assert "- URL:" not in result.markdown
    assert "Local workspace source" not in result.markdown


def test_source_appendix_does_not_use_source_id_as_reader_title(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            {
                "claim_id": "CL-001",
                "statement": "Example source reported the result.",
                "source_id": "SRC-001",
                "evidence_text": "Evidence text must not render.",
                "source_type": "local_file",
                "metadata": {
                    "publisher": "Example Institution",
                    "source_category": "market_report",
                },
            }
        ],
    )

    result = build_source_appendix(
        audited_markdown="Example source reported the result. [src:CL-001]\n",
        ledger_path=ledger,
    )

    assert result.status == "generated_with_warnings"
    assert "### [S1] Source record" in result.markdown
    assert "SRC-001" not in result.markdown
    assert "Source metadata missing source title/name." in result.markdown


def test_source_appendix_omits_invalid_url_and_reports_note(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="GEN Top 10 source title",
                metadata={
                    "source_title": "Example Source",
                    "publisher": "Example Publisher",
                    "source_category": "market_report",
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="Example source reported the result. [src:CL-001]\n",
        ledger_path=ledger,
    )

    assert result.status == "generated_with_warnings"
    assert "GEN Top 10" not in result.markdown
    assert "- URL:" not in result.markdown
    assert "## Appendix Notes" in result.markdown
    assert "Omitted source URL because it was not an HTTP(S) URL." in result.markdown


def test_source_appendix_renders_http_url_as_markdown_link(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="https://example.com/source",
                metadata={
                    "source_title": "Example Source",
                    "publisher": "Example Publisher",
                    "source_category": "market_report",
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="Example source reported the result. [src:CL-001]\n",
        ledger_path=ledger,
    )

    assert "- URL: <https://example.com/source>" in result.markdown


def test_source_appendix_renders_markdown_safe_url_with_metacharacters(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    url = "https://example.com/Foo_(bar)]"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url=url,
                metadata={
                    "source_title": "Example Source",
                    "publisher": "Example Publisher",
                    "source_category": "market_report",
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="Example source reported the result. [src:CL-001]\n",
        ledger_path=ledger,
    )

    assert f"- URL: <{url}>" in result.markdown


def test_source_appendix_filters_internal_id_shaped_metadata(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "SYN_CLAIM_001",
                source_id="SYN_SRC_001",
                metadata={
                    "source_title": "SYN_SRC_001",
                    "publisher": "CLAIM_INTERNAL_001",
                    "published_at": "2026-06-01",
                    "source_category": "news_media",
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="ID-shaped source metadata. [src:SYN_CLAIM_001]\n",
        ledger_path=ledger,
    )

    assert "SYN_SRC_001" not in result.markdown
    assert "CLAIM_INTERNAL_001" not in result.markdown
    assert "Source record" in result.markdown
    assert result.warnings
    assert "Source metadata missing source title/name." in result.markdown
    assert "Source metadata missing publisher/institution." in result.markdown


def test_source_appendix_adds_reader_safe_span_summary_and_audit_trace(tmp_path: Path):
    ws = tmp_path / "workspace"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    source_text = f"Intro.\n{raw_excerpt}\nOutro.\n"
    source_path = source_dir / "source-001.md"
    source_path.write_text(source_text, encoding="utf-8")
    start = source_text.index(raw_excerpt)
    ledger = intermediate / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="https://example.com/source-001",
                statement="ExampleCo shipments reached 12 MW.",
                evidence_text=raw_excerpt,
                metadata={
                    "source_title": "ExampleCo Source",
                    "publisher": "Example News",
                    "published_at": "2026-06-01",
                    "source_category": "news_media",
                    "retrieval_source_type": "news_media",
                    "underlying_evidence_type": "media_report",
                },
            ),
        ],
    )
    registry = intermediate / "evidence_span_registry.json"
    registry.write_text(
        json.dumps({
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "local_file",
                    "source_tier": "primary",
                    "source_path": "input/sources/source-001.md",
                    "retrieved_at": "2026-06-02",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": raw_excerpt,
                            "hash": _span_hash(raw_excerpt),
                            "span_role": "numeric_observation",
                            "char_start": start,
                            "char_end": start + len(raw_excerpt),
                        }
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    result = build_source_appendix(
        audited_markdown="ExampleCo shipments reached 12 MW. [src:CL-001]\n",
        ledger_path=ledger,
        evidence_span_registry_path=registry,
        workspace=ws,
    )

    assert result.trace_status == "generated"
    assert result.trace_source_count == 1
    assert result.trace_span_count == 1
    assert "- Retrieval source type: News media" in result.markdown
    assert "- Underlying evidence type: Media report" in result.markdown
    assert "Evidence trace: 1 span; roles: numeric observation" in result.markdown
    assert "ESP-001-01" not in result.markdown
    assert "SRC-001" not in result.markdown
    assert "input/sources/source-001.md" not in result.markdown
    assert raw_excerpt not in result.markdown
    assert "support sufficiency" not in result.markdown.lower()
    assert "ESP-001-01" in result.trace_markdown
    assert "SRC-001" in result.trace_markdown
    assert "input/sources/source-001.md" in result.trace_markdown
    assert "Claim IDs: `CL-001`" in result.trace_markdown
    source_bytes = source_path.read_bytes()
    assert f"Source SHA-256: `{hashlib.sha256(source_bytes).hexdigest()}`" in result.trace_markdown
    assert f"Source size bytes: {len(source_bytes)}" in result.trace_markdown
    assert f"Raw excerpt hash: `{_span_hash(raw_excerpt)}`" in result.trace_markdown
    assert f"Offsets: {start}..{start + len(raw_excerpt)}" in result.trace_markdown
    assert raw_excerpt in result.trace_markdown
    assert "traceability surface only" in result.trace_markdown


def test_source_appendix_trace_includes_metadata_completeness_warnings(tmp_path: Path):
    ws = tmp_path / "workspace"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    source_path = source_dir / "source-001.md"
    source_path.write_text(raw_excerpt + "\n", encoding="utf-8")
    ledger = intermediate / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="https://example.com/source-001",
                statement="ExampleCo shipments reached 12 MW.",
                evidence_text=raw_excerpt,
                metadata={
                    "source_category": "news_media",
                },
            ),
        ],
    )
    registry = intermediate / "evidence_span_registry.json"
    registry.write_text(
        json.dumps({
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "local_file",
                    "source_tier": "primary",
                    "source_path": "input/sources/source-001.md",
                    "retrieved_at": "2026-06-02",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": raw_excerpt,
                            "hash": _span_hash(raw_excerpt),
                            "span_role": "numeric_observation",
                        }
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    result = build_source_appendix(
        audited_markdown="ExampleCo shipments reached 12 MW. [src:CL-001]\n",
        ledger_path=ledger,
        evidence_span_registry_path=registry,
        workspace=ws,
    )

    assert result.trace_status == "generated"
    assert "Metadata warnings:" in result.trace_markdown
    assert "Source metadata missing source title/name." in result.trace_markdown
    assert "Source metadata missing publisher/institution." in result.trace_markdown
    assert "Source metadata missing retrieval source type." in result.trace_markdown
    assert "Source metadata missing underlying evidence type." in result.trace_markdown
    assert "Claim IDs:" not in result.markdown
    assert "SRC-001" not in result.markdown
    assert "ESP-001-01" not in result.markdown


def test_source_appendix_aggregates_trace_spans_for_deduped_source_ids(tmp_path: Path):
    ws = tmp_path / "workspace"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    excerpt_1 = "ExampleCo said module shipments reached 12 MW in Q2."
    excerpt_2 = "ExampleCo said module shipments reached 18 MW in Q3."
    (source_dir / "source-001.md").write_text(f"Intro.\n{excerpt_1}\n", encoding="utf-8")
    (source_dir / "source-002.md").write_text(f"Intro.\n{excerpt_2}\n", encoding="utf-8")
    ledger = intermediate / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="https://example.com/shared",
                statement="ExampleCo shipments reached 12 MW.",
                evidence_text=excerpt_1,
                metadata={"source_title": "Shared Source", "publisher": "Example News", "source_category": "news_media"},
            ),
            _claim(
                "CL-002",
                source_id="SRC-002",
                source_url="https://example.com/shared",
                statement="ExampleCo shipments reached 18 MW.",
                evidence_text=excerpt_2,
                metadata={"source_title": "Shared Source", "publisher": "Example News", "source_category": "news_media"},
            ),
        ],
    )
    registry = intermediate / "evidence_span_registry.json"
    registry.write_text(
        json.dumps({
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "local_file",
                    "source_tier": "primary",
                    "source_path": "input/sources/source-001.md",
                    "retrieved_at": "2026-06-02",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": excerpt_1,
                            "hash": _span_hash(excerpt_1),
                            "span_role": "numeric_observation",
                        }
                    ],
                },
                {
                    "source_id": "SRC-002",
                    "source_type": "local_file",
                    "source_tier": "primary",
                    "source_path": "input/sources/source-002.md",
                    "retrieved_at": "2026-06-02",
                    "spans": [
                        {
                            "span_id": "ESP-002-01",
                            "raw_excerpt": excerpt_2,
                            "hash": _span_hash(excerpt_2),
                            "span_role": "direct_statement",
                        }
                    ],
                },
            ],
        }),
        encoding="utf-8",
    )

    result = build_source_appendix(
        audited_markdown=(
            "ExampleCo shipments reached 12 MW. [src:CL-001]\n"
            "ExampleCo shipments reached 18 MW. [src:CL-002]\n"
        ),
        ledger_path=ledger,
        evidence_span_registry_path=registry,
        workspace=ws,
    )

    assert result.source_count == 1
    assert result.records[0].source_ids == ["SRC-001", "SRC-002"]
    assert result.trace_status == "generated"
    assert result.trace_span_count == 2
    assert "Evidence trace: 2 spans; roles: direct statement, numeric observation" in result.markdown
    assert "ESP-001-01" in result.trace_markdown
    assert "ESP-002-01" in result.trace_markdown
    assert "SRC-001" in result.trace_markdown
    assert "SRC-002" in result.trace_markdown


def test_source_appendix_skips_trace_non_blockingly_when_source_pack_mismatches(tmp_path: Path):
    ws = tmp_path / "workspace"
    intermediate = ws / "output" / "intermediate"
    source_dir = ws / "input" / "sources"
    intermediate.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    (source_dir / "source-001.md").write_text("Different source bytes.\n", encoding="utf-8")
    raw_excerpt = "ExampleCo said module shipments reached 12 MW in Q2."
    ledger = intermediate / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "CL-001",
                source_id="SRC-001",
                source_url="https://example.com/source-001",
                statement="ExampleCo shipments reached 12 MW.",
                evidence_text=raw_excerpt,
                metadata={"source_title": "ExampleCo Source", "publisher": "Example News", "source_category": "news_media"},
            ),
        ],
    )
    registry = intermediate / "evidence_span_registry.json"
    registry.write_text(
        json.dumps({
            "schema_version": "mabw.evidence_span_registry.v1",
            "sources": [
                {
                    "source_id": "SRC-001",
                    "source_type": "local_file",
                    "source_tier": "primary",
                    "source_path": "input/sources/source-001.md",
                    "retrieved_at": "2026-06-02",
                    "spans": [
                        {
                            "span_id": "ESP-001-01",
                            "raw_excerpt": raw_excerpt,
                            "hash": _span_hash(raw_excerpt),
                            "span_role": "numeric_observation",
                        }
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    result = build_source_appendix(
        audited_markdown="ExampleCo shipments reached 12 MW. [src:CL-001]\n",
        ledger_path=ledger,
        evidence_span_registry_path=registry,
        workspace=ws,
    )

    assert result.status == "generated"
    assert result.source_count == 1
    assert result.trace_status == "skipped"
    assert result.trace_markdown == ""
    assert result.trace_span_count == 0
    assert any("does not match source bytes" in warning for warning in result.trace_warnings)
