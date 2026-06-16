"""start / handoff — unified launcher that hands off to the agent runtime.

start never generates a brief; it never calls BriefPipeline or prepare.
It handles workspace init/doctor and produces a runtime handoff artifact
so the user's current agent (Hermes, Claude Code, Codex, OpenCode, or
manual) can continue from a known state.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from multi_agent_brief.orchestrator_contract import (
    CONTRACT_REFERENCES,
    ORCHESTRATOR_LOOP,
    resolve_repo_workdir,
)
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    load_artifact_contracts,
    load_stage_specs,
)
from multi_agent_brief.contracts.role_topology import ROLE_TOPOLOGY_VALUES
from multi_agent_brief.orchestrator.fact_layer_import import require_fast_rerun_handoff_ready
from multi_agent_brief.audience_memory import AUDIENCE_MEMORY_FILES
from multi_agent_brief.controls.contract import CONTROL_SWITCHBOARD_FILES
from multi_agent_brief.feedback.feedback_contract import FEEDBACK_STATE_FILES
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_STATE_FILES
from multi_agent_brief.provenance.contract import PROVENANCE_STATE_FILES


RUNTIME_AUTO = "auto"
RUNTIME_HERMES = "hermes"
RUNTIME_CLAUDE = "claude"
RUNTIME_OPENCODE = "opencode"
RUNTIME_CODEX = "codex"
RUNTIME_MANUAL = "manual"
VALID_RUNTIMES = (RUNTIME_AUTO, RUNTIME_HERMES, RUNTIME_CLAUDE, RUNTIME_OPENCODE, RUNTIME_CODEX, RUNTIME_MANUAL)
RUNTIME_RESOLVED = {RUNTIME_AUTO: RUNTIME_HERMES}  # auto resolves to hermes in v0.5.5
RUNTIME_RECIPE_FULL = "full"
RUNTIME_RECIPE_FAST_RERUN = "fast-rerun"
VALID_RUNTIME_RECIPES = (RUNTIME_RECIPE_FULL, RUNTIME_RECIPE_FAST_RERUN)
EXPECTED_WORKFLOW_ARTIFACTS = [
    "output/intermediate/candidate_claims.json",
    "output/intermediate/screened_candidates.json",
    "output/intermediate/claim_ledger.json",
    "output/intermediate/audited_brief.md",
    "output/intermediate/audit_report.json",
    "output/delivery/brief.md",
]
REPAIR_GUIDANCE_NOTE = (
    "Repair guidance is bounded runtime guidance, not an automatic trajectory "
    "regulator: if the same stage has already needed roughly three retry/repair "
    "rounds, prefer request_human_review or block_run; if a repair would touch "
    "more than two sections, narrow the scope before delegating or request human review."
)
DECISION_RECORDING_NOTE = (
    "Record every successful stage completion before moving on: run "
    "`multi-agent-brief state stage-complete --workspace <workspace> --stage <stage_id> "
    "--reason \"<reason>\"`. Use low-level `multi-agent-brief state decide` only for "
    "retry_stage, delegate_repair, request_human_review, or block_run decisions. "
    "Use only decisions allowed by `workflow_state.json.next_allowed_decisions`. "
    "If the transaction is rejected, stop and correct the stage state instead of continuing."
)
FINALIZE_GATE_NOTE = (
    "Before finalize, after the auditor stage completes, run "
    "`multi-agent-brief gates check --workspace <workspace> --stage auditor` and "
    "`multi-agent-brief state check --workspace <workspace> --strict`; this "
    "creates or refreshes `output/intermediate/gates/auditor_quality_gate_report.json` "
    "and a legacy latest projection at `output/intermediate/quality_gate_report.json`. If "
    "there are blocking findings, do not finalize. Use feedback/repair, "
    "request_human_review, or block_run. Complete auditor with "
    "`multi-agent-brief state stage-complete --workspace <workspace> --stage auditor "
    "--reason \"Audit and quality gates passed.\"` only when audit readiness and "
    "quality gates pass. After the finalize tool writes delivery artifacts under "
    "`output/delivery/`, run `multi-agent-brief gates check --workspace <workspace> "
    "--stage finalize --brief <workspace>/output/brief.md`, then run "
    "`multi-agent-brief state finalize-complete --workspace <workspace> --reason "
    "\"Reader artifacts finalized and clean.\"`."
)
ROLE_TOPOLOGY_HANDOFF_NOTE = (
    "Role topology note: read configs/policy_packs/default.yaml before delegating. "
    "With role_topology=default, Scout performs discovery and screening in one role: "
    "write output/intermediate/candidate_claims.json first, then "
    "output/intermediate/screened_candidates.json, and record "
    "`state stage-complete --stage scout`; the screener stage is satisfied by topology "
    "and must not be run as a separate specialist. With role_topology=strict, Scout "
    "writes only candidate_claims.json, then delegate Screener to write "
    "screened_candidates.json. In all modes both artifacts remain required evidence "
    "for Claim Ledger. Runtime may split Scout work internally, but chunk outputs "
    "are scratch material only; only deterministic joined artifacts count."
)
RUNTIME_WEBSEARCH_ZERO_RESULT_NOTE = (
    "Runtime WebSearch zero-result guard: if runtime WebSearch reports `Did 0 searches`, "
    "or every query returns an empty result set, stop and request human review. "
    "Do not switch to source-planner or continue with stale sources."
)
STAGE_COMPLETION_PROTOCOL_SCHEMA = "multi-agent-brief-stage-completion-protocol/v1"
STAGE_COMPLETION_PROTOCOL_RULES = [
    "Stage completion is artifact-based, not statement-based.",
    "A natural-language acknowledgement such as 'I completed the stage' is not sufficient unless the required artifact paths are present and validated.",
    "Before moving to the next stage, verify required output artifacts exist at the declared paths and have the expected shape where validators exist.",
    "If a required artifact is missing, stale, or invalid, stop the stage and record retry_stage, request_human_review, or block_run instead of continuing.",
    "source_candidates.yaml is a source plan only, not source evidence; Scout must extract candidates from actual source content or materialized source files.",
    "Runtime WebSearch results must be materialized before source-discovery completion as durable source files with URL, source title/name, published date or retrieved_at, and raw excerpt/snippet.",
    "After state stage-complete succeeds, that stage's output artifacts are frozen for downstream stages; later stages must not rewrite them in place.",
    "If a downstream stage finds schema mismatch or invalid frozen upstream artifacts, route repair back to the owner stage instead of editing the artifact directly.",
    "Every stage handoff to a child agent must include complete context, required input artifact paths, required output artifact paths, and forbidden actions.",
    "Scout chunk outputs are scratch material only; if Scout work is split across chunks or child agents, join chunks deterministically before writing candidate_claims.json.",
    "Record successful stage transitions with multi-agent-brief state stage-complete only after artifact-level completion evidence is available.",
    "Record finalize completion with multi-agent-brief state finalize-complete after delivery artifacts and finalize_report.json are clean.",
]
DEFAULT_STAGE_FORBIDDEN_ACTIONS = [
    "Do not claim stage completion based on prose acknowledgement alone.",
    "Do not proceed to the next stage without naming the produced artifact path.",
    "Do not mutate upstream input artifacts except through the stage's declared output artifacts.",
    "Do not treat source_candidates.yaml as source evidence or use it to support claims.",
    "Do not rewrite a previous stage's artifact after that stage has completed; schema mismatch must route back to the owner stage for repair.",
    "Do not invent source evidence, claim support, citations, or validation results.",
    "Do not bypass workflow_state.json next_allowed_decisions or skip state stage-complete/finalize-complete.",
]


@dataclass
class AgentHandoff:
    runtime: str
    recipe: str
    workspace: str
    repo_workdir: str
    venv_activate: str
    doctor_status: str = "not_run"
    next_steps: str = ""
    prompt: str = ""
    expected_artifacts: list[str] = field(default_factory=list)
    runtime_state_files: dict[str, str] = field(default_factory=lambda: dict(RUNTIME_STATE_FILES))
    audience_memory_files: dict[str, str] = field(default_factory=lambda: dict(AUDIENCE_MEMORY_FILES))
    improvement_memory_files: dict[str, str] = field(default_factory=dict)
    control_switchboard_files: dict[str, str] = field(default_factory=lambda: dict(CONTROL_SWITCHBOARD_FILES))
    feedback_state_files: dict[str, str] = field(default_factory=lambda: dict(FEEDBACK_STATE_FILES))
    quality_gate_state_files: dict[str, str] = field(default_factory=lambda: dict(QUALITY_GATE_STATE_FILES))
    provenance_state_files: dict[str, str] = field(default_factory=lambda: dict(PROVENANCE_STATE_FILES))
    contract_references: dict[str, str] = field(default_factory=lambda: dict(CONTRACT_REFERENCES))
    stage_completion_protocol: dict[str, Any] = field(default_factory=dict)
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
    env = os.environ.copy()
    package_root = Path(__file__).resolve().parents[2]
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{package_root}{os.pathsep}{current_pythonpath}"
        if current_pythonpath
        else str(package_root)
    )
    result = subprocess.run(
        [sys.executable, "-m", "multi_agent_brief.cli.main", "doctor", "--config", str(config)],
        capture_output=True, text=True,
        cwd=str(workspace),
        env=env,
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
        recipe=RUNTIME_RECIPE_FULL,
        workspace=str(workspace.resolve()),
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=(
            "Paste the prompt below into Hermes. The Hermes parent agent is the "
            "Orchestrator main agent and will run the contract-guided delegated workflow."
        ),
        prompt=prompt,
        expected_artifacts=list(EXPECTED_WORKFLOW_ARTIFACTS),
        notes=[
            "Install the MABW Hermes plugin: cp -R integrations/hermes-plugin/mabw ~/.hermes/plugins/mabw && hermes plugins enable mabw",
            "Then in Hermes: /mabw <workspace> → mabw_create_onboarding → mabw_init_workspace → mabw_run_handoff → read agent_handoff.md → continue delegated workflow.",
            "Read configs/orchestrator_contract.yaml, configs/stage_specs.yaml, configs/artifact_contracts.yaml, and configs/policy_packs/default.yaml before delegation.",
            "Read output/intermediate/runtime_manifest.json, workflow_state.json, artifact_registry.json, and event_log.jsonl before selecting the next stage.",
            "Read output/intermediate/audience_profile_snapshot.md at run start for reader taste context; do not treat audience_profile.md as source evidence.",
            "Read output/intermediate/orchestrator_control_switchboard.json; record enable/defer/reject selections before explicitly executing any selected control.",
            DECISION_RECORDING_NOTE,
            FINALIZE_GATE_NOTE,
            REPAIR_GUIDANCE_NOTE,
            f"Orchestrator loop: {ORCHESTRATOR_LOOP}",
            "Each delegate_task child needs complete goal, context, input paths, and output paths.",
            "Parent must verify each artifact before proceeding to the next child.",
        ],
    )


def _claude_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_CLAUDE,
        recipe=RUNTIME_RECIPE_FULL,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=f"In Claude Code, run: /generate-brief {ws_path}. The command context is the Orchestrator main agent.",
        prompt=(
            f"Use /generate-brief {ws_path} as the Orchestrator main-agent entrypoint.\n"
            "Read contract references before delegation:\n"
            "- configs/orchestrator_contract.yaml\n"
            "- configs/stage_specs.yaml\n"
            "- configs/artifact_contracts.yaml\n"
            "- configs/policy_packs/default.yaml\n\n"
            f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n\n"
            "Delegated stage order:\n"
            "default: scout(discovery+screening) → claim-ledger → analyst → editor/Delivery Editor → auditor → finalize.\n"
            "strict: scout → screener → claim-ledger → analyst → editor/Delivery Editor → auditor → finalize.\n\n"
            f"{ROLE_TOPOLOGY_HANDOFF_NOTE}\n\n"
            "Read runtime state files before selecting the next stage:\n"
            "- output/intermediate/runtime_manifest.json\n"
            "- output/intermediate/workflow_state.json\n"
            "- output/intermediate/artifact_registry.json\n"
            "- output/intermediate/event_log.jsonl\n\n"
            "Read audience memory snapshot for this run:\n"
            "- output/intermediate/audience_profile_snapshot.md\n"
            "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
            "Read the Orchestrator control switchboard:\n"
            "- output/intermediate/orchestrator_control_switchboard.json\n"
            "Record control selections with multi-agent-brief controls select. Selection is not execution; explicitly run the selected CLI/subagent/human action after selection and approval.\n\n"
            f"{DECISION_RECORDING_NOTE}\n\n"
            f"{FINALIZE_GATE_NOTE}\n\n"
            f"{REPAIR_GUIDANCE_NOTE}\n\n"
            f"Repository: {repo.resolve()}\n"
            f"Workspace: {ws_path}\n"
            f"Activate venv: source {venv}"
        ),
        expected_artifacts=list(EXPECTED_WORKFLOW_ARTIFACTS),
        notes=[
            "Claude Code must be opened from the repository root.",
            "The /generate-brief command handles the Orchestrator-led delegated workflow.",
        ],
    )


def _opencode_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_OPENCODE,
        recipe=RUNTIME_RECIPE_FULL,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=f"In OpenCode, use the generate-brief command for {ws_path}. brief-orchestrator is the primary Orchestrator main agent.",
        prompt=(
            f"Workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "Run the OpenCode generate-brief command through brief-orchestrator.\n"
            "Read contract references before delegation:\n"
            "- configs/orchestrator_contract.yaml\n"
            "- configs/stage_specs.yaml\n"
            "- configs/artifact_contracts.yaml\n"
            "- configs/policy_packs/default.yaml\n\n"
            f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n\n"
            "Delegated stage order:\n"
            "default: scout(discovery+screening) → claim-ledger → analyst → editor/Delivery Editor → auditor → finalize.\n"
            "strict: scout → screener → claim-ledger → analyst → editor/Delivery Editor → auditor → finalize.\n\n"
            f"{ROLE_TOPOLOGY_HANDOFF_NOTE}\n\n"
            "Read runtime state files before selecting the next stage:\n"
            "- output/intermediate/runtime_manifest.json\n"
            "- output/intermediate/workflow_state.json\n"
            "- output/intermediate/artifact_registry.json\n"
            "- output/intermediate/event_log.jsonl\n\n"
            "Read audience memory snapshot for this run:\n"
            "- output/intermediate/audience_profile_snapshot.md\n"
            "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
            "Read the Orchestrator control switchboard:\n"
            "- output/intermediate/orchestrator_control_switchboard.json\n"
            "Record control selections with multi-agent-brief controls select. Selection is not execution; explicitly run the selected CLI/subagent/human action after selection and approval.\n\n"
            f"{DECISION_RECORDING_NOTE}\n\n"
            f"{FINALIZE_GATE_NOTE}\n\n"
            f"{REPAIR_GUIDANCE_NOTE}"
        ),
        expected_artifacts=list(EXPECTED_WORKFLOW_ARTIFACTS),
        notes=[
            "OpenCode agent configs are in .opencode/.",
        ],
    )


def _codex_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    role_mapping = (
        "Codex custom agent mapping:\n"
        "- scout -> .codex/agents/scout.toml (default: discovery + screening; strict: discovery only)\n"
        "- screener -> .codex/agents/screener.toml (strict topology or explicit repair/review only)\n"
        "- claim-ledger -> .codex/agents/claim-ledger.toml\n"
        "- analyst -> .codex/agents/analyst.toml\n"
        "- editor -> .codex/agents/editor.toml\n"
        "- auditor -> .codex/agents/auditor.toml\n"
        "- formatter/finalize -> Python finalize tool, not a drafting agent\n"
    )
    writer_flow_protocol = (
        "Codex writer flow protocol:\n"
        "- When the user asks to inspect a folder, produce a Workspace Card before taking action: "
        "workspace path, MABW config found/missing, Codex runtime kit installed/not installed, "
        "trust status, input source count, demo-looking sources yes/no, existing output/control state, "
        "current workflow_state, and recommended next action.\n"
        "- Trust status is one Workspace Card line, not the main answer.\n"
        "- Do not launch the interactive terminal onboarding wizard inside Codex chat.\n"
        "- For workspace creation, collect onboarding fields in one batch, write onboarding.json, "
        "show the values to be written, then run multi-agent-brief init --from-onboarding.\n"
        "- Before initializing into an existing directory, check output/intermediate/runtime_manifest.json, "
        "workflow_state.json, artifact_registry.json, event_log.jsonl, and output/runs/. If present, ask whether "
        "to create a new workspace, overwrite config only while keeping old output, or reset old output/control state "
        "before running.\n"
        "- After init or config inspection, show a Source Mode Card: manual local files enabled/disabled, "
        "runtime WebSearch enabled/disabled, external API search enabled/disabled, existing source files count, "
        "and demo-looking source files yes/no.\n"
        "- If using Codex/runtime WebSearch, write collected public sources into input/sources/ as durable source files "
        "with URL, source title/name, published date or retrieved_at, and raw excerpt/snippet. Summary-only notes "
        "are discovery hints, not evidence.\n"
        "- Do not call sources decide --search unless web_search.mode is external_api.\n"
        "- Do not call sources decide --merge on source_plan_only artifacts.\n"
        "- source_candidates.yaml is planning/review only, not evidence.\n"
        "- If runtime_tool search and old demo-looking source files both exist, ask whether to keep or remove "
        "the old source files before running.\n"
        "- During production runs, report progress after every successful stage-complete transaction in this form: "
        "[stage] produced <artifact> -> stage-complete passed -> next <stage>.\n"
        "- Final status must list the delivery bundle and control status: gates, finalize_report, finalize-complete, "
        "and archive.\n"
    )
    return AgentHandoff(
        runtime=RUNTIME_CODEX,
        recipe=RUNTIME_RECIPE_FULL,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=(
            f"In Codex, use the root session as the Orchestrator main agent for {ws_path}. "
            "Spawn named Codex custom agents directly for specialist stages."
        ),
        prompt=(
            f"Workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "You are the Orchestrator main agent in the root Codex session.\n"
            "Do not invoke an orchestrator subagent that then invokes other subagents; "
            "Codex child-agent depth should stay at one.\n"
            "Do not use the Claude/OpenCode slash-command workflow; that is not the Codex runtime path.\n"
            "Codex custom agents are in .codex/agents/. Spawn the named Codex custom agent for each specialist stage.\n"
            "Codex loads project .codex/config.toml and custom agents only after the workspace is trusted in Codex.\n"
            "If Codex cannot see these custom agents, stop and ask the user to install Codex runtime assets.\n\n"
            f"{writer_flow_protocol}\n"
            "Read contract references before delegation:\n"
            "- configs/orchestrator_contract.yaml\n"
            "- configs/stage_specs.yaml\n"
            "- configs/artifact_contracts.yaml\n"
            "- configs/policy_packs/default.yaml\n\n"
            f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n\n"
            f"{ROLE_TOPOLOGY_HANDOFF_NOTE}\n\n"
            f"{role_mapping}\n"
            "Do not call the next specialist until `multi-agent-brief state stage-complete` succeeds for the current stage.\n"
            "Finalize is a Python delivery/rendering tool. After finalize writes delivery artifacts, record completion with `multi-agent-brief state finalize-complete`.\n\n"
            "Read runtime state files before selecting the next stage:\n"
            "- output/intermediate/runtime_manifest.json\n"
            "- output/intermediate/workflow_state.json\n"
            "- output/intermediate/artifact_registry.json\n"
            "- output/intermediate/event_log.jsonl\n\n"
            "Read audience memory snapshot for this run:\n"
            "- output/intermediate/audience_profile_snapshot.md\n"
            "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
            "Read the Orchestrator control switchboard:\n"
            "- output/intermediate/orchestrator_control_switchboard.json\n"
            "Record control selections with multi-agent-brief controls select. Selection is not execution; explicitly run the selected CLI/subagent/human action after selection and approval.\n\n"
            f"{DECISION_RECORDING_NOTE}\n\n"
            f"{FINALIZE_GATE_NOTE}\n\n"
            f"{REPAIR_GUIDANCE_NOTE}"
        ),
        expected_artifacts=list(EXPECTED_WORKFLOW_ARTIFACTS),
        notes=[
            "Codex agent configs are in .codex/agents/.",
            "Codex must trust the workspace before project .codex/config.toml and custom agents load.",
            "The root Codex session is the Orchestrator main agent; spawn specialist custom agents directly.",
            "If Codex cannot see custom agents, run `multi-agent-brief runtime install --workspace <workspace> --runtime codex --repo-workdir <repo>` from a source clone.",
        ],
    )


def _manual_handoff(workspace: Path, repo: Path, venv: str) -> AgentHandoff:
    ws_path = str(workspace.resolve())
    return AgentHandoff(
        runtime=RUNTIME_MANUAL,
        recipe=RUNTIME_RECIPE_FULL,
        workspace=ws_path,
        repo_workdir=str(repo.resolve()),
        venv_activate=venv,
        next_steps=(
            "Use the manual fallback as the Orchestrator main agent. After all artifacts are ready, "
            f"run: multi-agent-brief finalize --config {ws_path}/config.yaml"
        ),
        prompt=(
            f"Manual workflow for workspace: {ws_path}\n"
            f"Repository: {repo.resolve()}\n"
            f"Activate venv: source {venv}\n\n"
            "Read contract references before delegation:\n"
            "- configs/orchestrator_contract.yaml\n"
            "- configs/stage_specs.yaml\n"
            "- configs/artifact_contracts.yaml\n"
            "- configs/policy_packs/default.yaml\n\n"
            f"Orchestrator loop: {ORCHESTRATOR_LOOP}\n\n"
            f"{ROLE_TOPOLOGY_HANDOFF_NOTE}\n\n"
            "Read runtime state files before selecting the next stage:\n"
            "- output/intermediate/runtime_manifest.json\n"
            "- output/intermediate/workflow_state.json\n"
            "- output/intermediate/artifact_registry.json\n"
            "- output/intermediate/event_log.jsonl\n\n"
            "Read audience memory snapshot for this run:\n"
            "- output/intermediate/audience_profile_snapshot.md\n"
            "Summarize relevant taste guidance for delegated roles. Do not treat audience_profile.md as source evidence, and do not use mid-run profile edits until the next run.\n\n"
            "Read the Orchestrator control switchboard:\n"
            "- output/intermediate/orchestrator_control_switchboard.json\n"
            "Record control selections with multi-agent-brief controls select. Selection is not execution; explicitly run the selected CLI/subagent/human action after selection and approval.\n\n"
            f"{DECISION_RECORDING_NOTE}\n\n"
            f"{FINALIZE_GATE_NOTE}\n\n"
            f"{REPAIR_GUIDANCE_NOTE}\n\n"
            "Run each step in order, verifying each artifact before continuing:\n\n"
            f"1. multi-agent-brief doctor --config {ws_path}/config.yaml\n"
            f"2. multi-agent-brief sources decide --config {ws_path}/config.yaml  (if configured)\n"
            "   If runtime WebSearch reports `Did 0 searches`, or every query returns an empty result set, stop and request human review. Do not switch to source-planner or continue with stale sources.\n"
            f"3. multi-agent-brief inputs extract --config {ws_path}/config.yaml  (if PDF/DOCX/image inputs exist)\n"
            f"4. multi-agent-brief inputs classify --config {ws_path}/config.yaml\n"
            "5. Use the 'scout' subagent. Runtime may split Scout work internally, but chunk outputs are scratch only; write the deterministic joined output/intermediate/candidate_claims.json once. Default topology: also write output/intermediate/screened_candidates.json from the joined candidate universe, then stage-complete scout. Strict topology: write only candidate_claims.json.\n"
            "6. Strict topology only: use the 'screener' subagent to write output/intermediate/screened_candidates.json.\n"
            "7. Use the 'claim-ledger' subagent to write output/intermediate/claim_drafts.json, then run "
            f"multi-agent-brief state freeze-claim-ledger --workspace {ws_path} to create output/intermediate/claim_ledger.json, "
            f"then run multi-agent-brief state stage-complete --workspace {ws_path} --stage claim-ledger --reason \"Claim Ledger was frozen from claim drafts.\"\n"
            "8. Use the 'analyst' subagent to read frozen output/intermediate/claim_ledger.json only, never claim_drafts.json, and write output/intermediate/audited_brief.md as a working draft; analyst stage-complete freezes output/intermediate/analyst_draft_snapshot.md\n"
            "9. Use the 'editor' / Delivery Editor subagent to own and polish the final output/intermediate/audited_brief.md without adding facts\n"
            "10. Use the 'auditor' subagent to audit against frozen output/intermediate/claim_ledger.json, including overstatement and support-strength calibration, and write output/intermediate/audit_report.json\n"
            f"11. multi-agent-brief gates check --workspace {ws_path} --stage auditor\n"
            f"12. multi-agent-brief state check --workspace {ws_path} --strict\n"
            f"13. multi-agent-brief state stage-complete --workspace {ws_path} --stage auditor --reason \"Audit and quality gates passed.\"\n"
            f"14. multi-agent-brief finalize --config {ws_path}/config.yaml\n"
            f"15. multi-agent-brief gates check --workspace {ws_path} --stage finalize --brief {ws_path}/output/brief.md\n"
            f"16. multi-agent-brief state finalize-complete --workspace {ws_path} --reason \"Reader artifacts finalized and clean.\""
        ),
        expected_artifacts=list(EXPECTED_WORKFLOW_ARTIFACTS),
        notes=[
            "Each subagent step must complete before the next begins.",
            "Verify each artifact exists and is non-empty before continuing.",
            "Use inputs extract to convert PDF/DOCX/image inputs into .mineru.md before Scout reads evidence files.",
            "Directory role still controls claim eligibility: only extracted files under input/sources are evidence.",
            REPAIR_GUIDANCE_NOTE,
            DECISION_RECORDING_NOTE,
            FINALIZE_GATE_NOTE,
            "The 'auditor' step and required gates check must run before finalize.",
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
    recipe: str = RUNTIME_RECIPE_FULL,
    venv: str | None = None,
    run_doctor: bool = True,
) -> AgentHandoff:
    ws = Path(workspace).resolve()
    repo = resolve_repo_workdir(repo_workdir, workspace=ws)
    venv_activate = venv or _find_venv_activate(repo)

    # resolve auto -> hermes in v0.5.5
    resolved = RUNTIME_RESOLVED.get(runtime, runtime)

    if resolved not in _HANDOFF_BUILDERS:
        raise ValueError(f"Unknown runtime '{runtime}'. Valid: {', '.join(VALID_RUNTIMES)}")
    if recipe not in VALID_RUNTIME_RECIPES:
        raise ValueError(f"Unknown runtime recipe '{recipe}'. Valid: {', '.join(VALID_RUNTIME_RECIPES)}")

    builder = _HANDOFF_BUILDERS[resolved]
    handoff = builder(ws, repo, venv_activate)
    handoff.recipe = recipe
    if recipe == RUNTIME_RECIPE_FAST_RERUN:
        _apply_fast_rerun_recipe(handoff, ws)

    handoff.stage_completion_protocol = _build_stage_completion_protocol(repo)
    protocol_text = _render_stage_completion_protocol_prompt(handoff.stage_completion_protocol)
    handoff.prompt = f"{handoff.prompt}\n\n{protocol_text}"

    if run_doctor:
        rc, status = _run_doctor(ws)
        handoff.doctor_status = status
        if status == "passed":
            handoff.notes.insert(0, f"Doctor: passed")

    handoff.notes.append(RUNTIME_WEBSEARCH_ZERO_RESULT_NOTE)
    handoff.notes.append(
        "Feedback loop controls are optional: feedback_issues.json and repair_plan.json are created only by multi-agent-brief feedback ingest/plan/resolve."
    )
    handoff.notes.append(
        "output/intermediate/gates/auditor_quality_gate_report.json and output/intermediate/gates/finalize_quality_gate_report.json are created only by multi-agent-brief gates check; output/intermediate/quality_gate_report.json is a legacy/latest projection, not an authoritative completion artifact."
    )
    handoff.notes.append(
        "Provenance projection is optional: provenance_graph.json is created only by multi-agent-brief provenance build and is an audit/debug view, not semantic proof."
    )
    handoff.notes.append(
        "Audience memory is runtime context: audience_profile_snapshot.md is frozen per run and exposed through handoff; it is not source evidence or an artifact gate."
    )
    handoff.notes.append(
        "Control switchboard is runtime control context: orchestrator_control_switchboard.json recommends controls and control_selections.json records Orchestrator selections; selection is not execution."
    )
    handoff.notes.append(
        "Stage completion protocol is embedded in agent_handoff.json/agent_handoff.md; do not depend on any sidecar REFERENCE_RUN_ORCHESTRATOR_PROTOCOL.md file."
    )

    return handoff


def _build_stage_completion_protocol(repo: Path) -> dict[str, Any]:
    stages = load_stage_specs(repo)
    artifacts = load_artifact_contracts(repo)
    artifact_by_id = {str(item.get("artifact_id")): item for item in artifacts if item.get("artifact_id")}
    stage_protocol: list[dict[str, Any]] = []

    for stage in stages:
        stage_id = str(stage.get("stage_id") or "")
        consumes = [str(item) for item in (stage.get("consumes") or [])]
        expected = [str(item) for item in (stage.get("expected_artifacts") or [])]
        required_inputs: list[dict[str, Any]] = []
        context_inputs: list[str] = []
        required_outputs: list[dict[str, Any]] = []

        for item in consumes:
            artifact = artifact_by_id.get(item)
            if artifact:
                required_inputs.append(_protocol_artifact_ref(item, artifact))
            else:
                context_inputs.append(item)

        for item in expected:
            artifact = artifact_by_id.get(item)
            if artifact:
                required_outputs.append(_protocol_artifact_ref(item, artifact))
            else:
                required_outputs.append({
                    "artifact_id": item,
                    "path": item,
                    "required": True,
                    "format": "",
                })

        topology_satisfaction = _protocol_topology_satisfaction(
            stage.get("topology_satisfaction") or {},
            artifact_by_id,
        )
        freeze_input_artifacts: list[dict[str, Any]] = []
        pre_completion_transactions: list[dict[str, Any]] = []
        if stage_id == "claim-ledger" and "claim_drafts" in artifact_by_id:
            claim_drafts_ref = _protocol_artifact_ref("claim_drafts", artifact_by_id["claim_drafts"])
            claim_ledger_ref = _protocol_artifact_ref("claim_ledger", artifact_by_id["claim_ledger"])
            freeze_input_artifacts.append(claim_drafts_ref)
            pre_completion_transactions.append({
                "transaction": "freeze_claim_ledger",
                "command": "multi-agent-brief state freeze-claim-ledger --workspace <workspace>",
                "reads": [claim_drafts_ref],
                "writes": [claim_ledger_ref],
                "must_run_before": "state stage-complete --stage claim-ledger",
            })
        parallel_chunk_contract: dict[str, Any] | None = None
        if stage_id == "scout":
            parallel_chunk_contract = {
                "status": "runtime_internal_optional",
                "scratch_only": True,
                "final_joined_artifacts": {
                    "always": ["output/intermediate/candidate_claims.json"],
                    "default_topology": ["output/intermediate/screened_candidates.json"],
                },
                "join_requirement": (
                    "Join chunk outputs deterministically before writing workflow artifacts; "
                    "stable ordering must use source identity, source path or URL, source date, topic, and evidence text."
                ),
                "forbidden": [
                    "append to candidate_claims.json from chunk workers",
                    "treat chunk outputs as workflow artifacts",
                    "drop duplicates silently during chunk join",
                ],
            }
        independent_topologies = [
            topology
            for topology in _ordered_role_topologies()
            if topology not in topology_satisfaction
        ] if topology_satisfaction else []
        stage_protocol.append({
            "stage_id": stage_id,
            "owner": str(stage.get("owner") or ""),
            "category": str(stage.get("category") or ""),
            "required_input_artifacts": required_inputs,
            "context_inputs": context_inputs,
            "required_output_artifacts": required_outputs,
            "freeze_input_artifacts": freeze_input_artifacts,
            "pre_completion_transactions": pre_completion_transactions,
            "parallel_chunk_contract": parallel_chunk_contract,
            "topology_satisfaction": topology_satisfaction,
            "independent_completion_topologies": independent_topologies,
            "allowed_decisions": [str(item) for item in (stage.get("allowed_decisions") or [])],
            "forbidden_actions": list(DEFAULT_STAGE_FORBIDDEN_ACTIONS),
            "completion_condition": (
                "The stage is complete only when every required output artifact exists at the declared path, "
                "passes available shape/schema validation, and the Orchestrator can cite those artifact paths."
            ),
        })

    return {
        "schema_version": STAGE_COMPLETION_PROTOCOL_SCHEMA,
        "status": "canonical_handoff_protocol",
        "rules": list(STAGE_COMPLETION_PROTOCOL_RULES),
        "stages": stage_protocol,
    }


def _ordered_role_topologies() -> list[str]:
    preferred = ["default", "strict", "human_assisted"]
    return [
        item for item in preferred if item in ROLE_TOPOLOGY_VALUES
    ] + sorted(str(item) for item in ROLE_TOPOLOGY_VALUES if item not in preferred)


def _protocol_topology_satisfaction(
    rules: dict[str, Any],
    artifact_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for topology, rule in sorted(rules.items()):
        if not isinstance(rule, dict):
            continue
        required_artifacts: list[dict[str, Any]] = []
        for artifact_id in rule.get("required_artifacts") or []:
            artifact_key = str(artifact_id)
            artifact = artifact_by_id.get(artifact_key)
            if artifact:
                required_artifacts.append(_protocol_artifact_ref(artifact_key, artifact))
            else:
                required_artifacts.append({
                    "artifact_id": artifact_key,
                    "path": artifact_key,
                    "required": True,
                    "format": "",
                })
        result[str(topology)] = {
            "satisfied_by": str(rule.get("satisfied_by") or ""),
            "required_artifacts": required_artifacts,
        }
    return result


def _protocol_artifact_ref(artifact_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "path": str(artifact.get("path") or ""),
        "required": bool(artifact.get("required", False)),
        "format": str(artifact.get("format") or ""),
    }


def _render_stage_completion_protocol_prompt(protocol: dict[str, Any]) -> str:
    lines = [
        "Stage completion protocol:",
        "- This protocol is embedded in the handoff; do not rely on a sidecar REFERENCE_RUN_ORCHESTRATOR_PROTOCOL.md file.",
    ]
    for rule in protocol.get("rules") or []:
        lines.append(f"- {rule}")
    lines.extend(["", "Per-stage artifact proof requirements:"])
    for stage in protocol.get("stages") or []:
        stage_id = stage.get("stage_id")
        inputs = _protocol_paths(stage.get("required_input_artifacts") or [])
        outputs = _protocol_paths(stage.get("required_output_artifacts") or [])
        context = ", ".join(stage.get("context_inputs") or []) or "none"
        topology_satisfaction = stage.get("topology_satisfaction") or {}
        independent_topologies = ", ".join(stage.get("independent_completion_topologies") or []) or "none"
        lines.append(f"- {stage_id}:")
        lines.append(f"  required input artifacts: {inputs}")
        lines.append(f"  context inputs: {context}")
        if topology_satisfaction:
            lines.append(f"  topology satisfaction: {_render_topology_satisfaction(topology_satisfaction)}")
            lines.append(f"  independent completion topologies: {independent_topologies}")
            lines.append(f"  independent MUST produce ({independent_topologies}): {outputs}")
        else:
            lines.append(f"  MUST produce: {outputs}")
        chunk_contract = stage.get("parallel_chunk_contract")
        if isinstance(chunk_contract, dict):
            lines.append(
                "  optional chunk parallelism: runtime-internal scratch only; "
                f"{chunk_contract.get('join_requirement')}"
            )
            forbidden = ", ".join(str(item) for item in (chunk_contract.get("forbidden") or []))
            if forbidden:
                lines.append(f"  chunk forbidden: {forbidden}")
        freeze_inputs = _protocol_paths(stage.get("freeze_input_artifacts") or [])
        if freeze_inputs != "none":
            lines.append(f"  role MUST produce freeze input: {freeze_inputs}")
        for transaction in stage.get("pre_completion_transactions") or []:
            reads = _protocol_paths(transaction.get("reads") or [])
            writes = _protocol_paths(transaction.get("writes") or [])
            lines.append(
                "  control transaction before stage-complete: "
                f"{transaction.get('command')} (reads {reads}; writes {writes})"
            )
        lines.append("  forbidden: no prose-only completion, no upstream mutation, no invented evidence, no skipped completion transaction.")
    return "\n".join(lines)


def _render_topology_satisfaction(rules: dict[str, Any]) -> str:
    parts: list[str] = []
    for topology in _ordered_role_topologies():
        rule = rules.get(topology)
        if not isinstance(rule, dict):
            continue
        artifacts = _protocol_paths(rule.get("required_artifacts") or [])
        parts.append(
            f"{topology}: satisfied by {rule.get('satisfied_by') or 'unknown'} "
            f"when {artifacts} exist"
        )
    return "; ".join(parts) if parts else "none"


def _protocol_paths(items: list[dict[str, Any]]) -> str:
    paths = [
        f"{item.get('artifact_id')} at {item.get('path')}"
        for item in items
        if item.get("artifact_id")
    ]
    return ", ".join(paths) if paths else "none"


def _apply_fast_rerun_recipe(handoff: AgentHandoff, workspace: Path) -> None:
    summary = require_fast_rerun_handoff_ready(workspace)
    imported_stages = ", ".join(
        f"{stage.get('stage_id')}={stage.get('display_status')}"
        for stage in summary.get("imported_stages") or []
    )
    freshness = summary.get("freshness_at_import") if isinstance(summary.get("freshness_at_import"), dict) else {}
    freshness_status = freshness.get("status") or "unknown"
    guidance = [
        "Runtime recipe: fast-rerun.",
        "same frozen evidence, new writing -- verified by hash.",
        f"Source run: {summary.get('source_run_id')}",
        f"Imported fact-layer hash: {summary.get('fact_layer_sha256')}",
        f"Source archive manifest hash: {summary.get('source_archive_manifest_sha256')}",
        f"Imported files: {summary.get('imported_file_count')}",
        f"Freshness at import: {freshness_status}; target delivery freshness is checked again at finalize-complete.",
        f"Imported-satisfied upstream stages: {imported_stages}.",
        "Start model-backed content work at Analyst.",
        "Do not regenerate source-discovery, input-governance, Scout, Screener, or Claim Ledger.",
        "Do not synthesize or backfill upstream stage-complete, decision_recorded, "
        "or stage_status_changed events for imported stages.",
        "Do not add facts outside the imported Claim Ledger.",
        "Continue through Analyst, Editor, Auditor, gates, finalize, finalize-complete, human delivery, and archive.",
        "Timing comparability is downstream_only: upstream fact-layer stages were "
        "satisfied by import and must not be compared directly with full runs.",
        "If runtime_manifest.fact_layer_import is missing or invalid, stop and run "
        "`multi-agent-brief state import-fact-layer` first; do not silently fall back to a full run.",
        "This recipe is Experimental/internal in v0.8.1 and is not quality-equivalent to a full workflow.",
    ]
    text = "\n".join(guidance)
    handoff.prompt = f"{handoff.prompt}\n\n{text}"
    handoff.notes.append(text)
    handoff.next_steps = (
        "Use this fast-rerun handoff only after the imported fact layer is valid. "
        "Start at Analyst; do not rerun imported upstream fact-layer stages."
    )


def render_handoff_cli(handoff: AgentHandoff) -> str:
    lines = [
        "=" * 60,
        f"  Runtime: {handoff.runtime}",
        f"  Recipe: {handoff.recipe}",
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
        f"- Recipe: {handoff.recipe}",
        f"- Workspace: {handoff.workspace}",
        f"- Repository: {handoff.repo_workdir}",
        f"- Venv activate: {handoff.venv_activate}",
        f"- Doctor: {handoff.doctor_status}",
        "",
        "## Next Steps",
        "",
        handoff.next_steps,
        "",
        "## Contract References",
        "",
    ]
    for label, rel_path in handoff.contract_references.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Runtime State Files",
        "",
    ])
    for label, rel_path in handoff.runtime_state_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Audience Memory Files",
        "",
    ])
    for label, rel_path in handoff.audience_memory_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Improvement Memory Files",
        "",
    ])
    for label, rel_path in handoff.improvement_memory_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Control Switchboard Files",
        "",
    ])
    for label, rel_path in handoff.control_switchboard_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Feedback State Files",
        "",
    ])
    for label, rel_path in handoff.feedback_state_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Quality Gate State Files",
        "",
    ])
    for label, rel_path in handoff.quality_gate_state_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Provenance State Files",
        "",
    ])
    for label, rel_path in handoff.provenance_state_files.items():
        md_content.append(f"- `{label}`: `{rel_path}`")
    md_content.extend([
        "",
        "## Stage Completion Protocol",
        "",
        f"- Schema: `{handoff.stage_completion_protocol.get('schema_version', '')}`",
        f"- Status: `{handoff.stage_completion_protocol.get('status', '')}`",
        "",
        "### Rules",
        "",
    ])
    for rule in handoff.stage_completion_protocol.get("rules") or []:
        md_content.append(f"- {rule}")
    md_content.extend([
        "",
        "### Per-Stage Artifact Proof",
        "",
    ])
    for stage in handoff.stage_completion_protocol.get("stages") or []:
        md_content.extend([
            f"#### `{stage.get('stage_id')}`",
            "",
            f"- Owner: `{stage.get('owner')}`",
            f"- Context inputs: {', '.join(stage.get('context_inputs') or []) or 'none'}",
            f"- Completion condition: {stage.get('completion_condition')}",
            f"- Stage completion transaction: `multi-agent-brief state stage-complete --stage {stage.get('stage_id')} --workspace <workspace> --reason \"<reason>\"`",
            "- Required input artifacts:",
        ])
        inputs = stage.get("required_input_artifacts") or []
        if inputs:
            for item in inputs:
                md_content.append(f"  - `{item.get('artifact_id')}` at `{item.get('path')}`")
        else:
            md_content.append("  - none")
        freeze_inputs = stage.get("freeze_input_artifacts") or []
        if freeze_inputs:
            md_content.append("- Freeze input artifacts:")
            for item in freeze_inputs:
                md_content.append(f"  - `{item.get('artifact_id')}` at `{item.get('path')}`")
        pre_transactions = stage.get("pre_completion_transactions") or []
        if pre_transactions:
            md_content.append("- Pre-completion transactions:")
            for transaction in pre_transactions:
                reads = _protocol_paths(transaction.get("reads") or [])
                writes = _protocol_paths(transaction.get("writes") or [])
                md_content.append(f"  - `{transaction.get('transaction')}`: `{transaction.get('command')}`")
                md_content.append(f"    - Reads: {reads}")
                md_content.append(f"    - Writes: {writes}")
        chunk_contract = stage.get("parallel_chunk_contract")
        if isinstance(chunk_contract, dict):
            md_content.append("- Optional chunk parallelism:")
            md_content.append(
                f"  - Status: `{chunk_contract.get('status')}`; scratch only: `{chunk_contract.get('scratch_only')}`"
            )
            md_content.append(f"  - Join: {chunk_contract.get('join_requirement')}")
            forbidden = chunk_contract.get("forbidden") or []
            if forbidden:
                md_content.append("  - Forbidden:")
                for item in forbidden:
                    md_content.append(f"    - {item}")
        topology_satisfaction = stage.get("topology_satisfaction") or {}
        if topology_satisfaction:
            md_content.append("- Topology satisfaction:")
            for topology in _ordered_role_topologies():
                rule = topology_satisfaction.get(topology)
                if not isinstance(rule, dict):
                    continue
                artifacts = _protocol_paths(rule.get("required_artifacts") or [])
                md_content.append(
                    f"  - `{topology}`: satisfied by `{rule.get('satisfied_by')}` when {artifacts} exist"
                )
            md_content.append(
                "- Independent completion topologies: "
                + (", ".join(stage.get("independent_completion_topologies") or []) or "none")
            )
            md_content.append("- Required output artifacts for independent completion:")
        else:
            md_content.append("- Required output artifacts:")
        outputs = stage.get("required_output_artifacts") or []
        if outputs:
            for item in outputs:
                md_content.append(f"  - `{item.get('artifact_id')}` at `{item.get('path')}`")
        else:
            md_content.append("  - none")
        md_content.append("- Forbidden actions:")
        for action in stage.get("forbidden_actions") or []:
            md_content.append(f"  - {action}")
        md_content.append("")
    md_content.extend([
        "",
        "## Prompt",
        "",
        "```text",
        handoff.prompt,
        "```",
        "",
        "## Expected Artifacts",
        "",
    ])
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
