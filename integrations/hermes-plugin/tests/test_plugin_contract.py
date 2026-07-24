import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
CANONICAL_BRIEFLOOP_SKILL = REPO_ROOT / ".agents" / "skills" / "briefloop"
PLUGIN_BRIEFLOOP_SKILL = ROOT / "mabw" / "skills" / "briefloop"
sys.path.insert(0, str(ROOT))

from mabw import schemas, tools  # noqa: E402


def _relative_files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _as_posix_path(value: str) -> str:
    return value.replace("\\", "/")


def _normalize_stage_label(label: str) -> str:
    normalized = label.strip().removeprefix("→").strip()
    aliases = {
        "source discovery when configured": "source-discovery",
        "input governance when available": "input-governance",
    }
    return aliases.get(normalized, normalized)


def _extract_reference_sequence(text: str) -> list[str]:
    match = re.search(r"## Sequence\s+```text\n(?P<body>.*?)\n```", text, re.DOTALL)
    assert match, "missing Delegated Workflow sequence block"
    return [
        _normalize_stage_label(line)
        for line in match.group("body").splitlines()
        if line.strip()
    ]


def test_schemas_have_specific_descriptions():
    for schema in [
        schemas.MABW_CREATE_ONBOARDING,
        schemas.MABW_INIT_WORKSPACE,
        schemas.MABW_RUN_HANDOFF,
    ]:
        assert schema["name"].startswith("mabw_")
        assert "description" in schema
        assert len(schema["description"]) > 40
        assert "parameters" in schema


def test_create_onboarding_writes_json(tmp_path):
    result = json.loads(
        tools.create_onboarding(
            {
                "workspace": str(tmp_path / "workspace"),
                "profile": {
                    "company_or_org": "阿特斯",
                    "industry_or_theme": "光伏和储能",
                    "task_objective": "美国光储行业简报",
                    "language": "中文",
                    "web_search_mode": "runtime_websearch",
                },
            }
        )
    )

    assert result["ok"] is True
    onboarding_path = Path(result["onboarding_path"])
    assert onboarding_path.exists()
    data = json.loads(onboarding_path.read_text(encoding="utf-8"))
    assert data["company_or_org"] == "阿特斯"
    assert data["audience"] == "management team"


def test_create_onboarding_requires_core_fields(tmp_path):
    result = json.loads(
        tools.create_onboarding(
            {
                "workspace": str(tmp_path / "workspace"),
                "profile": {"company_or_org": "Only one field"},
            }
        )
    )
    assert result["ok"] is False
    assert "industry_or_theme" in result["missing"]
    assert "task_objective" in result["missing"]


class FakeCtx:
    def __init__(self):
        self.tools = []
        self.commands = []
        self.skills = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])

    def register_command(self, name, handler, **kwargs):
        self.commands.append(name)

    def register_skill(self, name, path):
        self.skills.append((name, str(path)))


def test_plugin_registers_tools_command_and_skill():
    import mabw

    ctx = FakeCtx()
    mabw.register(ctx)

    assert set(ctx.tools) == {
        "mabw_env_doctor",
        "mabw_create_onboarding",
        "mabw_init_workspace",
        "mabw_run_handoff",
    }
    assert "mabw" in ctx.commands
    assert {name for name, _ in ctx.skills} == {"mabw-workflow", "briefloop"}
    skill_paths = {name: _as_posix_path(path) for name, path in ctx.skills}
    assert skill_paths["mabw-workflow"].endswith("mabw/skills/mabw-workflow/SKILL.md")
    assert skill_paths["briefloop"].endswith("mabw/skills/briefloop/SKILL.md")


def test_plugin_briefloop_skill_matches_canonical_projection():
    assert (PLUGIN_BRIEFLOOP_SKILL / "SKILL.md").exists()
    assert (PLUGIN_BRIEFLOOP_SKILL / "CHANGELOG.md").exists()

    canonical_files = _relative_files(CANONICAL_BRIEFLOOP_SKILL)
    plugin_files = _relative_files(PLUGIN_BRIEFLOOP_SKILL)
    assert plugin_files == canonical_files

    for rel_path in canonical_files:
        canonical = (CANONICAL_BRIEFLOOP_SKILL / rel_path).read_bytes()
        projected = (PLUGIN_BRIEFLOOP_SKILL / rel_path).read_bytes()
        assert projected == canonical, (
            f"briefloop Hermes plugin projection differs: {rel_path}"
        )


