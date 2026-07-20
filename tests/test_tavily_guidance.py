"""Tests for Tavily API key guidance across init, doctor, and run."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent_brief.cli.main import build_parser, main
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


def _with_task_objective_if_supported(args: list[str]) -> list[str]:
    parser = build_parser()
    subcommands = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None)
    )
    init_options = {
        option
        for action in subcommands["init"]._actions
        for option in action.option_strings
    }
    if "--task-objective" in init_options:
        # LEGACY-DELETE: retain only the strict SQLite initialization contract.
        return [
            *args,
            "--task-objective",
            "Track material manufacturing developments.",
        ]
    return args


def _snapshot_tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestSecretsImport:
    """Deterministic workspace .env import without secret disclosure."""

    @pytest.mark.parametrize("json_flag", [[], ["--json"]])
    def test_secrets_import_public_cli_is_retired(self, tmp_path, capsys, json_flag):
        """The retired public CLI rejects with a typed token and performs zero writes."""
        source = tmp_path / "private.env"
        workspace = tmp_path / "workspace"
        _write_workspace_marker(workspace)
        secret = "tvly-super-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")
        before = _snapshot_tree_bytes(workspace)

        exit_code = main([
            "secrets",
            "import",
            "--workspace",
            str(workspace),
            "--from",
            str(source),
            "--keys",
            "TAVILY_API_KEY",
            *json_flag,
        ])
        captured = capsys.readouterr()

        # LEGACY-DELETE: remove with the retired public `secrets import` command surface.
        assert exit_code == 1
        assert captured.out == "runtime_command_unsupported\n"
        assert captured.err == ""
        assert _snapshot_tree_bytes(workspace) == before
        assert not (workspace / ".env").exists()

    def test_secrets_import_writes_env_but_redacts_output(self, tmp_path):
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

        with pytest.raises(SecretImportError, match="unsupported secret key") as excinfo:
            import_workspace_secrets(
                workspace=workspace,
                source=source,
                keys=["PRIVATE_VENDOR_TOKEN"],
            )
        message = str(excinfo.value)

        assert "not-for-briefloop" not in message
        assert "tvly-" not in message
        assert not (workspace / ".env").exists()

    def test_secrets_import_rejects_nonexistent_workspace_without_creating_it(self, tmp_path):
        source = tmp_path / "private.env"
        workspace = tmp_path / "typo-workspace"
        secret = "tvly-super-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        with pytest.raises(SecretImportError, match="workspace not found") as excinfo:
            import_workspace_secrets(
                workspace=workspace,
                source=source,
                keys=["TAVILY_API_KEY"],
            )
        message = str(excinfo.value)

        assert secret not in message
        assert "tvly-" not in message
        assert not workspace.exists()

    def test_secrets_import_rejects_directory_without_workspace_marker(self, tmp_path):
        source = tmp_path / "private.env"
        workspace = tmp_path / "plain-dir"
        workspace.mkdir()
        secret = "tvly-super-secret-123"
        source.write_text(f"TAVILY_API_KEY={secret}\n", encoding="utf-8")

        with pytest.raises(SecretImportError, match="not a BriefLoop workspace") as excinfo:
            import_workspace_secrets(
                workspace=workspace,
                source=source,
                keys=["TAVILY_API_KEY"],
            )
        message = str(excinfo.value)

        assert secret not in message
        assert "tvly-" not in message
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
        assert main(_with_task_objective_if_supported([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
        ])) == 0
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
        assert main(_with_task_objective_if_supported([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
            "--search-backend", "tavily",
        ])) == 0
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
        assert main(_with_task_objective_if_supported([
            "init", str(ws),
            "--language", "zh-CN",
            "--company", "Test Company",
            "--industry", "manufacturing",
            "--title", "Weekly Brief",
            "--audience", "management",
            "--cadence", "weekly",
            "--source-profile", "research",
            "--web-search-mode", "disabled",
        ])) == 0
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
            task_objective="Prepare the weekly manufacturing brief.",
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
        profile = InitProfile(
            task_objective="Prepare the weekly manufacturing brief.",
            tavily_enabled=False,
            web_search_enabled=False,
            web_search_mode="disabled",
            search_backend="",
        )
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
    """Retired operator-runtime run surface rejects with a typed token."""

    @pytest.mark.parametrize("backend", ["tavily", "exa"])
    def test_run_operator_runtime_is_retired(self, tmp_path, monkeypatch, capsys, backend):
        """The operator runtime is rejected regardless of search backend or key state."""
        import yaml

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        if backend == "exa":
            # Preserve the original scenario: a key for the wrong backend is present.
            monkeypatch.setenv("TAVILY_API_KEY", "tvly-present-but-wrong-backend")

        ws = tmp_path / "ws"
        init_extra = ["--tavily"] if backend == "tavily" else []
        assert main(_with_task_objective_if_supported([
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
            *init_extra,
        ])) == 0

        if backend == "exa":
            sources_path = ws / "sources.yaml"
            sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
            sources["source_strategy"]["enabled_providers"] = ["manual", "web_search"]
            sources["web_search"] = {"enabled": True, "mode": "external_api", "backend": "exa"}
            sources_path.write_text(yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")

        before = _snapshot_tree_bytes(ws)
        capsys.readouterr()  # drain init output so only the run rejection is captured
        exit_code = main(["run", "--runtime", "operator", "--config", str(ws / "config.yaml")])
        captured = capsys.readouterr()

        # LEGACY-DELETE: remove with the retired operator runtime handoff surface.
        assert exit_code == 1
        assert captured.out == "[run] runtime_adapter_unsupported\n"
        assert captured.err == ""
        assert _snapshot_tree_bytes(ws) == before
