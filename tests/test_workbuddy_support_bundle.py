"""Tests for secret-safe WorkBuddy support bundles."""

from __future__ import annotations

import json
from hashlib import sha256
import zipfile
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.workbuddy.support_bundle import (
    package_workbuddy_support_bundle,
    validate_workbuddy_support_bundle,
)

BARE_TOKEN = "sk-" + ("a" * 32)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "input").mkdir(parents=True)
    (ws / "output" / "intermediate" / "gates").mkdir(parents=True)
    (ws / "output" / "delivery").mkdir(parents=True)
    (ws / "private_planning").mkdir()
    (ws / "config.yaml").write_text("project:\n  name: Support\n", encoding="utf-8")
    (ws / "sources.yaml").write_text("source_strategy:\n  profile: conservative\n", encoding="utf-8")
    (ws / "user.md").write_text("Weekly brief request.\n", encoding="utf-8")
    (ws / ".env").write_text("TAVILY_API_KEY=redaction-secret-value\n", encoding="utf-8")
    (ws / "private_planning" / "notes.md").write_text("private plan\n", encoding="utf-8")
    (ws / "input" / "source.md").write_text(
        "Source note\n\napi_key: redaction-secret-value\n",
        encoding="utf-8",
    )
    (ws / "input" / "token.md").write_text(
        f"Plain leaked value {BARE_TOKEN}\n",
        encoding="utf-8",
    )
    (ws / "input" / "multi.yaml").write_text(
        "provider:\n"
        "  api_key: |\n"
        "    multiline-secret-value\n"
        "  name: demo\n",
        encoding="utf-8",
    )
    (ws / "input" / "bad.md").write_bytes(b"TAVILY_API_KEY=super-secret-value\xff")
    (ws / "output" / "intermediate" / "workflow_state.json").write_text(
        json.dumps({"current_stage": "auditor", "run_integrity": {"status": "clean"}}),
        encoding="utf-8",
    )
    (ws / "output" / "intermediate" / "event_log.jsonl").write_text(
        json.dumps({"event_type": "run_initialized"}) + "\n",
        encoding="utf-8",
    )
    (ws / "output" / "intermediate" / "gates" / "auditor_quality_gate_report.json").write_text(
        json.dumps({"status": "pass"}) + "\n",
        encoding="utf-8",
    )
    (ws / "output" / "delivery" / "brief.md").write_text("Reader copy\n", encoding="utf-8")
    (ws / "output" / "delivery_bundle.zip").write_bytes(b"not included")
    return ws


def test_workbuddy_support_bundle_excludes_env_and_redacts_text_secrets(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)

    result = package_workbuddy_support_bundle(workspace=ws, output_dir=tmp_path / "support")

    assert validate_workbuddy_support_bundle(
        zip_path=result.zip_path,
        manifest_path=result.manifest_path,
    ) == []
    assert result.manifest["share_workspace_zip_allowed"] is False
    assert result.manifest["runtime_effect"] == "packaging_only_read_only"
    assert result.manifest["boundary"] == "secret_safe_support_bundle_not_delivery_gate_release_authority"
    assert "zip_filename" in result.manifest
    assert "zip_path" not in result.manifest
    assert "input/source.md" in result.redacted_files
    assert "input/token.md" in result.redacted_files
    assert "input/multi.yaml" in result.redacted_files
    assert any(item["path"] == ".env" and item["reason"] == "secret_env_file" for item in result.excluded_files)
    assert any(
        item["path"] == "input/bad.md" and item["reason"] == "non_utf8_text_file"
        for item in result.excluded_files
    )
    assert any(
        item["path"] == "private_planning/notes.md"
        and item["reason"] == "forbidden_private_or_generated_path"
        for item in result.excluded_files
    )

    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
        assert "workspace/.env" not in names
        assert "workspace/private_planning/notes.md" not in names
        assert "workspace/input/bad.md" not in names
        assert "workspace/output/delivery_bundle.zip" not in names
        assert "workspace/output/intermediate/workflow_state.json" in names
        assert "workspace/output/intermediate/event_log.jsonl" in names
        assert "support_bundle_manifest.json" in names
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
        embedded_manifest = json.loads(archive.read("support_bundle_manifest.json").decode("utf-8"))
    assert b"redaction-secret-value" not in combined
    assert b"super-secret-value" not in combined
    assert b"multiline-secret-value" not in combined
    assert BARE_TOKEN.encode("utf-8") not in combined
    assert b"<redacted>" in combined
    assert b"<redacted-token>" in combined
    assert str(tmp_path).encode("utf-8") not in combined
    assert "zip_filename" in embedded_manifest
    assert "zip_path" not in embedded_manifest


def test_validate_workbuddy_support_bundle_rejects_unredacted_multiline_secret(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "support.zip"
    manifest_path = tmp_path / "support.manifest.json"
    member = "workspace/input/multi.yaml"
    data = b"provider:\n  api_key: |\n    multiline-secret-value\n"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, data)
    manifest = {
        "schema_version": "briefloop.workbuddy_support_bundle.v1",
        "runtime_effect": "packaging_only_read_only",
        "share_workspace_zip_allowed": False,
        "zip_sha256": _sha256_file(zip_path),
        "included_files": [
            {
                "path": member,
                "source_path": "input/multi.yaml",
                "sha256": sha256(data).hexdigest(),
                "size": len(data),
                "redacted": False,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_workbuddy_support_bundle(zip_path=zip_path, manifest_path=manifest_path)

    assert any("possible unredacted secret" in error for error in errors)


def test_workbuddy_support_bundle_cli_json(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(
        [
            "workbuddy",
            "support-bundle",
            "--workspace",
            str(ws),
            "--output",
            str(tmp_path / "support"),
            "--json",
        ]
    )

    assert rc == 0
    raw = capsys.readouterr().out
    assert "redaction-secret-value" not in raw
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["runtime_effect"] == "packaging_only_read_only"
    assert payload["share_workspace_zip_allowed"] is False
    assert payload["redacted_files"] == ["input/multi.yaml", "input/source.md", "input/token.md"]
    assert Path(payload["zip_path"]).exists()
    assert Path(payload["manifest_path"]).exists()


def test_workbuddy_support_bundle_rejects_output_inside_workspace(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path)

    rc = main(
        [
            "workbuddy",
            "support-bundle",
            "--workspace",
            str(ws),
            "--output",
            str(ws / "output" / "support"),
            "--json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["share_workspace_zip_allowed"] is False
    assert "must not be inside the workspace" in payload["error"]