def test_plugin_skill_uses_orchestrator_contract():
    skill = ROOT / "mabw" / "skills" / "mabw-workflow" / "SKILL.md"
    reference = (
        ROOT
        / "mabw"
        / "skills"
        / "mabw-workflow"
        / "references"
        / "delegated-workflow.md"
    )

    for path in (skill, reference):
        text = path.read_text(encoding="utf-8")
        assert "Orchestrator main agent" in text
        assert "configs/orchestrator_contract.yaml" in text
        assert "configs/stage_specs.yaml" in text
        assert "configs/artifact_contracts.yaml" in text
        assert "retry_stage" in text
        assert "request_human_review" in text
        assert "block_run" in text
    skill_text = skill.read_text(encoding="utf-8")
    assert "gates check/state check/stage-complete" in skill_text
    assert "finalize-complete" in skill_text
    assert "not a quality-gate executor" in skill_text
    assert "provenance build" in skill_text
    assert "not semantic proof" in skill_text
    assert "audience_profile_snapshot.md" in skill_text
    assert "not source evidence" in skill_text
    assert "orchestrator_control_switchboard.json" in skill_text
    assert "Selection is not execution" in skill_text


def test_plugin_reference_mentions_feedback_controls():
    reference = (
        ROOT
        / "mabw"
        / "skills"
        / "mabw-workflow"
        / "references"
        / "delegated-workflow.md"
    )
    artifact_contract = (
        ROOT
        / "mabw"
        / "skills"
        / "mabw-workflow"
        / "references"
        / "artifact-contract.md"
    )

    reference_text = reference.read_text(encoding="utf-8")
    artifact_text = artifact_contract.read_text(encoding="utf-8")
    assert "briefloop feedback ingest" in reference_text
    assert "feedback resolve" in reference_text
    assert "feedback show --json" in reference_text
    assert "do not execute repair" in reference_text
    assert "briefloop gates check" in reference_text
    assert "briefloop state check --workspace <workspace> --strict" in reference_text
    assert (
        "briefloop state stage-complete --workspace <workspace> --stage auditor"
        in reference_text
    )
    assert "briefloop state finalize-complete --workspace <workspace>" in reference_text
    assert "finalize` only renders reader-facing outputs" in reference_text
    assert "gates show --json" in reference_text
    assert "do not live-fetch" in reference_text
    assert "briefloop provenance build" in reference_text
    assert "provenance show --json" in reference_text
    assert "not semantic truth verification" in reference_text
    assert "audience_profile_snapshot.md" in reference_text
    assert "runtime context only" in reference_text
    assert "do not treat `audience_profile.md` as source evidence" in reference_text
    assert "orchestrator_control_switchboard.json" in reference_text
    assert "briefloop controls select" in reference_text
    assert "Selection is not execution" in reference_text
    assert "feedback_issues.json" in artifact_text
    assert "repair_plan.json" in artifact_text
    assert "delta_audit_report.json" in artifact_text
    assert "quality_gate_report.json" in artifact_text
    assert "provenance_graph.json" in artifact_text
    assert "audience_profile.md" in artifact_text
    assert "audience_profile_snapshot.md" in artifact_text
    assert "orchestrator_control_switchboard.json" in artifact_text
    assert "control_selections.json" in artifact_text
    assert "not workflow artifacts" in artifact_text


def _which_from(mapping):
    return lambda command: mapping.get(command)


