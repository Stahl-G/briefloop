from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

import yaml

from multi_agent_brief.cli.main import main
from multi_agent_brief.hermes import (
    build_hermes_cron_plan,
    render_hermes_prompt,
    render_hermes_setup_success,
    render_hermes_skill,
)
from tests.helpers import write_workspace_files_under


ROOT = Path(__file__).resolve().parent.parent
SOURCE_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


_write_workspace = partial(
    write_workspace_files_under,
    config_text="""
project:
  name: "AI Agent Weekly"
  company: "ExampleCo"
  industry: "AI agents"
  language: "zh-CN"
  audience: "management"
report:
  cadence: "weekly,monthly"
input:
  path: "input"
output:
  path: "output"
""".strip(),
    sources_text="""
source_strategy:
  profile: "conservative"
  enabled_providers:
    - "manual"
manual:
  enabled: true
  sources: []
""".strip(),
    include_input_dir=True,
)


def _assert_atomic_graph_boundary(text: str) -> None:
    normalized = " ".join(text.replace("`", "").split())
    assert "atomic_claim_graph.json" in normalized
    assert "optional experimental structural decomposition aid" in normalized
    assert "not source evidence or proof of support" in normalized
    assert "Do not cite atom IDs" in normalized or "do not cite atom IDs" in normalized
    assert "create, edit, rewrite, repair, or extend" in normalized or "create/edit/repair/extend" in normalized
    assert "material atoms absent" in normalized


def _assert_scoped_repair_guidance(text: str, workspace: str) -> None:
    assert f"briefloop gates show --workspace {workspace} --json" in text
    assert "follow its required_commands" in text
    assert "--gate-stage" in text
    assert "--gate-artifact" in text
    assert f"briefloop repair route --workspace {workspace} --json" in text
    assert "--finding-id <finding_id>" in text
    assert "--route-index <route_index>" in text
    assert "do not use unscoped repair start for current-gate blockers" in text
    bare_start = re.compile(
        rf"briefloop repair start --workspace {re.escape(workspace)}"
        r"(?![^\n`]*(?:--gate-stage|--finding-id|--route-index))"
    )
    offenders = [line for line in text.splitlines() if bare_start.search(line) and "do not use bare" not in line]
    assert offenders == []


# ---------------------------------------------------------------------------
# Cron plan structure tests
# ---------------------------------------------------------------------------

