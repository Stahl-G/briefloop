from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.outputs.reader_projection import build_reader_projection
from multi_agent_brief.outputs.reader_residue_taxonomy import (
    ReaderProjectionContractError,
    project_reader_markdown,
    source_appendix_reference_markdown,
)
from tests.helpers import sha256_file


def _write_claim_ledger(path: Path, claim_ids: list[str]) -> None:
    claims = [
        {
            "claim_id": claim_id,
            "statement": f"ExampleCo source-backed statement for {claim_id}.",
            "source_id": f"SRC-{index:03d}",
            "evidence_text": f"ExampleCo source-backed statement for {claim_id}.",
            "source_url": f"https://example.com/exampleco-demo-{index}",
            "source_type": "web_search",
            "metadata": {
                "source_title": f"ExampleCo Source {index}",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        }
        for index, claim_id in enumerate(claim_ids, start=1)
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_single_claim_ledger(path: Path, *, claim_id: str = "CL-001") -> None:
    _write_claim_ledger(path, [claim_id])


def _projection_workspace(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    return output_dir, intermediate


def test_reader_projection_writes_candidate_without_delivery_promotion(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    before_sha = sha256_file(audited)

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-test",
    )

    assert sha256_file(audited) == before_sha
    assert Path(result.candidate_dir) == intermediate / "finalize_candidate" / "tx-test"
    assert Path(result.reader_brief).exists()
    assert result.source_appendix_generation == "generated"
    assert result.source_appendix_source_count == 1
    assert result.reader_projection["transform_type"] == "reader_projection"
    assert result.reader_projection["source_sha256"] == before_sha
    assert result.reader_projection["output_sha256"] == sha256_file(Path(result.reader_brief))
    assert "citation_projection" in result.reader_projection["applied_operations"]
    assert "[src:CL-001]" not in result.reader_markdown
    assert "CL-001" not in result.reader_markdown
    assert "Source Appendix" in result.reader_markdown
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


def test_reader_projection_rejects_unmarked_internal_appendix_residue(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:CL-001]\n\n"
        "## 来源附录\n\n"
        "Claim Ledger: CL-0001 from input/sources/source-001.md\n",
        encoding="utf-8",
    )

    with pytest.raises(ReaderProjectionContractError) as excinfo:
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown"],
            transaction_id="tx-residue",
        )

    assert excinfo.value.findings[0].kind == "unmarked_source_appendix"
    assert not (intermediate / "finalize_candidate" / "tx-residue").exists()
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


def test_reader_projection_handles_bare_source_marker_as_one_token(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    _write_single_claim_ledger(intermediate / "claim_ledger.json", claim_id="CL-0020")
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility src:CL-0020\n",
        encoding="utf-8",
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-bare-src",
    )

    assert "src:" not in result.reader_markdown
    assert "CL-0020" not in result.reader_markdown
    assert "[S1]" in result.reader_markdown


def test_reader_projection_normalizes_source_marker_for_appendix_lookup(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [source:CL-001]\n",
        encoding="utf-8",
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-source-marker",
    )

    assert result.source_appendix_generation == "generated"
    assert result.source_appendix_source_count == 1
    assert "[S1]" in result.reader_markdown
    assert "[source:CL-001]" not in result.reader_markdown
    assert "CL-001" not in result.reader_markdown


@pytest.mark.parametrize(
    "markdown",
    [
        "Claim needs support. [src:CL-404]\n",
        "Claim needs support src:CL-404\n",
    ],
)
def test_reader_projection_rejects_unresolved_source_markers(markdown: str) -> None:
    with pytest.raises(ReaderProjectionContractError) as excinfo:
        project_reader_markdown(markdown, citation_labels={})

    assert "unresolved_source_marker" in {
        finding.kind for finding in excinfo.value.findings
    }


@pytest.mark.parametrize(
    "markdown",
    [
        "Claim needs support source:CL-001-extra\n",
        "Claim needs support src:CL-001-extra\n",
        "Claim needs support src:CL-001A\n",
    ],
)
def test_reader_projection_rejects_partial_prefix_bare_source_markers(
    markdown: str,
) -> None:
    with pytest.raises(ReaderProjectionContractError) as excinfo:
        project_reader_markdown(markdown, citation_labels={"CL-001": "S1"})

    assert {finding.kind for finding in excinfo.value.findings} == {
        "malformed_source_marker"
    }


@pytest.mark.parametrize(
    "markdown",
    [
        "Chart footnote. Source: FDA dashboard.\n",
        "Primary source: company filing.\n",
        "The source: company filing supports this statement.\n",
        "来源：公司公告。\n",
    ],
)
def test_reader_projection_preserves_reader_safe_source_label(markdown: str) -> None:

    result = project_reader_markdown(markdown, citation_labels={})

    assert result.markdown == markdown.strip()
    assert result.applied_operations == []
    assert source_appendix_reference_markdown(markdown) == markdown


@pytest.mark.parametrize(
    "markdown",
    [
        "Claim needs support source:CL-001\n",
        "Claim needs support src:SYN_CLAIM_001\n",
        "Claim needs support [source:SYN_CLAIM_001]\n",
    ],
)
def test_reader_projection_supports_shared_source_marker_claim_id_grammar(
    markdown: str,
) -> None:
    claim_id = "CL-001" if "CL-001" in markdown else "SYN_CLAIM_001"
    result = project_reader_markdown(
        markdown,
        citation_labels={claim_id: "S7"},
    )

    assert result.markdown == "Claim needs support [S7]"


def test_source_appendix_and_reader_projection_share_source_marker_parser(
    tmp_path: Path,
) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    _write_single_claim_ledger(
        intermediate / "claim_ledger.json",
        claim_id="SYN_CLAIM_001",
    )
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nReader-visible claim source:SYN_CLAIM_001\n",
        encoding="utf-8",
    )

    assert (
        source_appendix_reference_markdown("Reader-visible claim source:SYN_CLAIM_001")
        == "Reader-visible claim [src:SYN_CLAIM_001]"
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-shared-source-parser",
    )

    assert result.source_appendix_cited_claim_count == 1
    assert sorted(result.source_appendix_claim_map) == ["SYN_CLAIM_001"]
    assert "[S1]" in result.reader_markdown
    assert "SYN_CLAIM_001" not in result.reader_markdown


