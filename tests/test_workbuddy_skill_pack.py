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
WORKBUDDY_SKILL = ROOT / "integrations" / "workbuddy" / "briefloop"
WORKBUDDY_ASSISTANT_PROMPT = (
    ROOT / "integrations" / "workbuddy" / "assistant" / "briefloop-assistant-prompt.md"
)
WORKBUDDY_DOCS = (
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
)
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


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_workbuddy_skill_bundle_has_required_files() -> None:
    assert (WORKBUDDY_SKILL / "SKILL.md").exists()
    for name in REFERENCE_NAMES:
        assert (WORKBUDDY_SKILL / "references" / name).exists(), name


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


def test_workbuddy_skill_uses_operator_runtime_not_manual_path() -> None:
    text = _all_skill_text()
    assert "multi-agent-brief run --workspace <workspace> --runtime operator" in text
    assert "--runtime manual" not in text
    assert "legacy manual" not in text.lower()
    assert "host-agnostic compact operator workflow" in text
    assert "does not assume WorkBuddy delegated" in text


def test_workbuddy_skill_includes_required_cli_surface() -> None:
    text = _all_skill_text()
    for phrase in [
        'BRIEFLOOP_CLI="$(command -v briefloop || command -v multi-agent-brief)"',
        'test -n "$BRIEFLOOP_CLI"',
        '"$BRIEFLOOP_CLI" version',
        "command -v briefloop || command -v multi-agent-brief",
        "briefloop new industry-weekly <workspace>",
        "briefloop new management-monthly <workspace>",
        "briefloop new document-review <workspace>",
        "briefloop new solar-periodic <workspace>",
        "multi-agent-brief status --workspace <workspace>",
        "multi-agent-brief state check --workspace <workspace>",
        "multi-agent-brief quality summarize --workspace <workspace>",
        "multi-agent-brief repair route --workspace <workspace>",
        "multi-agent-brief repair start --workspace <workspace>",
        "multi-agent-brief repair complete --workspace <workspace> --reason",
    ]:
        assert phrase in text


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
        "follow the English operator handoff literally",
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
        "Suggest a safe local folder, for example `~/BriefLoop/<topic-slug>`",
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
        "已生成 operator handoff。",
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
        assert "WorkBuddy Skill source bundle" in text
        assert "Experimental" in text
        assert "source-clone-only" in text.lower()
        assert "multi-agent-brief workbuddy pack-skill --output dist/workbuddy" in text
        assert "WorkBuddy Assistant trigger" in text
        assert "--runtime operator" in text
        assert "not a WorkBuddy delegated runtime" in text or "不是 WorkBuddy delegated runtime" in text
        assert "semantic proof" in text or "semantic truth" in text or "语义证明" in text
        assert "approve delivery" in text or "不批准交付" in text
        assert "authorize release" in text or "不授权 release" in text
        assert "WorkBuddy Marketplace" in text


def test_workbuddy_assistant_prompt_is_trigger_only() -> None:
    text = _read(WORKBUDDY_ASSISTANT_PROMPT)
    for phrase in [
        "remote trigger into a local WorkBuddy session",
        "BriefLoop Skill installed",
        "You are not a BriefLoop runtime",
        "Use `--runtime operator`",
        "Do not hand-author control files",
        "Do not finalize, deliver, publish, approve release",
        "Do not say role subagents ran unless WorkBuddy explicitly delegated",
        "Do not treat traceability as semantic proof",
        "Do not claim hallucination elimination, output-quality improvement, or",
    ]:
        assert phrase in text


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
    assert "--runtime operator" in skill_text
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
    source = tmp_path / "repo" / "integrations" / "workbuddy" / "briefloop"
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
