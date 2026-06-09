import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from mabw import schemas, tools  # noqa: E402


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
    result = json.loads(tools.create_onboarding({
        "workspace": str(tmp_path / "workspace"),
        "profile": {
            "company_or_org": "阿特斯",
            "industry_or_theme": "光伏和储能",
            "task_objective": "美国光储行业简报",
            "language": "中文",
            "web_search_mode": "runtime_websearch",
        },
    }))

    assert result["ok"] is True
    onboarding_path = Path(result["onboarding_path"])
    assert onboarding_path.exists()
    data = json.loads(onboarding_path.read_text(encoding="utf-8"))
    assert data["company_or_org"] == "阿特斯"
    assert data["audience"] == "management team"


def test_create_onboarding_requires_core_fields(tmp_path):
    result = json.loads(tools.create_onboarding({
        "workspace": str(tmp_path / "workspace"),
        "profile": {"company_or_org": "Only one field"},
    }))
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
    assert ctx.skills and ctx.skills[0][0] == "mabw-workflow"


def test_plugin_skill_uses_orchestrator_contract():
    skill = ROOT / "mabw" / "skills" / "mabw-workflow" / "SKILL.md"
    reference = ROOT / "mabw" / "skills" / "mabw-workflow" / "references" / "delegated-workflow.md"

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
    assert "gates check/state check/state decide" in skill_text
    assert "not a quality-gate executor" in skill_text
    assert "provenance build" in skill_text
    assert "not semantic proof" in skill_text
    assert "audience_profile_snapshot.md" in skill_text
    assert "not source evidence" in skill_text
    assert "orchestrator_control_switchboard.json" in skill_text
    assert "Selection is not execution" in skill_text


def test_plugin_reference_mentions_feedback_controls():
    reference = ROOT / "mabw" / "skills" / "mabw-workflow" / "references" / "delegated-workflow.md"
    artifact_contract = ROOT / "mabw" / "skills" / "mabw-workflow" / "references" / "artifact-contract.md"

    reference_text = reference.read_text(encoding="utf-8")
    artifact_text = artifact_contract.read_text(encoding="utf-8")
    assert "multi-agent-brief feedback ingest" in reference_text
    assert "feedback resolve" in reference_text
    assert "feedback show --json" in reference_text
    assert "do not execute repair" in reference_text
    assert "multi-agent-brief gates check" in reference_text
    assert "multi-agent-brief state check --workspace <workspace> --strict" in reference_text
    assert "multi-agent-brief state decide --workspace <workspace> --stage auditor --decision continue" in reference_text
    assert "finalize` only renders reader-facing outputs" in reference_text
    assert "gates show --json" in reference_text
    assert "do not live-fetch" in reference_text
    assert "multi-agent-brief provenance build" in reference_text
    assert "provenance show --json" in reference_text
    assert "not semantic truth verification" in reference_text
    assert "audience_profile_snapshot.md" in reference_text
    assert "runtime context only" in reference_text
    assert "do not treat `audience_profile.md` as source evidence" in reference_text
    assert "orchestrator_control_switchboard.json" in reference_text
    assert "multi-agent-brief controls select" in reference_text
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


def test_plugin_delegated_workflow_matches_stage_specs():
    stage_specs = yaml.safe_load((REPO_ROOT / "configs" / "stage_specs.yaml").read_text(encoding="utf-8"))
    expected = [stage["stage_id"] for stage in stage_specs["workflow"]["stages"]]
    reference = ROOT / "mabw" / "skills" / "mabw-workflow" / "references" / "delegated-workflow.md"

    assert _extract_reference_sequence(reference.read_text(encoding="utf-8")) == expected


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
    monkeypatch.setattr(tools, "_mabw_bin", lambda: "multi-agent-brief")
    monkeypatch.setattr(tools, "_run", fake_run)

    result = json.loads(tools.run_handoff({"workspace": str(workspace), "runtime": "hermes"}))

    assert result["ok"] is True
    assert result["repo_root"] == str(repo_root)
    assert "audience_memory_files" in result
    assert result["audience_memory_files"]["audience_profile"] == str(workspace / "audience_profile.md")
    assert result["audience_memory_files"]["audience_profile_snapshot"] == str(
        workspace / "output" / "intermediate" / "audience_profile_snapshot.md"
    )
    assert result["audience_memory_files_exist"] == {
        "audience_profile": False,
        "audience_profile_snapshot": False,
    }
    assert "control_switchboard_files" in result
    assert result["control_switchboard_files"]["orchestrator_control_switchboard"] == str(
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
    assert "--repo-workdir" in captured["cmd"]
    repo_arg = captured["cmd"].index("--repo-workdir") + 1
    assert captured["cmd"][repo_arg] == str(repo_root)