def test_reader_projection_maps_citation_like_bare_claim_id(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    _write_single_claim_ledger(intermediate / "claim_ledger.json", claim_id="CL-0002")
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility; see CL-0002.\n",
        encoding="utf-8",
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-bare-citation",
    )

    assert "CL-0002" not in result.reader_markdown
    assert "see [S1]" in result.reader_markdown


def test_reader_projection_rejects_ambiguous_bare_claim_ids(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    _write_claim_ledger(intermediate / "claim_ledger.json", ["CL-0002", "CL-0003"])
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nMedium-confidence claims (CL-0002, CL-0003) are directional.\n",
        encoding="utf-8",
    )

    with pytest.raises(ReaderProjectionContractError) as excinfo:
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown"],
            transaction_id="tx-ambiguous",
        )

    kinds = {finding.kind for finding in excinfo.value.findings}
    assert "bare_claim_id" in kinds
    assert not (output_dir / "delivery").exists()


def test_reader_projection_rewrites_standard_claim_ledger_disclaimer(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nThis brief draws from the frozen Claim Ledger.\n\n"
        "ExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    before_sha = sha256_file(audited)

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-disclaimer",
    )

    assert sha256_file(audited) == before_sha
    assert "frozen Claim Ledger" not in result.reader_markdown
    assert "registered source evidence" in result.reader_markdown
    assert "disclaimer_rewrite" in result.reader_projection["applied_operations"]