def test_build_hermes_cron_plan_has_daily_weekly_monthly(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    config = yaml.safe_load((ws / "config.yaml").read_text(encoding="utf-8"))

    plan = build_hermes_cron_plan(
        config=config,
        workspace=ws,
        repo_workdir=tmp_path,
        cadences=["weekly", "monthly"],
        deliver="feishu",
        profile="default",
    )

    assert plan.version == f"v{SOURCE_VERSION}"
    assert plan.cadences == ["weekly", "monthly"]
    assert len(plan.jobs) == 3

    # Daily job
    daily = plan.jobs[0]
    assert daily.schedule == "0 7 * * *"
    assert "daily source cache collection" in daily.prompt
    assert "YYYY-MM-DD.json" in daily.prompt

    # Weekly job
    weekly = plan.jobs[1]
    assert weekly.context_from == [plan.jobs[0].name]
    assert "Hermes-native delegated" in weekly.prompt
    assert "delegate_task" in weekly.prompt
    assert "Orchestrator main agent" in weekly.prompt
    assert "configs/orchestrator_contract.yaml" in weekly.prompt
    assert "retry_stage" in weekly.prompt
    assert "scout" in weekly.prompt
    assert "auditor" in weekly.prompt
    assert "briefloop gates check" in weekly.prompt
    assert "orchestrator_control_switchboard.json" in weekly.prompt
    assert "briefloop controls select" in weekly.prompt
    assert "Selection is not execution" in weekly.prompt
    assert "briefloop state check" in weekly.prompt
    assert "briefloop state stage-complete" in weekly.prompt
    assert "briefloop state finalize-complete" in weekly.prompt
    assert "briefloop provenance build" in weekly.prompt
    assert "not semantic proof" in weekly.prompt
    assert "finalize" in weekly.prompt
    assert weekly.prompt.index("briefloop controls select") < weekly.prompt.index("briefloop gates check")
    assert weekly.prompt.index("briefloop gates check") < weekly.prompt.index("briefloop finalize")
    assert "/generate-brief" not in weekly.prompt

    # Monthly job
    monthly = plan.jobs[2]
    assert monthly.context_from == [plan.jobs[0].name]
    assert "Hermes-native delegated" in monthly.prompt
    assert "delegate_task" in monthly.prompt
    assert "Orchestrator main agent" in monthly.prompt
    assert "configs/orchestrator_contract.yaml" in monthly.prompt
    assert "request_human_review" in monthly.prompt
    assert "month-level patterns" in monthly.prompt.lower()

    # Shared properties
    assert all("multi-agent-brief-hermes" in job.skills for job in plan.jobs)
    assert all(job.workdir == str(tmp_path.resolve()) for job in plan.jobs)


# ---------------------------------------------------------------------------
# Skill content tests — delegate_task native runtime
# ---------------------------------------------------------------------------

def test_hermes_skill_uses_delegate_task_runtime():
    skill = render_hermes_skill()
    assert "delegate_task" in skill
    assert "Hermes-native delegated" in skill
    assert "Orchestrator main agent" in skill
    assert "configs/orchestrator_contract.yaml" in skill
    assert "configs/stage_specs.yaml" in skill
    assert "configs/artifact_contracts.yaml" in skill
    assert "retry_stage" in skill
    assert "request_human_review" in skill
    assert "block_run" in skill
    assert "feedback ingest/plan/resolve/show/validate" in skill
    assert "gates check/show/validate" in skill
    assert "provenance build/show/validate" in skill
    assert "feedback_issues.json" in skill
    assert "repair_plan.json" in skill
    assert "auditor_quality_gate_report.json" in skill
    assert "finalize_quality_gate_report.json" in skill
    assert "quality_gate_report.json" in skill
    assert "provenance_graph.json" in skill
    assert "orchestrator_control_switchboard.json" in skill
    assert "control_selections.json" in skill
    assert "controls select" in skill
    assert "Selection is not execution" in skill
    assert "Audit warnings, overstatement findings, support-calibration findings" in skill
    assert "do not authorize direct edits to frozen artifacts" in skill
    assert "briefloop gates check --workspace <workspace> --stage auditor" in skill
    assert "briefloop gates check --workspace <workspace> --stage finalize" in skill
    assert "briefloop state check --workspace <workspace> --strict" in skill
    assert "briefloop state freeze-claim-ledger --workspace <workspace>" in skill
    assert "briefloop state stage-complete --workspace <workspace> --stage claim-ledger" in skill
    assert "briefloop state stage-complete --workspace <workspace> --stage auditor" in skill
    assert "briefloop state finalize-complete --workspace <workspace>" in skill
    assert (
        skill.index("briefloop state freeze-claim-ledger --workspace <workspace>")
        < skill.index("briefloop state stage-complete --workspace <workspace> --stage claim-ledger")
        < skill.index("#### 4. Analyst child")
    )
    assert "Formatter/finalize reads `output/intermediate/audited_brief.md` as frozen input" in skill
    assert "route repair to Editor" in skill
    assert "Did 0 searches" in skill
    assert "every query returns an empty result set" in skill
    assert "Do not switch to source-planner" in skill
    assert "finalize` is not a quality-gate executor" in skill
    assert "not semantic proof" in skill
    assert "scout" in skill
    assert "screener" in skill
    assert "claim-ledger" in skill
    assert "analyst" in skill
    assert "editor" in skill
    assert "auditor" in skill
    assert "briefloop finalize" in skill


def test_hermes_skill_keeps_users_inside_hermes():
    skill = render_hermes_skill()
    assert "/generate-brief" not in skill
    assert "Claude Code" not in skill
    assert "briefloop prepare" not in skill


def test_hermes_skill_has_setup_workflow():
    skill = render_hermes_skill()
    assert "Setup Workflow" in skill
    assert "Project is cloned and ready" in skill
    assert "I can continue generating the brief inside Hermes" in skill
    assert "delegate_task children" in skill.lower()


def test_hermes_skill_has_daily_cache_workflow():
    skill = render_hermes_skill()
    assert "Daily Source Cache Workflow" in skill
    assert "YYYY-MM-DD.json" in skill
    assert "hermes_daily_cache" in skill


def test_hermes_skill_has_delegation_sequence():
    skill = render_hermes_skill()
    assert "## Hermes-native Delegated Brief Workflow" in skill
    assert "Scout child" in skill
    assert "Screener child" in skill
    assert "Claim-ledger child" in skill
    assert "Analyst child" in skill
    assert "Editor child" in skill
    assert "Auditor child" in skill
    assert "Parent Orchestration" in skill
    assert "batch delegation with up to" in skill
    assert "scratch/intermediate runtime material" in skill
    assert "not workflow artifacts" in skill
    assert "join chunk outputs deterministically" in skill
    assert "stable ordering must use source identity" in skill
    assert "child completion" in skill
    assert "do not silently drop chunk-level outputs" in skill
    assert "Only the final joined" in skill
    assert "do not delegate Screener and do not call" in skill
    assert "state stage-complete --stage screener" in skill


def test_hermes_skill_contains_atomic_graph_boundary():
    skill = render_hermes_skill()
    _assert_atomic_graph_boundary(skill)


def test_hermes_skill_no_prepare_reference():
    skill = render_hermes_skill()
    assert "briefloop prepare" not in skill


# ---------------------------------------------------------------------------
# Prompt and setup success tests
# ---------------------------------------------------------------------------

def test_hermes_prompt_keeps_user_inside_hermes():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "delegate_task" in prompt
    assert "Hermes" in prompt
    assert "Orchestrator main agent" in prompt
    assert "configs/orchestrator_contract.yaml" in prompt


def test_hermes_prompt_contains_atomic_graph_boundary():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    _assert_atomic_graph_boundary(prompt)
    assert "configs/stage_specs.yaml" in prompt
    assert "configs/artifact_contracts.yaml" in prompt
    assert "retry_stage" in prompt
    assert "request_human_review" in prompt
    assert "block_run" in prompt
    assert "briefloop feedback ingest" in prompt
    assert "feedback show" in prompt
    assert "auditor_quality_gate_report.json" in prompt
    assert "finalize_quality_gate_report.json" in prompt
    assert "quality_gate_report.json" in prompt
    assert "provenance_graph.json" in prompt
    assert "orchestrator_control_switchboard.json" in prompt
    assert "control_selections.json" in prompt
    assert "briefloop controls select" in prompt
    assert "Selection is not execution" in prompt
    resolved_ws = str(Path("/tmp/test-ws").resolve())
    assert f"briefloop controls select --workspace {resolved_ws}" in prompt
    assert f"briefloop gates check --workspace {resolved_ws} --stage auditor" in prompt
    assert f"briefloop gates check --workspace {resolved_ws} --stage finalize" in prompt
    assert f"briefloop state check --workspace {resolved_ws} --strict" in prompt
    claim_ledger_freeze = f"briefloop state freeze-claim-ledger --workspace {resolved_ws}"
    claim_ledger_complete = f"briefloop state stage-complete --workspace {resolved_ws} --stage claim-ledger"
    assert claim_ledger_freeze in prompt
    assert claim_ledger_complete in prompt
    assert prompt.index(claim_ledger_freeze) < prompt.index(claim_ledger_complete)
    assert prompt.index(claim_ledger_complete) < prompt.index('Goal: "Draft the audited BriefLoop brief"')
    assert f"briefloop state stage-complete --workspace {resolved_ws} --stage auditor" in prompt
    assert f"briefloop state finalize-complete --workspace {resolved_ws}" in prompt
    _assert_scoped_repair_guidance(prompt, resolved_ws)
    assert f"briefloop repair complete --workspace {resolved_ws}" in prompt
    assert "Audit warnings, overstatement findings, support-calibration findings" in prompt
    assert "do not authorize direct edits to frozen artifacts" in prompt
    assert f"briefloop provenance build --workspace {resolved_ws}" in prompt
    assert f"briefloop provenance validate --workspace {resolved_ws}" in prompt
    assert prompt.index("briefloop controls select") < prompt.index("briefloop gates check")
    assert prompt.index("briefloop gates check") < prompt.index("briefloop finalize")
    assert prompt.index("briefloop finalize") < prompt.index("briefloop provenance build")
    assert "feedback_issues.json" in prompt
    assert "scout" in prompt
    assert "screener" in prompt
    assert "claim-ledger" in prompt
    assert "analyst" in prompt
    assert "editor" in prompt
    assert "auditor" in prompt
    assert "briefloop finalize" in prompt
    assert "/generate-brief" not in prompt
    assert "Claude Code" not in prompt


def test_hermes_setup_next_step_is_hermes_native():
    text = render_hermes_setup_success(
        repo="/tmp/test-repo",
        venv="/tmp/test-repo/.venv",
        workspace="/tmp/test-ws",
        version=f"v{SOURCE_VERSION}",
        doctor_status="passed",
    )
    assert "briefloop hermes prompt" in text
    assert "briefloop hermes install-skill" in text
    assert "Orchestrator main agent" in text
    assert "delegate" in text.lower()
    assert "/generate-brief" not in text
    assert "Claude Code" not in text


def test_hermes_prompt_contains_artifact_paths():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "candidate_claims.json" in prompt
    assert "screened_candidates.json" in prompt
    assert "claim_ledger.json" in prompt
    assert "audited_brief.md" in prompt
    assert "audit_report.json" in prompt
    assert "output/delivery/brief.md" in prompt


def test_hermes_skill_and_prompt_lock_source_metadata_contract():
    skill = render_hermes_skill()
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    combined = "\n".join([skill, prompt])

    assert "source_url only for HTTP(S) URLs" in combined
    assert "source_path for local/package sources" in combined
    assert "source_category" in combined
    assert "source_type" in combined
    assert "never put titles" in combined.lower()
    assert "Write: output/intermediate/claim_drafts.json" in combined
    assert "state freeze-claim-ledger" in combined
    assert "state stage-complete" in combined
    assert "--stage claim-ledger" in combined


def test_hermes_prompt_contains_doctor_and_sources():
    prompt = render_hermes_prompt(
        workspace="/tmp/test-ws",
        repo_workdir="/tmp/test-repo",
        venv_path="/tmp/test-repo/.venv/bin/activate",
    )
    assert "briefloop doctor" in prompt
    assert "briefloop sources decide" in prompt
    assert "briefloop inputs classify" in prompt


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_cli_hermes_cron_plan_writes_json_and_markdown(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    out = tmp_path / "plan.json"
    md = tmp_path / "plan.md"

    result = main([
        "hermes",
        "cron-plan",
        "--config",
        str(ws / "config.yaml"),
        "--repo-workdir",
        str(tmp_path),
        "--cadence",
        "weekly,monthly",
        "--deliver",
        "feishu",
        "--output",
        str(out),
        "--markdown",
        str(md),
    ])

    assert result == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == f"v{SOURCE_VERSION}"
    assert data["cadences"] == ["weekly", "monthly"]
    assert len(data["jobs"]) == 3
    assert "Hermes Cron Plan" in md.read_text(encoding="utf-8")


def test_cli_hermes_skill_writes_file(tmp_path: Path):
    output = tmp_path / "multi-agent-brief-hermes" / "SKILL.md"
    result = main(["hermes", "skill", "--output", str(output)])
    assert result == 0
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "Skills" not in text.splitlines()[0]
    assert "multi-agent-brief-hermes" in text
    _assert_atomic_graph_boundary(text)


def test_cli_hermes_sync_sources_enables_cached_package(tmp_path: Path):
    ws = _write_workspace(tmp_path)

    result = main(["hermes", "sync-sources", "--config", str(ws / "config.yaml")])

    assert result == 0
    data = yaml.safe_load((ws / "sources.yaml").read_text(encoding="utf-8"))
    assert "cached_package" in data["source_strategy"]["enabled_providers"]
    assert data["cached_package"]["enabled"] is True
    assert "input/hermes_cache" in data["cached_package"]["paths"]


def test_cli_hermes_install_skill(tmp_path: Path):
    target = tmp_path / "multi-agent-brief-hermes"
    result = main(["hermes", "install-skill", "--target", str(target)])
    assert result == 0
    assert (target / "SKILL.md").exists()
    content = (target / "SKILL.md").read_text(encoding="utf-8")
    assert "delegate_task" in content
    assert "multi-agent-brief-hermes" in content


def test_cli_hermes_prompt_generates_output(tmp_path: Path):
    ws = _write_workspace(tmp_path)
    result = main([
        "hermes", "prompt",
        "--config", str(ws / "config.yaml"),
        "--repo-workdir", str(tmp_path),
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert result == 0


def test_cli_hermes_prompt_output_contains_workflow_steps(capsys, tmp_path: Path):
    ws = _write_workspace(tmp_path)
    result = main([
        "hermes", "prompt",
        "--config", str(ws / "config.yaml"),
        "--repo-workdir", str(tmp_path),
        "--venv", str(tmp_path / ".venv" / "bin" / "activate"),
    ])
    assert result == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "delegate_task" in output
    assert "Orchestrator main agent" in output
    assert "configs/orchestrator_contract.yaml" in output
    _assert_atomic_graph_boundary(output)
    assert "retry_stage" in output
    assert "scout" in output
    assert "briefloop gates check" in output
    assert "briefloop state check" in output
    assert "briefloop state stage-complete" in output
    assert "briefloop state finalize-complete" in output
    assert "audience_profile_snapshot.md" in output
    assert "briefloop provenance build" in output
    assert output.index("briefloop gates check") < output.index("briefloop finalize")
    assert output.index("briefloop finalize") < output.index("briefloop provenance build")
    assert "briefloop finalize" in output
    assert "/generate-brief" not in output
    # Onboarding workflow path
    assert "chat-to-JSON onboarding" in output
    assert "Collect brief profile in chat" in output
    assert "briefloop init <workspace> --from-onboarding onboarding.json" in output
    assert "briefloop run --workspace <workspace>" in output
    # Plugin preferred path
    assert "Preferred" in output
    assert "Hermes Plugin" in output
    assert "integrations/hermes-plugin/mabw" in output


def test_hermes_skill_contains_onboarding_workflow():
    """Hermes SKILL.md must reference the plugin as preferred path and have fallback onboarding."""
    skill_path = Path(__file__).resolve().parent.parent / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # Preferred path: plugin
    assert "Preferred Path" in content
    assert "Hermes Plugin" in content
    assert "integrations/hermes-plugin/mabw" in content
    assert "mabw_create_onboarding" in content
    assert "mabw_init_workspace" in content
    assert "mabw_run_handoff" in content
    # Fallback path: chat-to-JSON
    assert "Fallback" in content
    assert "chat-to-JSON" in content
    assert "Collect brief profile in chat" in content
    assert "briefloop init <workspace> --from-onboarding onboarding.json" in content
    assert "briefloop run --workspace <workspace>" in content
    assert "Do not call `briefloop run` again mid-pipeline" in content
    assert "delegate_task" in content
    assert "gates check + state check + state stage-complete" in content
    assert "state finalize-complete" in content
    assert "audience_profile_snapshot.md" in content
    assert "Do not treat `audience_profile.md` as evidence" in content
    assert "finalize` is not a quality-gate executor" in content
    assert "provenance build" in content
    assert "not semantic proof" in content
    assert "Orchestrator main agent" in content
    assert "configs/orchestrator_contract.yaml" in content
    assert "retry_stage" in content


def test_hermes_skill_template_matches_checked_in_contract() -> None:
    """`briefloop hermes install-skill` renders from the adapter template while
    the checked-in Hermes skill is hand-maintained. Both must carry the same
    RC contract phrases so the installed and checked-in skills cannot drift on
    repair scoping or finalize/delivery truth."""
    template = render_hermes_skill()
    checked_in = (
        ROOT / ".agents" / "hermes-skills" / "multi-agent-brief-hermes" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for phrase in [
        "briefloop gates show --workspace <workspace>",
        "--gate-stage",
        "--gate-artifact",
        "briefloop repair route --workspace <workspace> --json",
        "--finding-id",
        "--route-index",
        'delivery_promotion "promoted"',
        "briefloop workbuddy diagnose --workspace <workspace> --json",
        "briefloop repair",
        "request_human_review",
    ]:
        assert phrase in template, f"hermes template missing contract phrase: {phrase}"
        assert phrase in checked_in, f"checked-in hermes skill missing contract phrase: {phrase}"
