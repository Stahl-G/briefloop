"""Tests for Tavily API key guidance across init, doctor, and run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from multi_agent_brief.cli.secrets_commands import (
    SecretImportError,
    import_workspace_secrets,
)


def _write_workspace_marker(workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n  name: Test Workspace\n",
        encoding="utf-8",
    )


class TestSecretsImport:
    """Deterministic workspace .env import without secret disclosure."""

    def test_secrets_import_writes_env_but_redacts_output(self, tmp_path):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        tavily_secret = "tvly-super-secret-123"
        exa_secret = "sk-exa-super-secret-456"
        source.write_text(
            f"TAVILY_API_KEY={tavily_secret}\nEXA_API_KEY='{exa_secret}'\n",
            encoding="utf-8",
        )

        result = import_workspace_secrets(
            workspace=workspace,
            source=source,
            keys=["TAVILY_API_KEY", "EXA_API_KEY"],
        )
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

        statuses = {item["key"]: item for item in result["keys"]}
        assert statuses["TAVILY_API_KEY"]["status"] == "present"
        assert statuses["TAVILY_API_KEY"]["sha256_prefix"]
        assert statuses["EXA_API_KEY"]["status"] == "present"
        assert statuses["EXA_API_KEY"]["sha256_prefix"]
        assert tavily_secret not in rendered
        assert exa_secret not in rendered
        assert "tvly-" not in rendered
        assert "sk-" not in rendered

        env_text = (workspace / ".env").read_text(encoding="utf-8")
        assert f"TAVILY_API_KEY={tavily_secret}" in env_text
        assert f"EXA_API_KEY={exa_secret}" in env_text

    def test_secrets_import_json_output_is_redacted(self, tmp_path):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        secret = "tvly-json-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        result = import_workspace_secrets(
            workspace=workspace,
            source=source,
            keys=["TAVILY_API_KEY"],
        )
        rendered = json.dumps(
            {
                "ok": True,
                "workspace_env": result["workspace_env"],
                "keys": result["keys"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        assert "TAVILY_API_KEY" in rendered
        assert "present" in rendered
        assert "sha256_prefix" in rendered
        assert secret not in rendered
        assert "tvly-" not in rendered

    def test_secrets_import_rejects_unknown_key_without_leaking_values(self, tmp_path):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        source.write_text(
            "TAVILY_API_KEY=tvly-super-secret-123\n"
            "PRIVATE_VENDOR_TOKEN=not-for-briefloop\n",
            encoding="utf-8",
        )

        with pytest.raises(
            SecretImportError, match="unsupported secret key"
        ) as excinfo:
            import_workspace_secrets(
                workspace=workspace,
                source=source,
                keys=["PRIVATE_VENDOR_TOKEN"],
            )
        message = str(excinfo.value)

        assert "not-for-briefloop" not in message
        assert "tvly-" not in message
        assert not (workspace / ".env").exists()


class TestInitTavilyGuidance:
    """Init wizard Tavily opt-in and setup guidance."""

    def test_no_generated_config_contains_api_key(self, tmp_path, monkeypatch):
        """No generated config file should contain actual API key values."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-12345")
        ws = tmp_path / "ws"
        profile = InitProfile(
            task_objective="Prepare the weekly manufacturing brief.",
            tavily_enabled=True,
        )
        create_workspace(ws, profile)

        for f in ws.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8")
                assert "super-secret" not in content, f"API key leaked in {f}"
                assert "tvly-super-secret" not in content, f"API key leaked in {f}"


class TestDoctorTavilyGuidance:
    """Doctor Tavily API key checks with actionable instructions."""

    def test_doctor_never_prints_key_value(self, tmp_path, monkeypatch):
        """Doctor must never print the actual API key value."""
        from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-999")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  mode: external_api\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        report = format_doctor_report(results)
        assert "super-secret" not in report
        assert "tvly-" not in report

    def test_doctor_reads_workspace_env_without_printing_value(
        self, tmp_path, monkeypatch
    ):
        """Doctor should treat workspace .env as a safe fallback for known keys."""
        from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / ".env").write_text(
            "TAVILY_API_KEY=tvly-workspace-secret-123\n"
            "UNRELATED_PRIVATE_KEY=should-not-be-read\n",
            encoding="utf-8",
        )
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n    - web_search\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  mode: external_api\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        report = format_doctor_report(results)

        assert any(
            r.status == "OK"
            and "TAVILY_API_KEY" in r.message
            and "detected" in r.message.lower()
            for r in results
        )
        assert "workspace-secret" not in report
        assert "tvly-" not in report