def test_cli_resolver_has_one_frozen_order(tmp_path):
    repo_root = tmp_path / "repo"
    repo_briefloop = str(repo_root / ".venv" / "bin" / "briefloop")
    repo_compat = str(repo_root / ".venv" / "bin" / "multi-agent-brief")

    both = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from(
            {
                "briefloop": "/path/briefloop",
                "multi-agent-brief": "/path/multi-agent-brief",
                repo_briefloop: repo_briefloop,
            }
        ),
    )
    assert (both.command, both.source) == ("/path/briefloop", "path_briefloop")

    only_public = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from({"briefloop": "/path/briefloop"}),
    )
    assert (only_public.command, only_public.source) == (
        "/path/briefloop",
        "path_briefloop",
    )

    only_compat = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from({"multi-agent-brief": "/path/multi-agent-brief"}),
    )
    assert (only_compat.command, only_compat.source) == (
        "/path/multi-agent-brief",
        "path_multi_agent_brief",
    )

    repo_local = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from(
            {
                repo_briefloop: repo_briefloop,
                repo_compat: repo_compat,
            }
        ),
    )
    assert (repo_local.command, repo_local.source) == (
        repo_briefloop,
        "repo_local_briefloop",
    )

    repo_compat_only = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from({repo_compat: repo_compat}),
    )
    assert (repo_compat_only.command, repo_compat_only.source) == (
        repo_compat,
        "repo_local_multi_agent_brief",
    )

    unavailable = tools._resolve_cli(
        repo_root=repo_root,
        environ={},
        which=_which_from({}),
    )
    assert unavailable.command is None
    assert unavailable.reason_code == "briefloop_cli_unavailable"


def test_cli_resolver_preserves_explicit_override_precedence_and_failure():
    mapping = {
        "/preferred": "/validated/preferred",
        "/legacy": "/validated/legacy",
        "/older": "/validated/older",
        "briefloop": "/path/briefloop",
        "multi-agent-brief": "/path/multi-agent-brief",
    }
    preferred = tools._resolve_cli(
        repo_root=None,
        environ={
            "BRIEFLOOP_BIN": "/preferred",
            "MABW_BIN": "/legacy",
            "MULTI_AGENT_BRIEF_BIN": "/older",
        },
        which=_which_from(mapping),
    )
    assert preferred.command == "/preferred"
    assert preferred.override_name == "BRIEFLOOP_BIN"

    legacy = tools._resolve_cli(
        repo_root=None,
        environ={"MABW_BIN": "/legacy"},
        which=_which_from(mapping),
    )
    assert legacy.command == "/legacy"
    assert legacy.override_name == "MABW_BIN"

    older = tools._resolve_cli(
        repo_root=None,
        environ={"MULTI_AGENT_BRIEF_BIN": "/older"},
        which=_which_from(mapping),
    )
    assert older.command == "/older"
    assert older.override_name == "MULTI_AGENT_BRIEF_BIN"

    invalid = tools._resolve_cli(
        repo_root=None,
        environ={"BRIEFLOOP_BIN": "/missing"},
        which=_which_from(mapping),
    )
    assert invalid.command is None
    assert invalid.source == "explicit_override_invalid"
    assert invalid.reason_code == "briefloop_explicit_override_unavailable"


def test_cli_resolver_preserves_explicit_symlink_and_bare_command_identity(tmp_path):
    target = tmp_path / "briefloop-target"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    symlink = tmp_path / "briefloop-override"
    symlink.symlink_to(target)

    symlink_resolution = tools._resolve_cli(
        repo_root=None,
        environ={"BRIEFLOOP_BIN": str(symlink)},
        which=_which_from({str(symlink): str(symlink)}),
    )
    assert symlink_resolution.command == str(symlink)
    assert symlink_resolution.command != str(target)

    bare_resolution = tools._resolve_cli(
        repo_root=None,
        environ={"MABW_BIN": "briefloop-override"},
        which=_which_from({"briefloop-override": "/resolved/briefloop"}),
    )
    assert bare_resolution.command == "briefloop-override"


def test_cli_resolver_classifies_only_actual_path_separators(tmp_path):
    caller = tmp_path / "caller"
    bare_tokens = (
        "briefloop",
        ".briefloop",
        ".multi-agent-brief",
        "briefloop.override",
    )
    relative_paths = (
        "./briefloop",
        "../bin/briefloop",
        "bin/briefloop",
        ".\\briefloop",
        "..\\bin\\briefloop",
        "bin\\briefloop",
    )

    for index, override in enumerate((*bare_tokens, *relative_paths)):
        resolution = tools._resolve_cli(
            repo_root=None,
            environ={"BRIEFLOOP_BIN": override},
            which=_which_from({override: f"/validated/{index}"}),
            resolution_cwd=caller,
        )
        expected = override if override in bare_tokens else str(caller / Path(override))
        assert resolution.command == expected


