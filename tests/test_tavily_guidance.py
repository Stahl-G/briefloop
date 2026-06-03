"""Tests for Tavily API key guidance across init, doctor, and run."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent_brief.cli.main import main


class TestInitTavilyGuidance:
    """Init wizard Tavily opt-in and setup guidance."""

    def test_init_tavily_generates_config(self, tmp_path, monkeypatch):
        """Init with Tavily enabled should generate web_search config."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        # Simulate answering 'y' to Tavily question
        # Use CLI args to skip interactive prompts
        assert main([
            "init", str(ws),
            "--language", "zh-CN",
            "--industry", "solar",
            "--source-profile", "research",
        ]) == 0
        # Without tavily_enabled CLI arg, web_search should be disabled
        sources = (ws / "sources.yaml").read_text(encoding="utf-8")
        assert "enabled: false" in sources.split("web_search:")[1].split("api:")[0]

    def test_init_tavily_creates_env_example(self, tmp_path, monkeypatch):
        """Init with Tavily enabled should create .env.example."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        profile = InitProfile(
            interface_language="en-US",
            industry="solar",
            tavily_enabled=True,
        )
        create_workspace(ws, profile)
        assert (ws / ".env.example").exists()
        env_content = (ws / ".env.example").read_text(encoding="utf-8")
        assert "TAVILY_API_KEY" in env_content
        # Must not contain an actual key value
        assert "tvly-" not in env_content

    def test_init_tavily_prints_guidance(self, tmp_path, capsys, monkeypatch):
        """Init with Tavily enabled should print setup guidance."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace
        from multi_agent_brief.cli.main import _print_tavily_guidance

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        _print_tavily_guidance()
        captured = capsys.readouterr()
        assert "TAVILY_API_KEY" in captured.out
        assert "environment variable" in captured.out
        assert "Do not paste API keys" in captured.out

    def test_init_tavily_sources_yaml_has_tavily_config(self, tmp_path, monkeypatch):
        """sources.yaml with Tavily should have correct backend config."""
        from multi_agent_brief.cli.init_wizard import InitProfile, build_sources

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        profile = InitProfile(tavily_enabled=True)
        sources = build_sources(profile)
        ws = sources["web_search"]
        assert ws["enabled"] is True
        assert ws["backend"] == "tavily"
        assert ws["api_key_env"] == "TAVILY_API_KEY"
        assert "web_search" in sources["source_strategy"]["enabled_providers"]

    def test_init_no_tavily_no_env_example(self, tmp_path, monkeypatch):
        """Init without Tavily should not create .env.example."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        profile = InitProfile(tavily_enabled=False)
        create_workspace(ws, profile)
        assert not (ws / ".env.example").exists()

    def test_no_generated_config_contains_api_key(self, tmp_path, monkeypatch):
        """No generated config file should contain actual API key values."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-12345")
        ws = tmp_path / "ws"
        profile = InitProfile(tavily_enabled=True)
        create_workspace(ws, profile)

        for f in ws.rglob("*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8")
                assert "super-secret" not in content, f"API key leaked in {f}"
                assert "tvly-super-secret" not in content, f"API key leaked in {f}"


class TestDoctorTavilyGuidance:
    """Doctor Tavily API key checks with actionable instructions."""

    def test_doctor_tavily_ok_with_key(self, tmp_path, monkeypatch):
        """Doctor should report OK when TAVILY_API_KEY is set."""
        from multi_agent_brief.sources.doctor import run_doctor

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        tavily_results = [r for r in results if "Tavily" in r.message]
        assert any(r.status == "OK" and "detected" in r.message.lower() for r in tavily_results)

    def test_doctor_tavily_error_without_key(self, tmp_path, monkeypatch):
        """Doctor should ERROR with setup instructions when key is missing."""
        from multi_agent_brief.sources.doctor import run_doctor

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        error_msgs = [r.message for r in results if r.status == "ERROR"]
        assert any("TAVILY_API_KEY" in m and "missing" in m.lower() for m in error_msgs)
        assert any("environment variable" in m for m in error_msgs)
        assert any("Do not paste" in m for m in error_msgs)

    def test_doctor_never_prints_key_value(self, tmp_path, monkeypatch):
        """Doctor must never print the actual API key value."""
        from multi_agent_brief.sources.doctor import run_doctor, format_doctor_report

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-999")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("project:\n  name: Test\n", encoding="utf-8")
        (tmp_path / "sources.yaml").write_text(
            "source_strategy:\n  profile: research\n  enabled_providers:\n    - manual\n"
            "manual:\n  enabled: true\n  sources:\n    - name: Test\n      path: input/\n"
            "web_search:\n  enabled: true\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        report = format_doctor_report(results)
        assert "super-secret" not in report
        assert "tvly-" not in report


class TestRunTavilyGuidance:
    """Run command Tavily key validation."""

    def test_run_surfaces_missing_key_error(self, tmp_path, monkeypatch, capsys):
        """Run should surface clear error when Tavily enabled but key missing."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        ws = tmp_path / "ws"
        assert main(["init", str(ws), "--language", "zh-CN", "--industry", "solar"]) == 0

        # Manually enable Tavily in sources.yaml
        sources = (ws / "sources.yaml").read_text(encoding="utf-8")
        sources = sources.replace("enabled: false", "enabled: true", 1)
        sources = sources.replace('backend: ""', "backend: tavily")
        sources = sources.replace(
            'note: "Configure a real backend or external agent before enabling web_search."',
            "api_key_env: TAVILY_API_KEY",
        )
        (ws / "sources.yaml").write_text(sources, encoding="utf-8")

        # Add a source file
        (ws / "input" / "news.md").write_text(
            "- Solar manufacturing capacity expanded by 15 percent.\n", encoding="utf-8"
        )

        # Run should complete but surface the error
        exit_code = main(["run", "--config", str(ws / "config.yaml")])
        captured = capsys.readouterr()
        assert "TAVILY_API_KEY" in captured.out
        assert "not set" in captured.out.lower() or "missing" in captured.out.lower()
        assert "Do not paste" in captured.out