def test_reader_projection_rejects_local_path_instead_of_deleting_it(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nSee /Users/example/input/sources/source-001.md for details.\n",
        encoding="utf-8",
    )

    with pytest.raises(ReaderProjectionContractError) as excinfo:
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown"],
            transaction_id="tx-local-path",
        )

    assert "local_path" in {finding.kind for finding in excinfo.value.findings}
    assert not (output_dir / "delivery").exists()


def test_reader_projection_rejects_unterminated_projectable_block(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "<!-- briefloop:projectable-reader-start -->\n"
        "Claim Ledger internal note.\n\n"
        "## Reader Section That Must Not Be Silently Dropped\n\n"
        "Reader-safe text.\n",
        encoding="utf-8",
    )

    with pytest.raises(ReaderProjectionContractError) as excinfo:
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown"],
            transaction_id="tx-open-block",
        )

    assert "malformed_projectable_block" in {
        finding.kind for finding in excinfo.value.findings
    }
    assert not (intermediate / "finalize_candidate" / "tx-open-block").exists()
    assert not (output_dir / "delivery").exists()


def test_reader_projection_excludes_projectable_block_citations_from_appendix(
    tmp_path: Path,
) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    _write_claim_ledger(intermediate / "claim_ledger.json", ["CL-001", "CL-002"])
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "Reader-visible claim. [src:CL-001]\n\n"
        "<!-- briefloop:projectable-reader-start -->\n"
        "Internal-only drafting note. [src:CL-002]\n"
        "<!-- briefloop:projectable-reader-end -->\n",
        encoding="utf-8",
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-block-citation",
    )

    assert result.source_appendix_generation == "generated"
    assert result.source_appendix_cited_claim_count == 1
    assert result.source_appendix_resolved_claim_count == 1
    assert result.source_appendix_source_count == 1
    assert sorted(result.source_appendix_claim_map) == ["CL-001"]
    assert "CL-002" not in result.reader_markdown
    assert "ExampleCo Source 2" not in result.reader_markdown
    assert "marked_block_removal" in result.reader_projection["applied_operations"]
    assert "[S1]" in result.reader_markdown


def test_reader_projection_rejects_pathlike_transaction_id_without_deleting_intermediate(
    tmp_path: Path,
) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    (intermediate / "finalize_candidate").mkdir()
    sentinel = intermediate / "do_not_delete.txt"
    sentinel.write_text("still here", encoding="utf-8")

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown"],
        transaction_id="..",
    )

    assert sentinel.read_text(encoding="utf-8") == "still here"
    candidate = Path(result.candidate_dir)
    assert candidate.parent == intermediate / "finalize_candidate"
    assert candidate.name not in {".", ".."}
    assert Path(result.reader_brief).exists()


def test_reader_projection_refuses_same_transaction_id_overwrite(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    audited = intermediate / "audited_brief.md"
    audited.write_text(
        "# Brief\n\nFirst candidate content. [src:CL-001]\n",
        encoding="utf-8",
    )

    first = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown"],
        transaction_id="tx-same",
    )
    first_reader = Path(first.reader_brief)
    first_text = first_reader.read_text(encoding="utf-8")

    audited.write_text(
        "# Brief\n\nSecond candidate content. [src:CL-001]\n",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="already exists"):
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown"],
            transaction_id="tx-same",
        )

    assert first_reader.read_text(encoding="utf-8") == first_text
    assert "Second candidate content" not in first_reader.read_text(encoding="utf-8")


def test_reader_projection_cleans_failed_candidate_for_same_transaction_retry(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n",
        encoding="utf-8",
    )
    candidate = intermediate / "finalize_candidate" / "tx-retry"

    with pytest.raises(FileNotFoundError):
        build_reader_projection(
            output_dir=output_dir,
            output_formats=["markdown", "source_appendix"],
            transaction_id="tx-retry",
        )

    assert not candidate.exists()

    _write_single_claim_ledger(intermediate / "claim_ledger.json")
    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown", "source_appendix"],
        transaction_id="tx-retry",
    )

    assert Path(result.candidate_dir) == candidate
    assert Path(result.reader_brief).exists()
    assert result.source_appendix_generation == "generated"
