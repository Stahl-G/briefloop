"""Unit tests for ib_docx.convert image_root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("docx")

from multi_agent_brief.outputs.ib_docx import convert


def _png(path: Path) -> bytes:
    # 1x1 white PNG
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def test_convert_resolves_images_against_explicit_image_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    (output_root / "charts" / "market_data").mkdir(parents=True)
    chart = output_root / "charts" / "market_data" / "trend.png"
    chart.write_bytes(_png(chart))
    scratch = tmp_path / "scratch" / "render-001"
    scratch.mkdir(parents=True)
    markdown = scratch / "reader_brief.md"
    markdown.write_text(
        "# Brief\n\n![Trend](charts/market_data/trend.png)\n",
        encoding="utf-8",
    )
    docx_path = tmp_path / "brief.docx"
    convert(
        markdown,
        docx_path,
        title="Brief",
        template="default",
        image_root=output_root,
    )
    assert docx_path.exists()
    assert docx_path.read_bytes()[:2] == b"PK"


def test_convert_rejects_image_escaping_explicit_image_root(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    scratch = tmp_path / "scratch" / "render-002"
    scratch.mkdir(parents=True)
    markdown = scratch / "reader_brief.md"
    markdown.write_text(
        "# Brief\n\n![Escape](../../elsewhere/trend.png)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inside the report output directory"):
        convert(
            markdown,
            tmp_path / "brief.docx",
            title="Brief",
            template="default",
            image_root=output_root,
        )


def test_convert_keeps_legacy_intermediate_layout_without_image_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    (output_root / "charts").mkdir(parents=True)
    chart = output_root / "charts" / "trend.png"
    chart.write_bytes(_png(chart))
    markdown = output_root / "intermediate" / "audited.md"
    markdown.parent.mkdir(parents=True)
    markdown.write_text(
        "# Brief\n\n![Trend](../charts/trend.png)\n",
        encoding="utf-8",
    )
    docx_path = tmp_path / "brief.docx"
    convert(markdown, docx_path, title="Brief", template="default")
    assert docx_path.read_bytes()[:2] == b"PK"


def test_convert_is_byte_deterministic(tmp_path: Path) -> None:
    import hashlib

    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "trend.png").write_bytes(_png(tmp_path))
    markdown = tmp_path / "brief.md"
    markdown.write_text(
        "# Brief\n\n![Trend](charts/trend.png)\n\nDEMO 周报正文。\n",
        encoding="utf-8",
    )
    first = tmp_path / "one.docx"
    second = tmp_path / "two.docx"
    convert(markdown, first, title="Brief")
    convert(markdown, second, title="Brief")
    assert hashlib.sha256(first.read_bytes()).hexdigest() == hashlib.sha256(
        second.read_bytes()
    ).hexdigest()