def test_dotted_bare_override_reaches_handoff_unchanged(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    caller = tmp_path / "caller"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    configured_override = ".briefloop"
    original_resolve = tools._resolve_cli
    commands = []

    def resolve(*, repo_root, **_kwargs):
        return original_resolve(
            repo_root=repo_root,
            environ={"BRIEFLOOP_BIN": configured_override},
            which=_which_from({configured_override: "/validated/.briefloop"}),
            resolution_cwd=caller,
        )

    def fake_run(cmd, cwd=None, timeout=300):
        del timeout
        commands.append((cmd, cwd))
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "command": cmd}

    monkeypatch.setattr(tools, "_find_repo_root", lambda: repo_root)
    monkeypatch.setattr(tools, "_resolve_cli", resolve)
    monkeypatch.setattr(tools, "_run", fake_run)

    handoff = json.loads(tools.run_handoff({"workspace": str(workspace)}))

    assert handoff["command"][0] == configured_override
    assert [cmd[0] for cmd, _cwd in commands] == [configured_override]
    assert commands[-1][1] == str(repo_root)


def test_relative_explicit_override_is_frozen_before_consumer_cwd_changes(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    onboarding = workspace / "onboarding.json"
    onboarding.write_text("{}\n", encoding="utf-8")
    caller = tmp_path / "caller"
    override_path = caller / "bin" / "briefloop"
    override_path.parent.mkdir(parents=True)
    target = caller / "briefloop-target"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    override_path.symlink_to(target)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    configured_override = "./bin/briefloop"
    expected_command = str(override_path)
    original_resolve = tools._resolve_cli
    commands = []

    def resolve(*, repo_root, **_kwargs):
        return original_resolve(
            repo_root=repo_root,
            environ={"BRIEFLOOP_BIN": configured_override},
            which=_which_from({configured_override: expected_command}),
            resolution_cwd=caller,
        )

    def fake_run(cmd, cwd=None, timeout=300):
        del timeout
        commands.append((cmd, cwd))
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "command": cmd}

    monkeypatch.setattr(tools, "_find_repo_root", lambda: repo_root)
    monkeypatch.setattr(tools, "_find_workspace_dirs", lambda _root: [])
    monkeypatch.setattr(tools, "_resolve_cli", resolve)
    monkeypatch.setattr(tools, "_run", fake_run)

    initialized = json.loads(
        tools.init_workspace(
            {
                "workspace": str(workspace),
                "onboarding_path": str(onboarding),
            }
        )
    )
    doctor = json.loads(tools.env_doctor({}))
    handoff = json.loads(tools.run_handoff({"workspace": str(workspace)}))

    assert initialized["command"][0] == expected_command
    assert doctor["mabw_bin"] == expected_command
    assert handoff["command"][0] == expected_command
    assert [cmd[0] for cmd, _cwd in commands] == [expected_command] * 3
    assert commands[-1][1] == str(repo_root)


def test_cli_resolver_fails_closed_before_any_fallback_for_first_override():
    calls = []

    def which(command):
        calls.append(command)
        return None

    empty = tools._resolve_cli(
        repo_root=None,
        environ={"BRIEFLOOP_BIN": "", "MABW_BIN": "/legacy"},
        which=which,
    )
    assert empty.command is None
    assert empty.override_name == "BRIEFLOOP_BIN"
    assert empty.reason_code == "briefloop_explicit_override_unavailable"
    assert calls == []

    invalid = tools._resolve_cli(
        repo_root=None,
        environ={"BRIEFLOOP_BIN": "/missing"},
        which=which,
    )
    assert invalid.command is None
    assert invalid.reason_code == "briefloop_explicit_override_unavailable"
    assert calls == ["/missing"]


def test_cli_resolver_freezes_relative_which_result_before_cwd_changes(tmp_path):
    caller = tmp_path / "caller"
    expected = caller / "bin" / "briefloop"

    resolution = tools._resolve_cli(
        repo_root=None,
        environ={},
        which=_which_from({"briefloop": "bin/briefloop"}),
        resolution_cwd=caller,
    )

    assert resolution.command == str(expected.resolve())


def test_plugin_readme_marks_mabw_command_as_legacy_and_uses_public_cli():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "`briefloop init --from-onboarding`" in text
    assert "`briefloop run --workspace --runtime hermes`" in text
    assert "legacy `/mabw <workspace>`" in text


