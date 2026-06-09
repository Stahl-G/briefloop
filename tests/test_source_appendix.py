from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.outputs.source_appendix import (
    build_source_appendix,
    cited_claim_ids,
)


def _write_ledger(path: Path, claims: list[dict]) -> None:
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


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
        },
    }


def test_cited_claim_ids_dedupe_in_first_appearance_order():
    markdown = (
        "Alpha [src:SYN_CLAIM_002]\n"
        "Beta [src:SYN_CLAIM_001]\n"
        "Alpha again [src:SYN_CLAIM_002]\n"
    )

    assert cited_claim_ids(markdown) == ["SYN_CLAIM_002", "SYN_CLAIM_001"]


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
                metadata={"source_title": "Shared Source", "publisher": "Example News"},
            ),
            _claim(
                "SYN_CLAIM_002",
                source_id="SYN_SRC_SHARED",
                source_url="https://example.com/shared",
                statement="Second cited statement.",
                evidence_text="Evidence for second cited statement must not render.",
                metadata={"source_title": "Shared Source", "publisher": "Example News"},
            ),
            _claim(
                "SYN_CLAIM_UNUSED",
                source_id="SYN_SRC_UNUSED",
                source_url="https://example.com/unused",
                statement="Unused statement.",
                evidence_text="Unused evidence must not render.",
                metadata={"source_title": "Unused Source", "publisher": "Example News"},
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


def test_source_appendix_missing_cited_claim_warns_without_reader_leak(tmp_path: Path):
    ledger = tmp_path / "claim_ledger.json"
    _write_ledger(
        ledger,
        [
            _claim(
                "SYN_CLAIM_001",
                source_id="SYN_SRC_001",
                metadata={"source_title": "Visible Source", "publisher": "Example News"},
            ),
        ],
    )
    audited = "Known [src:SYN_CLAIM_001]\nMissing [src:SYN_CLAIM_MISSING]\n"

    result = build_source_appendix(audited_markdown=audited, ledger_path=ledger)

    assert result.status == "generated_with_warnings"
    assert result.source_count == 1
    assert any("not found" in warning for warning in result.warnings)
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
                },
            ),
        ],
    )

    result = build_source_appendix(
        audited_markdown="Local source claim. [src:SYN_CLAIM_001]\n",
        ledger_path=ledger,
    )

    assert result.source_count == 1
    assert "Local workspace source" in result.markdown
    assert "/Users/" not in result.markdown
    assert "C:\\Users" not in result.markdown
    assert "file://" not in result.markdown
    assert result.warnings
