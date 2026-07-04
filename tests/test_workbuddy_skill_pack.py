from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKBUDDY_SKILL = ROOT / "integrations" / "workbuddy" / "briefloop"
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
        "re-open the relevant step in\n`output/intermediate/agent_handoff.md`",
        "re-read the relevant handoff step before continuing",
        "Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or\nFormatter subagents ran",
        "follow the English operator handoff literally",
        "not semantic proof",
        "not gates, release approval, or\ndelivery approval",
    ]:
        assert phrase in text


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
    ]
    lowered = text.lower()
    for phrase in forbidden:
        assert phrase.lower() not in lowered


def test_workbuddy_skill_declares_source_clone_distribution_boundary() -> None:
    text = _all_skill_text()
    assert "source-clone-only" in text
    assert "wheel/sdist package installs do not include" in text