def test_plugin_delegated_workflow_matches_stage_specs():
    stage_specs = yaml.safe_load(
        (REPO_ROOT / "configs" / "stage_specs.yaml").read_text(encoding="utf-8")
    )
    expected = [stage["stage_id"] for stage in stage_specs["workflow"]["stages"]]
    reference = (
        ROOT
        / "mabw"
        / "skills"
        / "mabw-workflow"
        / "references"
        / "delegated-workflow.md"
    )

    assert (
        _extract_reference_sequence(reference.read_text(encoding="utf-8")) == expected
    )


def test_run_handoff_passes_detected_repo_workdir(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured = {}

    def fake_run(cmd, cwd=None, timeout=300):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "command": cmd}

    monkeypatch.setattr(tools, "_find_repo_root", lambda: repo_root)
    monkeypatch.setattr(
        tools,
        "_resolve_cli",
        lambda **_kwargs: tools._CliResolution(
            command="briefloop",
            source="path_briefloop",
            reason_code=None,
        ),
    )
    monkeypatch.setattr(tools, "_run", fake_run)

    result = json.loads(
        tools.run_handoff({"workspace": str(workspace), "runtime": "hermes"})
    )

    assert result["ok"] is True
    assert result["repo_root"] == str(repo_root)
    assert "audience_memory_files" in result
    assert result["audience_memory_files"]["audience_profile"] == str(
        workspace / "audience_profile.md"
    )
    assert result["audience_memory_files"]["audience_profile_snapshot"] == str(
        workspace / "output" / "intermediate" / "audience_profile_snapshot.md"
    )
    assert result["audience_memory_files_exist"] == {
        "audience_profile": False,
        "audience_profile_snapshot": False,
    }
    assert "control_switchboard_files" in result
    assert result["control_switchboard_files"][
        "orchestrator_control_switchboard"
    ] == str(
        workspace / "output" / "intermediate" / "orchestrator_control_switchboard.json"
    )
    assert result["control_switchboard_files"]["control_selections"] == str(
        workspace / "output" / "intermediate" / "control_selections.json"
    )
    assert result["control_switchboard_files_exist"] == {
        "orchestrator_control_switchboard": False,
        "control_selections": False,
    }
    assert "audience_profile_snapshot.md" in result["next"]
    assert "orchestrator_control_switchboard.json" in result["next"]
    assert "selection is not execution" in result["next"]
    assert captured["cwd"] == str(repo_root)
    assert captured["cmd"][0] == "briefloop"
    assert "--repo-workdir" in captured["cmd"]
    repo_arg = captured["cmd"].index("--repo-workdir") + 1
    assert captured["cmd"][repo_arg] == str(repo_root)


def test_doctor_and_handoff_consume_identical_resolution(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolution = tools._CliResolution(
        command="/path/multi-agent-brief",
        source="path_multi_agent_brief",
        reason_code=None,
    )
    resolutions = []
    commands = []

    def fake_resolve(**_kwargs):
        resolutions.append(resolution)
        return resolution

    def fake_run(cmd, cwd=None, timeout=300):
        del cwd, timeout
        commands.append(cmd)
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "briefloop 1.0\n" if cmd[1] == "version" else "",
            "stderr": "",
            "command": cmd,
        }

    monkeypatch.setattr(tools, "_find_repo_root", lambda: None)
    monkeypatch.setattr(tools, "_find_workspace_dirs", lambda _root: [])
    monkeypatch.setattr(tools, "_resolve_cli", fake_resolve)
    monkeypatch.setattr(tools, "_run", fake_run)

    doctor = json.loads(tools.env_doctor({}))
    handoff = json.loads(tools.run_handoff({"workspace": str(workspace)}))

    assert resolutions == [resolution, resolution]
    assert doctor["binary_resolution"] == handoff["binary_resolution"]
    assert commands[0][0] == commands[1][0] == "/path/multi-agent-brief"


