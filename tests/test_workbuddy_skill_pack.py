from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from multi_agent_brief.workbuddy.skill_pack import (
    EMBEDDED_MANIFEST,
    MANIFEST_SCHEMA_VERSION,
    WorkBuddySkillPackError,
    package_workbuddy_skill,
    validate_workbuddy_skill_pack,
)


ROOT = Path(__file__).resolve().parent.parent
WORKBUDDY_SKILL = ROOT / ".agents" / "skills" / "briefloop-workbuddy"
REPO_OPERATOR_SKILL = ROOT / ".agents" / "skills" / "briefloop" / "SKILL.md"
LEGACY_WORKBUDDY_SKILL = ROOT / "integrations" / "workbuddy" / "briefloop"
WORKBUDDY_ASSISTANT_PROMPT = (
    ROOT / "integrations" / "workbuddy" / "assistant" / "briefloop-assistant-prompt.md"
)
WORKBUDDY_DOCS = (
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
)
WORKBUDDY_SMOKE_CHECKLIST = ROOT / "docs" / "workbuddy-smoke-checklist.md"
CODEBUDDY_AGENT_ROOT = ROOT / ".codebuddy" / "agents"
CODEBUDDY_SKILL = ROOT / ".codebuddy" / "skills" / "briefloop" / "SKILL.md"
CODEBUDDY_ROLE_AGENTS = {
    "briefloop-scout.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": [
            "output/intermediate/candidate_claims.json",
            "output/intermediate/screened_candidates.json",
        ],
    },
    "briefloop-screener.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": ["output/intermediate/screened_candidates.json"],
    },
    "briefloop-claim-ledger.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": ["output/intermediate/claim_drafts.json"],
    },
    "briefloop-analyst.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": ["output/intermediate/audited_brief.md"],
    },
    "briefloop-editor.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": ["output/intermediate/audited_brief.md"],
    },
    "briefloop-auditor.md": {
        "tools": "tools: Read, Write, Grep, Glob",
        "outputs": ["output/intermediate/audit_report.json"],
    },
    "briefloop-formatter.md": {
        "tools": "tools: Read, Grep, Glob",
        "outputs": ["finalize readiness"],
    },
}
REFERENCE_NAMES = {
    "quickstart.md",
    "workspace-workflow.md",
    "artifact-boundary.md",
    "status-and-gates.md",
    "repair-protocol.md",
    "workbuddy-safety.md",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_skill_text() -> str:
    return "\n".join(_read(path) for path in sorted(WORKBUDDY_SKILL.rglob("*.md")))


def _all_legacy_workbuddy_text() -> str:
    return "\n".join(_read(path) for path in sorted(LEGACY_WORKBUDDY_SKILL.rglob("*.md")))


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _compact_without_code_ticks(text: str) -> str:
    return _compact(text.replace("`", ""))


def test_workbuddy_skill_bundle_has_required_files() -> None:
    assert (WORKBUDDY_SKILL / "SKILL.md").exists()
    for name in REFERENCE_NAMES:
        assert (WORKBUDDY_SKILL / "references" / name).exists(), name


def test_codebuddy_role_agents_are_project_level_assets() -> None:
    for filename, spec in CODEBUDDY_ROLE_AGENTS.items():
        path = CODEBUDDY_AGENT_ROOT / filename
        text = _read(path)
        compact = _compact(text)
        role_name = filename.removesuffix(".md")

        assert path.exists(), filename
        assert f"name: {role_name}" in text
        assert "Use only when the main CodeBuddy session explicitly delegates" in text
        assert "MUST BE USED" not in text
        assert spec["tools"] in text
        assert "Bash" not in text
        assert "model: inherit" in text
        assert "permissionMode: default" in text
        assert "context: fork" not in text
        assert "CodeBuddy project sub-agent" in text
        assert "agent_handoff.md" in text
        assert "agent_handoff.json" in text
        assert "CodeBuddy sub-agents cannot spawn other sub-agents" in compact
        assert "stop without writing" in text
        assert "Do not run `briefloop` or `multi-agent-brief` CLI commands" in text
        assert "ask the main CodeBuddy session to run deterministic validation" in compact
        for output in spec["outputs"]:
            assert output in text


def test_codebuddy_skill_adapter_is_main_session_orchestrator() -> None:
    text = _read(CODEBUDDY_SKILL)
    compact = _compact(text)
    frontmatter = text.split("---", 2)[1]

    assert "name: briefloop" in text
    assert "main CodeBuddy session" in text
    assert "not a forked" in text
    assert "context:" not in frontmatter
    assert "Do not add `context: fork`" in text
    assert ".agents/skills/briefloop-workbuddy/SKILL.md" in text
    assert ".agents/skills/briefloop-workbuddy/references/" in text
    assert ".codebuddy/skills/briefloop/SKILL.md" in text
    assert "Do not perform Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or" in text
    for role_name in [
        "briefloop-scout",
        "briefloop-screener",
        "briefloop-claim-ledger",
        "briefloop-analyst",
        "briefloop-editor",
        "briefloop-auditor",
        "briefloop-formatter",
    ]:
        assert role_name in text
        assert f".codebuddy/agents/{role_name}.md" in text
    for phrase in [
        "Role sub-agents may draft only handoff-assigned role artifacts",
        "They must not run `briefloop` or `multi-agent-brief` CLI commands",
        "The main CodeBuddy session owns deterministic CLI transactions",
        "Before every role delegation and after every deterministic CLI transaction",
        "Do not let a role sub-agent spawn another sub-agent",
    ]:
        assert phrase in compact


def test_codebuddy_role_agents_refuse_control_authority() -> None:
    forbidden_targets = [
        "workflow_state.json",
        "artifact_registry.json",
        "runtime_manifest.json",
        "event_log.jsonl",
        "gate reports",
        "release reports",
        "frozen artifacts",
    ]
    for path in sorted(CODEBUDDY_AGENT_ROOT.glob("briefloop-*.md")):
        text = _read(path)
        assert "You must not edit Python-owned control files" in text
        assert "Forbidden edits:" in text
        for target in forbidden_targets:
            assert target in text
        assert "Bash" not in text
        if path.name != "briefloop-formatter.md":
            assert "briefloop finalize" not in text
            assert "briefloop deliver" not in text
        assert "delivery approval" not in text.lower()
        assert "release authority" not in text.lower()


def test_codebuddy_formatter_is_read_only_readiness_reporter() -> None:
    text = _read(CODEBUDDY_AGENT_ROOT / "briefloop-formatter.md")
    assert "tools: Read, Grep, Glob" in text
    assert "Write" not in text
    assert "Bash" not in text
    assert "You must not write any files" in text
    assert "Return a concise readiness summary only" in text
    for phrase in [
        "Do not run `briefloop finalize`",
        "`briefloop deliver`",
        "gate commands",
        "stage-complete commands",
        "release commands",
    ]:
        assert phrase in text


def test_codebuddy_claim_ledger_never_emits_claim_ids() -> None:
    text = _read(CODEBUDDY_AGENT_ROOT / "briefloop-claim-ledger.md")
    assert "Never\nemit `claim_id` in `claim_drafts.json`" in text
    assert "including nested metadata fields" in text
    assert "let the deterministic freeze transaction assign\nauthoritative Claim Ledger IDs" in text


def test_workbuddy_skill_is_exposed_at_agent_skill_root() -> None:
    assert WORKBUDDY_SKILL.exists()
    assert LEGACY_WORKBUDDY_SKILL.exists()
    assert WORKBUDDY_SKILL.relative_to(ROOT).as_posix() == ".agents/skills/briefloop-workbuddy"
    assert LEGACY_WORKBUDDY_SKILL.relative_to(ROOT).as_posix() == "integrations/workbuddy/briefloop"
    text = _read(WORKBUDDY_SKILL / "SKILL.md")
    assert "BriefLoop WorkBuddy Skill" in text
    assert "WorkBuddy-facing adapter" in text
    assert "## First Checks" in text


def test_repo_operator_skill_redirects_workbuddy_users() -> None:
    text = _read(REPO_OPERATOR_SKILL)
    compact = _compact(text)
    assert "not the WorkBuddy first-user adapter" in text
    assert ".agents/skills/briefloop-workbuddy/" in text
    assert "briefloop workbuddy pack-skill" in text
    assert "Do not point WorkBuddy users at this repo operator protocol skill" in compact


def test_workbuddy_skill_references_are_linked_from_entrypoint() -> None:
    text = _read(WORKBUDDY_SKILL / "SKILL.md")
    references = set(re.findall(r"references/[a-z0-9-]+\.md", text))
    assert references == {f"references/{name}" for name in REFERENCE_NAMES}


def test_workbuddy_skill_has_natural_language_triggers() -> None:
    text = _read(WORKBUDDY_SKILL / "SKILL.md")
    assert "triggers:" not in text
    for phrase in [
        "跑周报",
        "生成行业简报",
        "运行简报",
        "帮我做市场简报",
    ]:
        assert phrase in text


def test_workbuddy_skill_uses_codebuddy_role_agent_runtime_not_operator_default() -> None:
    text = _all_skill_text()
    assert "briefloop run --workspace <workspace> --runtime codebuddy" in text
    assert "CodeBuddy-compatible role subagent" in text
    assert "Use `--runtime codebuddy` only when the source checkout contains" in text
    assert "The local WorkBuddy Skill zip alone does\nnot install those CodeBuddy project assets" in text
    assert "briefloop-scout" in text
    assert "briefloop-auditor" in text
    assert "do not\nfall back to hand-authoring BriefLoop JSON artifacts" in text
    assert "silently switch\nto `--runtime operator`" in text
    assert "run --workspace <workspace> --runtime operator" not in text
    assert "Use `--runtime operator`" not in text
    assert "use `--runtime operator` for handoff" not in text
    assert "--runtime manual" not in text
    assert "legacy manual" not in text.lower()


def test_workbuddy_skill_includes_required_cli_surface() -> None:
    text = _all_skill_text()
    compact = _compact(text)
    normalized = _compact_without_code_ticks(text)
    for phrase in [
        'BRIEFLOOP_CLI="$(command -v briefloop)"',
        'test -n "$BRIEFLOOP_CLI"',
        '"$BRIEFLOOP_CLI" version',
        "command -v briefloop",
        "briefloop new industry-weekly <workspace> --search-backend tavily",
        "briefloop new management-monthly <workspace> --search-backend tavily",
        "briefloop new document-review <workspace> --search-backend tavily",
        "briefloop new solar-periodic <workspace> --search-backend tavily",
        "briefloop new industry-weekly <workspace> --web-search-mode disabled",
        "briefloop new management-monthly <workspace> --web-search-mode disabled",
        "briefloop new document-review <workspace> --web-search-mode disabled",
        "briefloop new solar-periodic <workspace> --web-search-mode disabled",
        "briefloop run --workspace <workspace> --runtime codebuddy",
        "multi-agent-brief status --workspace <workspace>",
        "multi-agent-brief state check --workspace <workspace>",
        "multi-agent-brief quality summarize --workspace <workspace>",
        "multi-agent-brief gates show --workspace <workspace> --json",
        "multi-agent-brief repair route --workspace <workspace> --json",
        "--gate-stage",
        "--gate-artifact",
        "--finding-id <finding_id>",
        "--route-index <route_index>",
        "multi-agent-brief repair complete --workspace <workspace> --reason",
    ]:
        assert phrase in text
    assert "do not use unscoped repair start for current-gate blockers" in compact
    assert "do not use bare repair start --workspace <workspace>" in normalized.lower()
    assert _bare_repair_start_offenders(text) == []


def test_workbuddy_repair_reference_documents_supersede_lane() -> None:
    for text in (_all_skill_text(), _all_legacy_workbuddy_text()):
        compact = _compact(text)
        assert "multi-agent-brief repair supersede-stage --workspace <workspace>" in text
        assert "old registered hash, current bytes hash, and reason" in compact
        assert "original contamination event" in compact
        assert "does not make the run clean or reference-eligible" in compact
        assert "stop_human_review_or_supersede" in compact


def _bare_repair_start_offenders(text: str) -> list[str]:
    bare_start = re.compile(
        r"multi-agent-brief\s+repair\s+start\s+--workspace\s+<workspace>"
        r"(?![^\n`]*(?:--gate-stage|--finding-id|--route-index))"
    )
    return [line for line in text.splitlines() if bare_start.search(line) and "Do not use bare" not in line]


def test_legacy_workbuddy_mirror_uses_scoped_repair_contract() -> None:
    text = _all_legacy_workbuddy_text()
    compact = _compact(text)
    normalized = _compact_without_code_ticks(text)

    assert "multi-agent-brief gates show --workspace <workspace> --json" in text
    assert "multi-agent-brief repair route --workspace <workspace> --json" in text
    assert "--gate-stage" in text
    assert "--gate-artifact" in text
    assert "--finding-id <finding_id>" in text
    assert "--route-index <route_index>" in text
    assert "do not use unscoped repair start for current-gate blockers" in compact
    assert "do not use bare repair start --workspace <workspace>" in normalized.lower()
    assert _bare_repair_start_offenders(text) == []


def test_workbuddy_skill_preserves_control_boundaries() -> None:
    text = _all_skill_text()
    for control_file in [
        "workflow_state.json",
        "artifact_registry.json",
        "runtime_manifest.json",
        "event_log.jsonl",
    ]:
        assert control_file in text
    for phrase in [
        "Do not directly edit",
        "must not hand-edit control files",
        "Before each stage or role-owned artifact action",
        "re-read the relevant handoff step before continuing",
        "Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or\nFormatter subagents ran",
        "follow the generated handoff literally",
        "Role subagents draft only handoff-assigned artifacts",
        "not semantic proof",
        "not gates, release approval, or\ndelivery approval",
    ]:
        assert phrase in text


def test_workbuddy_skill_hardens_first_use_routing_and_progress_feedback() -> None:
    text = _all_skill_text()
    compact = _compact(text)
    for phrase in [
        'If no workspace path is provided, do not ask only "where is the workspace?"',
        "existing workspace: ask for the folder path",
        "first-time run: offer to create one",
    ]:
        assert phrase in text
    for phrase in [
        "a BriefLoop workspace is the local folder for this report project",
        "ask for explicit confirmation of the target path",
        "Suggest a safe local folder outside the BriefLoop source checkout",
        "`C:\\Users\\<User>\\Documents\\BriefLoop\\workspaces\\<topic-slug>`",
        "Suggest only; do not create the folder or workspace silently",
        "周报, 行业, or 竞品 -> `industry-weekly`",
        "管理月报, or 月报 -> `management-monthly`",
        "文件, PDF, or 审阅 -> `document-review`",
    ]:
        assert phrase in compact


def test_workbuddy_skill_requires_stage_handoff_reread_and_deterministic_progress() -> None:
    text = _all_skill_text()
    compact = _compact(text)
    for phrase in [
        "Before each stage or role-owned artifact action",
        "`agent_handoff.md` / `agent_handoff.json` step",
        "After each deterministic CLI transaction, summarize progress to the user",
        "已创建工作区。",
        "已生成 CodeBuddy handoff。",
        "当前状态：等待 source/scout artifact。",
        "Quality Panel 已生成。",
    ]:
        assert phrase in text
    for phrase in [
        "visible in `status`, `workflow_state.json`, `event_log.jsonl`, or generated artifacts",
        "Do not say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching",
        "Preserve command names, artifact names, and handoff obligations exactly",
    ]:
        assert phrase in compact


def test_workbuddy_skill_requires_run_card_and_hard_stop_rules() -> None:
    text = _all_skill_text()
    compact = _compact(text)
    for field in [
        "runtime:",
        "current_stage:",
        "run_integrity:",
        "blocked:",
        "latest_gate_status:",
        "finalize_report:",
        "delivery_truth:",
        "next_allowed_action:",
    ]:
        assert field in text
    for phrase in [
        "After every key CLI command, role return, repair action, gate check, finalize",
        "`briefloop doctor` reports any error",
        "Show the full doctor output",
        "`run_integrity` is `contaminated`, `stale_or_invalid`, or unknown",
        "Do not run finalize or delivery",
        "`delivery_truth.valid` is not",
        "Say the run has a draft only when",
        "Any export, share, package, zip, or attachment candidate contains",
    ]:
        assert phrase in text
    for phrase in [
        "Do not turn normal pre-finalize state into a workflow stop",
        "may be delivered, but it remains permanently non-reference-eligible",
        "delivery_truth.eligibility.allowed=true",
        "stop finalize, delivery, export, and share actions",
        "For early-stage draft work, report the Run Card and continue only with non-delivery workflow steps allowed by the handoff",
        "otherwise say no draft or delivery exists yet",
        "This is normal before finalize and must not block earlier handoff-assigned stages by itself",
        "Do not say \"delivered\" unless `briefloop workbuddy diagnose --json` reports `delivery_truth.valid=true`",
        "Do not zip or share the whole workspace. Use BriefLoop-generated delivery or audit bundles when present; never include `.env`",
        "share only manually reviewed, non-secret excerpts from `briefloop status --json` or doctor output",
        "Never share a whole workspace zip",
        "Do not downgrade the error yourself",
        "recommend rotating any exposed key",
    ]:
        assert phrase in compact
    for phrase in [
        "future support bundles",
        "support-bundle",
        "support package",
    ]:
        assert phrase not in compact


def test_workbuddy_skill_has_no_private_paths_or_overclaim_language() -> None:
    text = _all_skill_text()
    forbidden = [
        "private_planning",
        "local-private-user-home",
        "semantic proof engine",
        "proves truth",
        "eliminates hallucinations",
        "automatic truth checker",
        "ready to send automatically",
        "manual runtime",
        "automatically create",
        "automatic workspace creation",
        "run the full workflow silently",
    ]
    lowered = text.lower()
    for phrase in forbidden:
        assert phrase.lower() not in lowered


def test_workbuddy_skill_declares_source_clone_distribution_boundary() -> None:
    text = _all_skill_text()
    assert "source-clone-only" in text
    assert "wheel/sdist package installs do not include" in text


def test_workbuddy_public_docs_declare_install_and_assistant_boundaries() -> None:
    for path in WORKBUDDY_DOCS:
        text = _read(path)
        compact = _compact(text)
        assert "WorkBuddy Skill source bundle" in text
        assert ".agents/skills/briefloop-workbuddy/" in text
        assert ".codebuddy/skills/briefloop/" in text
        assert ".codebuddy/agents/briefloop-*.md" in text
        assert "Experimental" in text
        assert "source-clone-only" in text.lower()
        assert "briefloop workbuddy pack-skill --output dist/workbuddy" in text
        assert "WorkBuddy Assistant trigger" in text
        assert "--runtime codebuddy" in text
        assert "WorkBuddy role-agent orchestration" in text
        assert "silently fall back to `--runtime operator`" in text or "静默回退到 `--runtime operator`" in text
        assert "semantic proof" in text or "semantic truth" in text or "语义证明" in text
        assert "approve delivery" in text or "不批准交付" in text
        assert "authorize release" in text or "不授权 release" in text
        assert "WorkBuddy Marketplace" in text
        assert "docs/workbuddy-smoke-checklist.md" in text
        assert "not the WorkBuddy first-user adapter" in compact or "不是 WorkBuddy first-user" in compact
        assert "Run Card" in text
        assert "workspace zip" in text or "workspace" in text and ".env" in text
        assert "run_integrity" in text
        assert "finalize_report.json" in text
        assert "delivery_truth.valid" in text


def test_workbuddy_docs_do_not_route_users_to_repo_operator_skill() -> None:
    for path in (*WORKBUDDY_DOCS, WORKBUDDY_SMOKE_CHECKLIST):
        text = _read(path)
        assert ".agents/skills/briefloop-workbuddy/" in text
        assert "open `.agents/skills/briefloop/`" not in text
        assert "use `.agents/skills/briefloop/`" not in text
        assert "install `.agents/skills/briefloop/`" not in text


def test_workbuddy_assistant_prompt_is_trigger_only() -> None:
    text = _read(WORKBUDDY_ASSISTANT_PROMPT)
    for phrase in [
        "remote trigger into a local WorkBuddy session",
        "BriefLoop Skill installed",
        "You are not a BriefLoop runtime",
        "Use `--runtime codebuddy`",
        "briefloop-scout",
        "briefloop-auditor",
        "Do not hand-author control files",
        "Do not finalize, deliver, publish, approve release",
        "Do not say role subagents ran unless WorkBuddy explicitly delegated",
        "Do not treat traceability as semantic proof",
        "Do not claim hallucination elimination, output-quality improvement, or",
    ]:
        assert phrase in text


def test_workbuddy_manual_smoke_checklist_is_non_authoritative() -> None:
    text = _read(WORKBUDDY_SMOKE_CHECKLIST)
    compact = _compact(text)
    for phrase in [
        "WorkBuddy Integration Smoke Checklist",
        "experimental integration smoke",
        "not runtime proof",
        "briefloop workbuddy pack-skill --output dist/workbuddy",
        "briefloop run --workspace <workspace> --runtime codebuddy",
        "briefloop status --workspace <workspace>",
        "briefloop state check --workspace <workspace>",
        "briefloop quality summarize --workspace <workspace>",
        ".codebuddy/skills/briefloop/",
        ".codebuddy/agents/briefloop-*.md",
        "must not auto-deliver",
        "WorkBuddy did not silently fall back to `--runtime operator`",
        "WorkBuddy printed machine-fact Run Cards",
        "WorkBuddy stopped on doctor errors, stopped finalize/delivery/export/share",
        "did not claim delivery when completion projection reported `delivery_truth.valid=false`",
        "WorkBuddy did not share a whole workspace zip",
    ]:
        assert phrase in compact
    for phrase in [
        "not runtime proof, delegated-agent proof, output-quality proof, semantic proof",
        "WorkBuddy did not hand-edit Python-owned control files or frozen artifacts",
        "WorkBuddy did not claim delegated role execution without actual WorkBuddy delegation",
    ]:
        assert phrase in compact


def test_workbuddy_skill_pack_contains_only_public_skill_files(tmp_path: Path) -> None:
    result = package_workbuddy_skill(output_dir=tmp_path, repo_workdir=ROOT)
    assert result.zip_path.exists()
    assert result.manifest_path.exists()
    assert validate_workbuddy_skill_pack(
        zip_path=result.zip_path,
        manifest_path=result.manifest_path,
    ) == []

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["runtime_effect"] == "packaging_only"
    assert manifest["zip_sha256"] == result.zip_sha256
    assert manifest["source_root"] == ".agents/skills/briefloop-workbuddy"
    assert manifest["distribution_boundary"] == (
        "local_workbuddy_skill_zip_not_marketplace_ready_not_python_package_data"
    )
    names = [item["path"] for item in manifest["included_files"]]
    assert f"briefloop/SKILL.md" in names
    assert "briefloop/references/quickstart.md" in names
    assert EMBEDDED_MANIFEST in names
    assert all("private_planning" not in name for name in names)
    assert all("/output/" not in f"/{name}/" for name in names)
    assert all(not Path(name).is_absolute() for name in names)

    with zipfile.ZipFile(result.zip_path) as archive:
        assert sorted(archive.namelist()) == sorted(names)
        skill_text = archive.read("briefloop/SKILL.md").decode("utf-8")
    assert "--runtime codebuddy" in skill_text
    assert "run --workspace <workspace> --runtime operator" not in skill_text
    assert "BriefLoop WorkBuddy Skill" in skill_text
    assert "BriefLoop Operator Protocol" not in skill_text
    assert "semantic proof" in skill_text


def test_workbuddy_skill_pack_is_reproducible(tmp_path: Path) -> None:
    first = package_workbuddy_skill(output_dir=tmp_path / "a", repo_workdir=ROOT)
    second = package_workbuddy_skill(output_dir=tmp_path / "b", repo_workdir=ROOT)
    assert first.zip_sha256 == second.zip_sha256
    assert first.zip_path.read_bytes() == second.zip_path.read_bytes()


def test_workbuddy_pack_skill_cli_json(tmp_path: Path) -> None:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src if not env.get("PYTHONPATH") else f"{src}{os.pathsep}{env['PYTHONPATH']}"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "multi_agent_brief.cli.main",
            "workbuddy",
            "pack-skill",
            "--output",
            str(tmp_path),
            "--repo-workdir",
            str(ROOT),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["runtime_effect"] == "packaging_only"
    assert Path(payload["zip_path"]).exists()
    assert Path(payload["manifest_path"]).exists()


def test_workbuddy_skill_pack_rejects_output_inside_source_tree() -> None:
    output = WORKBUDDY_SKILL / "dist"
    try:
        package_workbuddy_skill(output_dir=output, repo_workdir=ROOT)
    except WorkBuddySkillPackError as exc:
        assert "must not be inside the skill source tree" in str(exc)
    else:  # pragma: no cover - clearer failure than pytest.raises message here
        raise AssertionError("expected output directory rejection")


def test_validate_workbuddy_skill_pack_reports_malformed_included_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "briefloop-workbuddy-skill.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("briefloop/SKILL.md", "ok\n")
    manifest_path = tmp_path / "briefloop-workbuddy-skill.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "runtime_effect": "packaging_only",
                "zip_sha256": "0" * 64,
                "included_files": None,
            }
        ),
        encoding="utf-8",
    )

    errors = validate_workbuddy_skill_pack(zip_path=zip_path, manifest_path=manifest_path)

    assert "manifest included_files must be a list" in errors
    assert "zip sha256 mismatch" in errors


def test_workbuddy_skill_pack_rejects_symlinked_source_file(tmp_path: Path) -> None:
    source = tmp_path / "repo" / ".agents" / "skills" / "briefloop-workbuddy"
    (source / "references").mkdir(parents=True)
    for rel in ["SKILL.md", *REFERENCE_NAMES]:
        target = source / (Path("references") / rel if rel != "SKILL.md" else Path(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok\n", encoding="utf-8")
    (tmp_path / "repo" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("private\n", encoding="utf-8")
    (source / "references" / "local.md").symlink_to(outside)

    try:
        package_workbuddy_skill(output_dir=tmp_path / "out", repo_workdir=tmp_path / "repo")
    except WorkBuddySkillPackError as exc:
        assert "symlink" in str(exc)
    else:  # pragma: no cover - clearer failure than pytest.raises message here
        raise AssertionError("expected symlink rejection")
