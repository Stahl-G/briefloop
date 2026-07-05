"""Tests for doctor: config health checks and available-but-unconfigured hints."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from multi_agent_brief.sources import doctor
from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal workspace with config.yaml and sources.yaml."""
    config = tmp_path / "config.yaml"
    config.write_text("project:\n  name: Test\n", encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "source_strategy:\n"
        "  profile: research\n"
        "  enabled_providers:\n"
        "    - manual\n"
        "manual:\n"
        "  enabled: true\n"
        "  sources:\n"
        "    - name: Test\n"
        "      path: input/\n",
        encoding="utf-8",
    )
    return tmp_path


class TestDoctorAvailableButUnconfigured:
    """Doctor should list providers that exist but are not enabled."""

    def test_shows_unconfigured_providers(self, workspace, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("NEWSAPI_API_KEY", raising=False)

        results = run_doctor(config_path=workspace / "config.yaml")
        messages = [r.message for r in results]

        assert any("Available but not enabled" in m for m in messages)
        assert any("web_search" in m and "hint" not in m.lower() for m in messages)
        assert any("filing_resolver" in m for m in messages)
        assert any("mineru" in m for m in messages)

    def test_does_not_list_enabled_providers(self, workspace, monkeypatch):
        sources = workspace / "sources.yaml"
        sources.write_text(
            "source_strategy:\n"
            "  profile: research\n"
            "  enabled_providers:\n"
            "    - manual\n"
            "    - web_search\n"
            "manual:\n"
            "  enabled: true\n"
            "  sources:\n"
            "    - name: Test\n"
            "      path: input/\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: external_api\n"
            "  backend: tavily\n"
            "  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")

        results = run_doctor(config_path=workspace / "config.yaml")
        messages = [r.message for r in results]

        unconfigured_section = False
        for m in messages:
            if "Available but not enabled" in m:
                unconfigured_section = True
                continue
            if unconfigured_section and m.strip().startswith("[OK]") and "web_search" in m:
                pytest.fail(f"web_search should not appear in unconfigured list: {m}")

    def test_format_report_includes_available_section(self, workspace, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        results = run_doctor(config_path=workspace / "config.yaml")
        report = format_doctor_report(results)

        assert "Available but not enabled" in report
        assert "web_search" in report

    def test_runtime_tool_web_search_is_ok_without_api_key(self, workspace, monkeypatch):
        (workspace / "input").mkdir()
        sources = workspace / "sources.yaml"
        sources.write_text(
            "source_strategy:\n"
            "  profile: research\n"
            "  enabled_providers:\n"
            "    - manual\n"
            "    - web_search\n"
            "manual:\n"
            "  enabled: true\n"
            "  sources:\n"
            "    - name: Test\n"
            "      path: input/\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: runtime_tool\n"
            "  required_capability: web_search\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)

        results = run_doctor(config_path=workspace / "config.yaml")
        messages = [r.message for r in results]

        assert any("built-in search" in m and "no API key is required" in m for m in messages)

    def test_runtime_tool_web_search_rejects_backend(self, workspace, monkeypatch):
        sources = workspace / "sources.yaml"
        sources.write_text(
            "source_strategy:\n"
            "  profile: research\n"
            "  enabled_providers:\n"
            "    - web_search\n"
            "web_search:\n"
            "  enabled: true\n"
            "  mode: runtime_tool\n"
            "  backend: tavily\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        results = run_doctor(config_path=workspace / "config.yaml")
        messages = [r.message for r in results]

        assert any(r.status == "ERROR" and "runtime_tool must not configure backend" in r.message for r in results)
        assert not any("no backend configured" in m.lower() for m in messages)

    def test_output_writability_check_cleans_probe_file(self, workspace):
        output = workspace / "output"
        output.mkdir()

        results = run_doctor(config_path=workspace / "config.yaml")

        assert any(r.status == "OK" and r.message == "Output directory writable" for r in results)
        assert list(output.glob(".write_test*")) == []

    def test_missing_output_directory_checks_parent_can_create_it(self, workspace):
        output = workspace / "output"
        assert not output.exists()

        results = run_doctor(config_path=workspace / "config.yaml")

        assert any(
            r.status == "OK" and r.message == "Output directory can be created and written"
            for r in results
        )
        assert not output.exists()

    def test_missing_output_directory_create_failure_is_error(self, workspace, monkeypatch):
        output = workspace / "output"
        assert not output.exists()

        original_mkdir = Path.mkdir

        def fail_output_mkdir(self: Path, *args, **kwargs):
            if self == output:
                raise PermissionError("parent not writable")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_output_mkdir)

        results = run_doctor(config_path=workspace / "config.yaml")

        assert any(
            r.status == "ERROR"
            and "Output directory cannot be created" in r.message
            and "parent not writable" in r.message
            for r in results
        )

    def test_output_writability_cleanup_failure_is_warning_not_not_writable(
        self,
        workspace,
        monkeypatch,
    ):
        output = workspace / "output"
        output.mkdir()

        def fail_cleanup(path: Path) -> str:
            return "windows_sandbox_recycle_bin_unavailable"

        monkeypatch.setattr(doctor, "_try_remove_write_test", fail_cleanup)

        results = run_doctor(config_path=workspace / "config.yaml")

        assert not any(r.status == "ERROR" and "Output directory not writable" in r.message for r in results)
        assert any(
            r.status == "WARN"
            and "Output directory writable, but write-test cleanup failed" in r.message
            and "windows_sandbox_recycle_bin_unavailable" in r.message
            for r in results
        )


class TestDoctorRootEnvExample:
    """Root .env.example should list all 7 API keys."""

    def test_root_env_example_has_all_keys(self):
        env_example = Path(__file__).parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        required_keys = [
            "TAVILY_API_KEY",
            "EXA_API_KEY",
            "BRAVE_SEARCH_API_KEY",
            "FIRECRAWL_API_KEY",
            "SERPER_API_KEY",
            "NEWSAPI_API_KEY",
            "MINERU_API_TOKEN",
        ]
        for key in required_keys:
            assert f"{key}=" in content, f"Missing {key} in .env.example"

    def test_root_env_example_no_real_keys(self):
        env_example = Path(__file__).parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        for line in content.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                assert value.strip() == "", f"Key {key.strip()} has a value in .env.example"
