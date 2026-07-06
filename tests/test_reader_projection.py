from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.outputs.reader_projection import build_reader_projection
from tests.helpers import sha256_file


def _write_single_claim_ledger(path: Path, *, claim_id: str = "CL-001") -> None:
    claims = [
        {
            "claim_id": claim_id,
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SRC-001",
            "evidence_text": "ExampleCo opened a public demo facility in June 2026.",
            "source_url": "https://example.com/exampleco-demo",
            "source_type": "web_search",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        }
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


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
    assert "[src:CL-001]" not in result.reader_markdown
    assert "CL-001" not in result.reader_markdown
    assert "Source Appendix" in result.reader_markdown
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


def test_reader_projection_surfaces_internal_appendix_residue(tmp_path: Path) -> None:
    output_dir, intermediate = _projection_workspace(tmp_path)
    (intermediate / "audited_brief.md").write_text(
        "# Brief\n\n"
        "ExampleCo opened a public demo facility. [src:CL-001]\n\n"
        "## Source Appendix\n\n"
        "Claim Ledger: CL-0001 from input/sources/source-001.md\n",
        encoding="utf-8",
    )

    result = build_reader_projection(
        output_dir=output_dir,
        output_formats=["markdown"],
        transaction_id="tx-residue",
    )

    assert result.reader_clean["status"] == "fail"
    kinds = {finding["kind"] for finding in result.reader_clean["sample_findings"]}
    assert {"bare_claim_id", "process_wording"}.issubset(kinds)
    assert not (output_dir / "brief.md").exists()
    assert not (output_dir / "delivery").exists()


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