def test_invalid_explicit_override_is_identical_and_never_falls_back(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    onboarding = workspace / "onboarding.json"
    onboarding.write_text("{}\n", encoding="utf-8")
    invalid = tools._CliResolution(
        command=None,
        source="explicit_override_invalid",
        reason_code="briefloop_explicit_override_unavailable",
        override_name="BRIEFLOOP_BIN",
    )
    calls = []

    monkeypatch.setattr(tools, "_find_repo_root", lambda: None)
    monkeypatch.setattr(tools, "_find_workspace_dirs", lambda _root: [])
    monkeypatch.setattr(tools, "_resolve_cli", lambda **_kwargs: invalid)
    monkeypatch.setattr(tools, "_run", lambda *_args, **_kwargs: calls.append(True))

    initialized = json.loads(
        tools.init_workspace(
            {
                "workspace": str(workspace),
                "onboarding_path": str(onboarding),
            }
        )
    )
    doctor = json.loads(tools.env_doctor({}))
    handoff = json.loads(tools.run_handoff({"workspace": str(workspace)}))

    assert calls == []
    assert initialized["reason_code"] == "briefloop_explicit_override_unavailable"
    assert initialized["command"] == []
    assert doctor["binary_resolution"] == handoff["binary_resolution"]
    assert doctor["next_action"] == "configure_cli_override"
    assert handoff["ok"] is False
    assert handoff["reason_code"] == "briefloop_explicit_override_unavailable"
    assert handoff["command"] == []


def test_init_doctor_and_handoff_probe_the_same_repo_local_environment(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    onboarding = workspace / "onboarding.json"
    onboarding.write_text("{}\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    command = str(repo_root / ".venv" / "bin" / "briefloop")
    probed_roots = []
    commands = []

    def fake_resolve(*, repo_root, **_kwargs):
        probed_roots.append(repo_root)
        return tools._CliResolution(
            command=command,
            source="repo_local_briefloop",
            reason_code=None,
        )

    def fake_run(cmd, cwd=None, timeout=300):
        del cwd, timeout
        commands.append(cmd)
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "command": cmd,
        }

    monkeypatch.setattr(tools, "_find_repo_root", lambda: repo_root)
    monkeypatch.setattr(tools, "_find_workspace_dirs", lambda _root: [])
    monkeypatch.setattr(tools, "_resolve_cli", fake_resolve)
    monkeypatch.setattr(tools, "_run", fake_run)

    doctor = json.loads(tools.env_doctor({}))
    initialized = json.loads(
        tools.init_workspace(
            {
                "workspace": str(workspace),
                "onboarding_path": str(onboarding),
            }
        )
    )
    handoff = json.loads(tools.run_handoff({"workspace": str(workspace)}))

    assert initialized["command"][0] == handoff["command"][0] == command
    assert doctor["mabw_bin"] == command
    assert [cmd[0] for cmd in commands] == [command, command, command]
    assert probed_roots == [repo_root, repo_root, repo_root]


def test_copied_plugin_resolver_matches_source_for_identity_and_fallback(
    monkeypatch, tmp_path
):
    copied_plugin = tmp_path / "copied-mabw"
    shutil.copytree(ROOT / "mabw", copied_plugin)
    module_name = "copied_mabw_tools"
    spec = importlib.util.spec_from_file_location(
        module_name, copied_plugin / "tools.py"
    )
    assert spec is not None and spec.loader is not None
    copied_tools = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, copied_tools)
    spec.loader.exec_module(copied_tools)

    cases = [
        (
            {"BRIEFLOOP_BIN": "briefloop-override"},
            {"briefloop-override": "/bin/briefloop"},
        ),
        ({"BRIEFLOOP_BIN": ".briefloop"}, {".briefloop": "/bin/.briefloop"}),
        (
            {"BRIEFLOOP_BIN": ".multi-agent-brief"},
            {".multi-agent-brief": "/bin/.multi-agent-brief"},
        ),
        (
            {"BRIEFLOOP_BIN": "./bin/briefloop"},
            {"./bin/briefloop": str(tmp_path / "bin" / "briefloop")},
        ),
        (
            {"BRIEFLOOP_BIN": "bin\\briefloop"},
            {"bin\\briefloop": str(tmp_path / Path("bin\\briefloop"))},
        ),
        ({}, {"briefloop": "bin/briefloop"}),
        ({"BRIEFLOOP_BIN": ""}, {}),
    ]
    for environ, mapping in cases:
        source = tools._resolve_cli(
            repo_root=None,
            environ=environ,
            which=_which_from(mapping),
            resolution_cwd=tmp_path,
        )
        copied = copied_tools._resolve_cli(
            repo_root=None,
            environ=environ,
            which=_which_from(mapping),
            resolution_cwd=tmp_path,
        )
        assert (
            source.command,
            source.source,
            source.reason_code,
            source.override_name,
        ) == (
            copied.command,
            copied.source,
            copied.reason_code,
            copied.override_name,
        )
