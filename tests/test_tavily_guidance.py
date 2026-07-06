"""Tests for Tavily API key guidance across init, doctor, and run."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent_brief.cli.main import main


def _write_workspace_marker(workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "config.yaml").write_text(
        "project:\n  name: Test Workspace\n",
        encoding="utf-8",
    )


class TestSecretsImport:
    """Deterministic workspace .env import without secret disclosure."""

    def test_secrets_import_writes_env_but_redacts_output(self, tmp_path, capsys):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        tavily_secret = "tvly-super-secret-123"
        exa_secret = "sk-exa-super-secret-456"
        source.write_text(
            f"TAVILY_API_KEY={tavily_secret}\n"
            f"EXA_API_KEY='{exa_secret}'\n",
            encoding="utf-8",
        )

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "TAVILY_API_KEY",
            "EXA_API_KEY",
        ])
        captured = capsys.readouterr()

        assert exit_code == 0
        combined_output = captured.out + captured.err
        assert "TAVILY_API_KEY=present sha256_prefix=" in captured.out
        assert "EXA_API_KEY=present sha256_prefix=" in captured.out
        assert tavily_secret not in combined_output
        assert exa_secret not in combined_output
        assert "tvly-" not in combined_output
        assert "sk-" not in combined_output

        env_text = (workspace / ".env").read_text(encoding="utf-8")
        assert f"TAVILY_API_KEY={tavily_secret}" in env_text
        assert f"EXA_API_KEY={exa_secret}" in env_text

    def test_secrets_import_json_output_is_redacted(self, tmp_path, capsys):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        secret = "tvly-json-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "TAVILY_API_KEY",
            "--json",
        ])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "TAVILY_API_KEY" in captured.out
        assert "present" in captured.out
        assert "sha256_prefix" in captured.out
        assert secret not in captured.out
        assert "tvly-" not in captured.out

    def test_secrets_import_rejects_unknown_key_without_leaking_values(self, tmp_path, capsys):
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        source.write_text(
            "TAVILY_API_KEY=tvly-super-secret-123\n"
            "PRIVATE_VENDOR_TOKEN=not-for-briefloop\n",
            encoding="utf-8",
        )

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "PRIVATE_VENDOR_TOKEN",
        ])
        captured = capsys.readouterr()

        assert exit_code == 1
        combined_output = captured.out + captured.err
        assert "unsupported secret key" in combined_output
        assert "not-for-briefloop" not in combined_output
        assert "tvly-" not in combined_output
        assert not (workspace / ".env").exists()

    def test_secrets_import_rejects_nonexistent_workspace_without_creating_it(self, tmp_path, capsys):
        source = tmp_path / "private.env"
        workspace = tmp_path / "typo-workspace"
        secret = "tvly-super-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "TAVILY_API_KEY",
        ])
        captured = capsys.readouterr()

        assert exit_code == 1
        combined_output = captured.out + captured.err
        assert "workspace not found" in combined_output
        assert secret not in combined_output
        assert "tvly-" not in combined_output
        assert not workspace.exists()

    def test_secrets_import_rejects_directory_without_workspace_marker(self, tmp_path, capsys):
        source = tmp_path / "private.env"
        workspace = tmp_path / "plain-dir"
        workspace.mkdir()
        secret = "tvly-super-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "TAVILY_API_KEY",
        ])
        captured = capsys.readouterr()

        assert exit_code == 1
        combined_output = captured.out + captured.err
        assert "not a BriefLoop workspace" in combined_output
        assert secret not in combined_output
        assert "tvly-" not in combined_output
        assert not (workspace / ".env").exists()

    def test_writer_surfaces_do_not_instruct_copying_api_key_values(self):
        surfaces = [
            Path(".claude/commands/mabw.md"),
            Path(".claude/commands/briefloop.md"),
            Path(".claude/commands/init-brief.md"),
            Path(".agents/skills/briefloop/SKILL.md"),
            Path(".agents/skills/source-provider/SKILL.md"),
            Path("src/multi_agent_brief/runtime_assets.py"),
            Path("src/multi_agent_brief/hermes/adapter.py"),
        ]
        forbidden = [
            "cat ~/.env",
            "cat $HOME/.env",
            "cat .env",
            "Write(.env)",
            "read and copy API key",
            "paste API key value",
        ]
        for path in surfaces:
            text = path.read_text(encoding="utf-8")
            for phrase in forbidden:
                assert phrase not in text, f"{path} suggests unsafe secret handling: {phrase}"


class TestInitTavilyGuidance:
    """Init wizard Tavily opt-in and setup guidance."""

    def test_init_defaults_to_configure_later_without_key(self, tmp_path, monkeypatch):
        """Init without flags recommends web search without requiring an API key."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        # Use CLI args to skip interactive prompts (no --tavily flag)
        assert main([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
        ]) == 0
        import yaml
        config = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
        web_search = config["web_search"]
        assert web_search["enabled"] is True
        assert web_search["mode"] == "configure_later"
        assert "backend" not in web_search
        assert "api_key_env" not in web_search

    def test_init_explicit_tavily_generates_external_api_config(self, tmp_path, monkeypatch):
        """Explicit Tavily selection should generate external API web_search."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        assert main([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
            "--search-backend", "tavily",
        ]) == 0
        import yaml
        config = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
        web_search = config["web_search"]
        assert web_search["enabled"] is True
        assert web_search["mode"] == "external_api"
        assert web_search["backend"] == "tavily"
        assert web_search["api_key_env"] == "TAVILY_API_KEY"

    def test_init_can_explicitly_disable_web_search(self, tmp_path, monkeypatch):
        """Explicit disabled mode must override the recommended search setup."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        assert main([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
            "--web-search-mode", "disabled",
        ]) == 0
        import yaml
        config = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
        web_search = config["web_search"]
        assert web_search["enabled"] is False
        assert web_search["mode"] == "disabled"

    def test_init_tavily_creates_env_example(self, tmp_path, monkeypatch):
        """Init with Tavily enabled should create .env.example."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        profile = InitProfile(
            interface_language="en-US",
            industry="manufacturing",
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
        from multi_agent_brief.cli.init_commands import print_tavily_guidance

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        print_tavily_guidance()
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
        # llm_decide profile doesn't use enabled_providers; web_search config is the contract

    def test_init_no_tavily_no_env_example(self, tmp_path, monkeypatch):
        """Init always creates .env.example listing all 5 backends."""
        from multi_agent_brief.cli.init_wizard import InitProfile, create_workspace

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        ws = tmp_path / "ws"
        profile = InitProfile(tavily_enabled=False, web_search_enabled=False, web_search_mode="disabled", search_backend="")
        create_workspace(ws, profile)
        # .env.example is now always generated to guide users
        assert (ws / ".env.example").exists()
        content = (ws / ".env.example").read_text(encoding="utf-8")
        assert "TAVILY_API_KEY=" in content
        assert "EXA_API_KEY=" in content
        assert "BRAVE_SEARCH_API_KEY=" in content
        assert "Copy this file to .env" in content

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
            "web_search:\n  enabled: true\n  mode: external_api\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        tavily_results = [r for r in results if "tavily" in r.message.lower()]
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
            "web_search:\n  enabled: true\n  mode: external_api\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        error_msgs = [r.message for r in results if r.status == "ERROR"]
        assert any("TAVILY_API_KEY" in m and "missing" in m.lower() for m in error_msgs)
        assert any(".env.example" in m for m in error_msgs)
        assert any("--web-search-mode disabled" in m for m in error_msgs)
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
            "web_search:\n  enabled: true\n  mode: external_api\n  backend: tavily\n  api_key_env: TAVILY_API_KEY\n",
            encoding="utf-8",
        )

        results = run_doctor(config_path=config_path)
        report = format_doctor_report(results)
        assert "super-secret" not in report
        assert "tvly-" not in report

    def test_doctor_reads_workspace_env_without_printing_value(self, tmp_path, monkeypatch):
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
            r.status == "OK" and "TAVILY_API_KEY" in r.message and "detected" in r.message.lower()
            for r in results
        )
        assert "workspace-secret" not in report
        assert "tvly-" not in report


class TestRunTavilyGuidance:
    """Run command redirects users to subagent workflow."""

    def test_run_surfaces_missing_key_error(self, tmp_path, monkeypatch, capsys):
        """run command must redirect to subagent workflow, not check keys."""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        ws = tmp_path / "ws"
        assert main([
            "init",
            str(ws),
            "--language",
            "zh-CN",
            "--company",
            "Test Company",
            "--industry",
            "manufacturing",
            "--title",
            "Weekly Brief",
            "--audience",
            "management",
            "--cadence",
            "weekly",
            "--source-profile",
            "research",
            "--tavily",
        ]) == 0

        exit_code = main(["run", "--config", str(ws / "config.yaml"), "--skip-doctor"])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Runtime:" in captured.out
        assert "delegate_task" in captured.out or "hermes" in captured.out.lower()

    def test_run_surfaces_exa_missing_key_error(self, tmp_path, monkeypatch, capsys):
        """run command creates a handoff regardless of search backend."""
        import yaml

        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-present-but-wrong-backend")

        ws = tmp_path / "ws"
        assert main([
            "init",
            str(ws),
            "--language",
            "en-US",
            "--company",
            "Test Company",
            "--industry",
            "manufacturing",
            "--title",
            "Weekly Brief",
            "--audience",
            "management",
            "--cadence",
            "weekly",
            "--source-profile",
            "research",
        ]) == 0

        sources_path = ws / "sources.yaml"
        sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        sources["source_strategy"]["enabled_providers"] = ["manual", "web_search"]
        sources["web_search"] = {"enabled": True, "mode": "external_api", "backend": "exa"}
        sources_path.write_text(yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")

        exit_code = main(["run", "--config", str(ws / "config.yaml"), "--skip-doctor"])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Runtime:" in captured.out
        assert "delegate_task" in captured.out or "hermes" in captured.out.lower()
