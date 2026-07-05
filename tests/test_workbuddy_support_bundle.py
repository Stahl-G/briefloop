"""Tests for secret-safe WorkBuddy support bundles."""

from __future__ import annotations

import json
from hashlib import sha256
import zipfile
from pathlib import Path

import multi_agent_brief.cli.workbuddy_commands as workbuddy_commands
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
    (ws / "input" / f"{BARE_TOKEN}.md").write_text("secret-like filename\n", encoding="utf-8")
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
    excluded_reasons = {item["reason"] for item in result.excluded_files}
    assert all(item["path"] == "<redacted>" for item in result.excluded_files)
    assert "secret_env_file" in excluded_reasons
    assert "non_utf8_text_file" in excluded_reasons
    assert "forbidden_private_or_generated_path" in excluded_reasons
    assert "secret_like_path" in excluded_reasons

    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
        assert "workspace/.env" not in names
        assert "workspace/private_planning/notes.md" not in names
        assert "workspace/input/bad.md" not in names
        assert not any(BARE_TOKEN in name for name in names)
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
    assert BARE_TOKEN.encode("utf-8") not in result.zip_path.read_bytes()
    assert BARE_TOKEN not in result.manifest_path.read_text(encoding="utf-8")
    assert b"<redacted>" in combined
    assert b"<redacted-token>" in combined
    assert str(tmp_path).encode("utf-8") not in combined
    assert "zip_filename" in embedded_manifest
    assert "zip_path" not in embedded_manifest
    assert all(item["path"] == "<redacted>" for item in embedded_manifest["excluded_files"])


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


def test_validate_workbuddy_support_bundle_rejects_unredacted_excluded_paths(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "support.zip"
    manifest_path = tmp_path / "support.manifest.json"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("support_bundle_manifest.json", b"{}\n")
    manifest = {
        "schema_version": "briefloop.workbuddy_support_bundle.v1",
        "runtime_effect": "packaging_only_read_only",
        "share_workspace_zip_allowed": False,
        "zip_sha256": _sha256_file(zip_path),
        "included_files": [
            {
                "path": "support_bundle_manifest.json",
                "source_path": "support_bundle_manifest.json",
                "sha256": sha256(b"{}\n").hexdigest(),
                "size": 3,
                "redacted": False,
            }
        ],
        "excluded_files": [
            {"path": "private_planning/Acme-MA-targets.md", "reason": "forbidden_private_or_generated_path"}
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validate_workbuddy_support_bundle(zip_path=zip_path, manifest_path=manifest_path)

    assert "manifest excluded_files paths must be redacted" in errors


def test_workbuddy_support_bundle_cli_removes_rejected_bundle(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    ws = _workspace(tmp_path)
    output = tmp_path / "support"

    def reject_bundle(*, zip_path: str | Path, manifest_path: str | Path) -> list[str]:
        assert Path(zip_path).exists()
        assert Path(manifest_path).exists()
        return ["forced validation failure"]

    monkeypatch.setattr(workbuddy_commands, "validate_workbuddy_support_bundle", reject_bundle)

    rc = main(
        [
            "workbuddy",
            "support-bundle",
            "--workspace",
            str(ws),
            "--output",
            str(output),
            "--json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "forced validation failure" in payload["error"]
    assert list(output.glob("*.zip")) == []
    assert list(output.glob("*.manifest.json")) == []


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
