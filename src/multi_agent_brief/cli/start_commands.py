"""start / handoff — unified launcher that hands off to the agent runtime.

start never generates a brief; it never calls BriefPipeline or prepare.
It handles workspace init/doctor and produces a runtime handoff artifact
so the user's current agent (Hermes, Claude Code, Codex, OpenCode, or
manual) can continue from a known state.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


RUNTIME_AUTO = "auto"
RUNTIME_HERMES = "hermes"
RUNTIME_CLAUDE = "claude"
RUNTIME_OPENCODE = "opencode"
RUNTIME_CODEX = "codex"
RUNTIME_MANUAL = "manual"
VALID_RUNTIMES = (RUNTIME_AUTO, RUNTIME_HERMES, RUNTIME_CLAUDE, RUNTIME_OPENCODE, RUNTIME_CODEX, RUNTIME_MANUAL)
RUNTIME_RESOLVED = {RUNTIME_AUTO: RUNTIME_HERMES}  # auto resolves to hermes in v0.5.5


@dataclass
class AgentHandoff:
    runtime: str
    workspace: str
    repo_workdir: str
    venv_activate: str
    doctor_status: str = "not_run"
    next_steps: str = ""
    prompt: str = ""
    expected_artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_config(workspace: Path) -> dict[str, Any] | None:
    import yaml
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        return None
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _find_venv_activate(repo: Path) -> str:
    if sys.platform == "win32":
        cand = repo / ".venv" / "Scripts" / "activate"
        return str(cand) if cand.exists() else str(cand)
    cand = repo / ".venv" / "bin" / "activate"
    return str(cand) if cand.exists() else str(cand)


def _run_doctor(workspace: Path) -> tuple[int, str]:
    config = workspace / "config.yaml"
    if not config.exists():
        return 1, "no_config"
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "multi_agent_brief.cli.main", "doctor", "--config", str(config)],
        capture_output=True, text=True,
        cwd=str(workspace),
    )
    output = result.stdout + result.stderr
    if result.returncode == 0 and "FAIL" not in output:
        return 0, "passed"
    return result.returncode, "failed"


def _hermes_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    from multi_agent_brief.hermes import render_hermes_prompt
    prompt = render_hermes_prompt(
        workspace=workspace,
        repo_workdir=repo,
        venv_path=venv,
    )
    return AgentHandoff(
        runtime=RUNTIME_HERMES,
        workspace=str(workspace.resolve()),
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=(
            "Paste the prompt below into Hermes to run the delegated brief workflow. "
            "Hermes parent agent will orchestrate: "
            "scout → screener → claim-ledger → analyst → editor → auditor → finalize."
        ),
        prompt=prompt,
        expected_artifacts=[
            str(workspace.resolve() / "output" / "intermediate" / "candidate_claims.json"),
            str(workspace.resolve() / "output" / "intermediate" / "screened_candidates.json"),
            str(workspace.resolve() / "output" / "intermediate" / "claim_ledger.json"),
            str(workspace.resolve() / "output" / "intermediate" / "audited_brief.md"),
            str(workspace.resolve() / "output" / "intermediate" / "audit_report.json"),
            str(workspace.resolve() / "output" / "brief.md"),
        ],
        notes=[
            "Install the MABW Hermes plugin: cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw && hermes plugins enable mabw",
            "Then in Hermes: /mabw <workspace> → mabw_create_onboarding → mabw_init_workspace → mabw_run_handoff → read agent_handoff.md → continue delegated workflow.",
            "Each delegate_task child needs complete goal, context, input paths, and output paths.",
            "Parent must verify each artifact before proceeding to the next child.",
        ],
    )


def _claude_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_CLAUDE,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=f"In Claude Code, run: /generate-brief {ws_path}",
        prompt=(
            f"Use /generate-brief {ws_path} to run the full subagent workflow:\n"
            "scout → screener → claim-ledger → analyst → editor → auditor → finalize.\n\n"
            f"Repository: {repo.resolve()}\n"
            f"Workspace: {ws_path}\n"
            f"Activate venv: source {venv}"
        ),
        expected_artifacts=[
            str(workspace.resolve() / "output" / "intermediate" / "claim_ledger.json"),
            str(workspace.resolve() / "output" / "intermediate" / "audited_brief.md"),
            str(workspace.resolve() / "output" / "intermediate" / "audit_report.json"),
            str(workspace.resolve() / "output" / "brief.md"),
        ],
        notes=[
            "Claude Code must be opened from the repository root.",
            "The /generate-brief command handles the full subagent pipeline.",
        ],
    )


def _opencode_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_OPENCODE,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=f"In OpenCode, use the generate-brief command to run the full subagent workflow for {ws_path}.",
        prompt=(
            f"Workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "Run the OpenCode generate-brief command. It will orchestrate:\n"
            "scout → screener → claim-ledger → analyst → editor → auditor → finalize."
        ),
        expected_artifacts=[
            str(workspace.resolve() / "output" / "intermediate" / "claim_ledger.json"),
            str(workspace.resolve() / "output" / "intermediate" / "audited_brief.md"),
            str(workspace.resolve() / "output" / "intermediate" / "audit_report.json"),
            str(workspace.resolve() / "output" / "brief.md"),
        ],
        notes=[
            "OpenCode agent configs are in .opencode/.",
        ],
    )


def _codex_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_CODEX,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=f"In Codex, invoke the generate-brief agent workflow for {ws_path}.",
        prompt=(
            f"Workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "Codex agent roles are in .codex/agents/. Use the Codex agent workflow:\n"
            "scout → screener → claim-ledger → analyst → editor → auditor → finalize."
        ),
        expected_artifacts=[
            str(workspace.resolve() / "output" / "intermediate" / "claim_ledger.json"),
            str(workspace.resolve() / "output" / "intermediate" / "audited_brief.md"),
            str(workspace.resolve() / "output" / "intermediate" / "audit_report.json"),
            str(workspace.resolve() / "output" / "brief.md"),
        ],
        notes=[
            "Codex agent configs are in .codex/agents/.",
        ],
    )


def _manual_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_MANUAL,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=(
            "Manually run each subagent step. After all artifacts are ready, "
            f"run: multi-agent-brief finalize --config {ws_path}/config.yaml"
        ),
        prompt=(
            f"Manual workflow for workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "Run each step in order, verifying each artifact before continuing:\n\n"
            f"1. multi-agent-brief doctor --config {ws_path}/config.yaml\n"
            f"2. multi-agent-brief sources decide --config {ws_path}/config.yaml  (if configured)\n"
            f"3. multi-agent-brief inputs classify --config {ws_path}/config.yaml  (if available)\n"
            "4. Use the 'scout' subagent to write output/intermediate/candidate_claims.json\n"
            "5. Use the 'screener' subagent to write output/intermediate/screened_candidates.json\n"
            "6. Use the 'claim-ledger' subagent to write output/intermediate/claim_ledger.json\n"
            "7. Use the 'analyst' subagent to write output/intermediate/audited_brief.md\n"
            "8. Use the 'editor' subagent to polish output/intermediate/audited_brief.md\n"
            "9. Use the 'auditor' subagent to write output/intermediate/audit_report.json\n"
            f"10. multi-agent-brief finalize --config {ws_path}/config.yaml"
        ),
        expected_artifacts=[
            str(workspace.resolve() / "output" / "intermediate" / "candidate_claims.json"),
            str(workspace.resolve() / "output" / "intermediate" / "screened_candidates.json"),
            str(workspace.resolve() / "output" / "intermediate" / "claim_ledger.json"),
            str(workspace.resolve() / "output" / "intermediate" / "audited_brief.md"),
            str(workspace.resolve() / "output" / "intermediate" / "audit_report.json"),
            str(workspace.resolve() / "output" / "brief.md"),
        ],
        notes=[
            "Each subagent step must complete before the next begins.",
            "Verify each artifact exists and is non-empty before continuing.",
            "The 'auditor' step must run before finalize.",
        ],
    )


_HANDOFF_BUILDERS = {
    RUNTIME_HERMES: _hermes_handoff,
    RUNTIME_CLAUDE: _claude_handoff,
    RUNTIME_OPENCODE: _opencode_handoff,
    RUNTIME_CODEX: _codex_handoff,
    RUNTIME_MANUAL: _manual_handoff,
}


def build_handoff(
    *,
    workspace: str | Path,
    repo_workdir: str | Path,
    runtime: str,
    venv: str | None = None,
    run_doctor: bool = True,
) -> AgentHandoff:
    ws = Path(workspace).resolve()
    repo = Path(repo_workdir).resolve()
    venv_activate = venv or _find_venv_activate(repo)

    # resolve auto -> hermes in v0.5.5
    resolved = RUNTIME_RESOLVED.get(runtime, runtime)

    if resolved not in _HANDOFF_BUILDERS:
        raise ValueError(f"Unknown runtime '{runtime}'. Valid: {', '.join(VALID_RUNTIMES)}")

    builder = _HANDOFF_BUILDERS[resolved]
    handoff = builder(ws, repo, venv_activate)

    if run_doctor:
        rc, status = _run_doctor(ws)
        handoff.doctor_status = status
        if status == "passed":
            handoff.notes.insert(0, f"Doctor: passed")

    return handoff


def render_handoff_cli(handoff: AgentHandoff) -> str:
    lines = [
        "=" * 60,
        f"  Runtime: {handoff.runtime}",
        f"  Workspace: {handoff.workspace}",
        f"  Doctor: {handoff.doctor_status}",
        "=" * 60,
        "",
        handoff.next_steps,
        "",
    ]
    if handoff.notes:
        lines.append("Notes:")
        for n in handoff.notes:
            lines.append(f"  - {n}")
        lines.append("")
    return "\n".join(lines)


def write_handoff_artifacts(handoff: AgentHandoff, workspace: Path) -> tuple[Path, Path]:
    intermediate = workspace / "output" / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)

    md_path = intermediate / "agent_handoff.md"
    md_content = [
        "# Agent Handoff",
        "",
        f"- Runtime: {handoff.runtime}",
        f"- Workspace: {handoff.workspace}",
        f"- Repository: {handoff.repo_workdir}",
        f"- Venv activate: {handoff.venv_activate}",
        f"- Doctor: {handoff.doctor_status}",
        "",
        "## Next Steps",
        "",
        handoff.next_steps,
        "",
        "## Prompt",
        "",
        "```text",
        handoff.prompt,
        "```",
        "",
        "## Expected Artifacts",
        "",
    ]
    for a in handoff.expected_artifacts:
        md_content.append(f"- `{a}`")
    md_content.append("")
    if handoff.notes:
        md_content.append("## Notes")
        md_content.append("")
        for n in handoff.notes:
            md_content.append(f"- {n}")
        md_content.append("")

    md_path.write_text("\n".join(md_content), encoding="utf-8")

    json_path = intermediate / "agent_handoff.json"
    json_path.write_text(
        json.dumps(handoff.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return md_path, json_path
