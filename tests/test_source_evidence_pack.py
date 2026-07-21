"""Tests for durable source evidence pack materialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.sources.evidence_pack import (
    SourceEvidencePackError,
    materialize_source_evidence_pack,
)

ROOT = Path(__file__).resolve().parent.parent


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input" / "raw").mkdir(parents=True)
    (ws / "config.yaml").write_text(
        "project:\n  name: Source Evidence Pack Test\n"
        "output:\n  path: output\n"
        "input:\n  path: input\n",
        encoding="utf-8",
    )
    (ws / "user.md").write_text("# User\n", encoding="utf-8")
    return ws






def test_sources_materialize_pack_separates_retrieval_and_underlying_evidence_types(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    cache_dir = ws / "input" / "raw" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "news.json").write_text(
        json.dumps(
            {
                "source_id": "NEWS_001",
                "source_name": "Example News",
                "title": "Article about a published paper",
                "content": "A media article summarizes a paper but is not itself the paper.",
                "metadata": {
                    "retrieval_source_type": "news_media",
                    "category": "industry_media",
                    "publisher": "Example News",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - cached_package\n"
        "cached_package:\n"
        "  enabled: true\n"
        "  paths:\n"
        "    - input/raw/cache\n",
        encoding="utf-8",
    )

    payload = materialize_source_evidence_pack(config_path=ws / "config.yaml")

    record_payload = json.loads((ws / payload["records"][0]["path"]).read_text(encoding="utf-8"))
    assert record_payload["source_type"] == "cached_package"
    assert record_payload["retrieval_source_type"] == "news_media"
    assert record_payload["underlying_evidence_type"] == "media_report"
    assert record_payload["source_category"] == "news_media"
    assert record_payload["raw_underlying_evidence_type"] == "industry_media"


def test_sources_materialize_pack_infers_cached_retrieval_from_legacy_category(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    cache_dir = ws / "input" / "raw" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "legacy-news.json").write_text(
        json.dumps(
            {
                "source_id": "NEWS_002",
                "source_name": "Example Trade Press",
                "title": "Cached article with legacy category",
                "content": "A cached media article reports durable evidence.",
                "metadata": {
                    "category": "industry_media",
                    "publisher": "Example Trade Press",
                    "storage_type": "cached_package",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - cached_package\n"
        "cached_package:\n"
        "  enabled: true\n"
        "  paths:\n"
        "    - input/raw/cache\n",
        encoding="utf-8",
    )

    payload = materialize_source_evidence_pack(config_path=ws / "config.yaml")

    record_payload = json.loads((ws / payload["records"][0]["path"]).read_text(encoding="utf-8"))
    assert record_payload["source_type"] == "cached_package"
    assert record_payload["retrieval_source_type"] == "news_media"
    assert record_payload["underlying_evidence_type"] == "media_report"
    assert record_payload["source_category"] == "news_media"
    assert record_payload["raw_underlying_evidence_type"] == "industry_media"


def test_sources_materialize_pack_unknown_category_stays_explicit_unknown(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    source = ws / "input" / "raw" / "source-unknown.md"
    source.write_text("# Unknown source\n\nDurable evidence text.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Example Unknown\n"
        "      path: input/raw/source-unknown.md\n"
        "      category: handwritten_note\n",
        encoding="utf-8",
    )

    payload = materialize_source_evidence_pack(config_path=ws / "config.yaml")

    record_payload = json.loads((ws / payload["records"][0]["path"]).read_text(encoding="utf-8"))
    assert record_payload["source_category"] == "other"
    assert record_payload["underlying_evidence_type"] == "unknown"
    assert record_payload["raw_underlying_evidence_type"] == "handwritten_note"


def test_sources_materialize_pack_rejects_duplicate_source_ids_before_writing(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    cache_dir = ws / "input" / "raw" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "duplicates.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_id": "DUP_001",
                        "source_name": "Example News",
                        "title": "First record",
                        "content": "First durable evidence text.",
                    },
                    {
                        "source_id": "DUP_001",
                        "source_name": "Example News",
                        "title": "Second record",
                        "content": "Second durable evidence text.",
                    },
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - cached_package\n"
        "cached_package:\n"
        "  enabled: true\n"
        "  paths:\n"
        "    - input/raw/cache\n",
        encoding="utf-8",
    )

    with pytest.raises(SourceEvidencePackError) as excinfo:
        materialize_source_evidence_pack(config_path=ws / "config.yaml")

    assert "duplicate source_id" in str(excinfo.value)
    assert "DUP_001" in str(excinfo.value)
    assert not (ws / "input" / "sources").exists()
    assert not (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").exists()


def test_sources_materialize_pack_refuses_search_only_sources(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )

    with pytest.raises(SourceEvidencePackError) as excinfo:
        materialize_source_evidence_pack(config_path=ws / "config.yaml")

    assert "manual or cached_package" in str(excinfo.value)
    assert not (ws / "input" / "sources").exists()
    assert not (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").exists()


def test_sources_materialize_pack_fails_closed_on_partial_provider_errors(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    source = ws / "input" / "raw" / "source-001.md"
    source.write_text("# Source 001\n\nDurable evidence text.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "    - cached_package\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Regulator Bulletin\n"
        "      path: input/raw/source-001.md\n"
        "cached_package:\n"
        "  enabled: true\n"
        "  paths:\n"
        "    - input/raw/missing-cache\n",
        encoding="utf-8",
    )

    with pytest.raises(SourceEvidencePackError) as excinfo:
        materialize_source_evidence_pack(config_path=ws / "config.yaml")

    assert "provider errors must be resolved" in str(excinfo.value)
    assert "cached_package:ConfigValidationError" in str(excinfo.value)
    assert not (ws / "input" / "sources").exists()
    assert not (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").exists()


def test_sources_materialize_pack_force_refuses_user_source_file(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    source = ws / "input" / "raw" / "source-001.md"
    source.write_text("# Source 001\n\nDurable evidence text.\n", encoding="utf-8")
    source_dir = ws / "input" / "sources"
    source_dir.mkdir(parents=True)
    user_file = source_dir / "source-001.json"
    user_payload = {"schema_version": "user.source.v1", "content": "do not overwrite"}
    user_file.write_text(json.dumps(user_payload, sort_keys=True) + "\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Regulator Bulletin\n"
        "      path: input/raw/source-001.md\n",
        encoding="utf-8",
    )

    with pytest.raises(SourceEvidencePackError) as excinfo:
        materialize_source_evidence_pack(config_path=ws / "config.yaml", force=True)

    assert "can only replace records generated by sources.materialize-pack" in str(excinfo.value)
    assert json.loads(user_file.read_text(encoding="utf-8")) == user_payload
    assert not (ws / "output" / "intermediate" / "source_evidence_pack_manifest.json").exists()


def test_sources_materialize_pack_force_replaces_generated_record(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    source = ws / "input" / "raw" / "source-001.md"
    source.write_text("# Source 001\n\nFirst durable evidence text.\n", encoding="utf-8")
    (ws / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  sources:\n"
        "    - name: Regulator Bulletin\n"
        "      path: input/raw/source-001.md\n",
        encoding="utf-8",
    )

    first = materialize_source_evidence_pack(config_path=ws / "config.yaml")
    record_path = ws / first["records"][0]["path"]
    source.write_text("# Source 001\n\nUpdated durable evidence text.\n", encoding="utf-8")

    second = materialize_source_evidence_pack(config_path=ws / "config.yaml", force=True)

    assert second["records"][0]["path"] == first["records"][0]["path"]
    record_payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert record_payload["source"] == "sources.materialize-pack"
    assert "Updated durable evidence text." in record_payload["content"]












def _write_manifest(ws: Path, *, records: list[dict]) -> Path:
    manifest_path = ws / "output" / "intermediate" / "source_evidence_pack_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    provider_errors: list[dict] = []
    manifest = {
        "schema_version": "mabw.source_evidence_pack_manifest.v1",
        "source": "test",
        "source_config_path": "sources.yaml",
        "durable_provider_names": ["manual"],
        "record_count": len(records),
        "error_count": len(provider_errors),
        "records": records,
        "provider_errors": provider_errors,
        "pack_sha256": _sha256_json([
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "source_id": record["source_id"],
            }
            for record in records
        ]),
        "non_goals": ["semantic_support_assessment"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_json(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
