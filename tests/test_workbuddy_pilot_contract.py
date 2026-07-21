from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent

CANONICAL = ROOT / ".agents" / "skills" / "briefloop-workbuddy"
MIRROR = ROOT / "integrations" / "workbuddy" / "briefloop"
CODEBUDDY = ROOT / ".codebuddy" / "skills" / "briefloop" / "SKILL.md"
ASSISTANT = (
    ROOT
    / "integrations"
    / "workbuddy"
    / "assistant"
    / "briefloop-assistant-prompt.md"
)
DOCS = (
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
    ROOT / "docs" / "workbuddy-smoke-checklist.md",
)


def _required_references(skill_root: Path) -> tuple[Path, ...]:
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## Required References", 1)[1].split("\n## ", 1)[0]
    names = tuple(re.findall(r"^- `references/([^`]+)`$", section, re.MULTILINE))
    assert names
    return tuple(skill_root / "references" / name for name in names)


CANONICAL_REFERENCES = _required_references(CANONICAL)
MIRROR_REFERENCES = _required_references(MIRROR)
ACTIVE_INSTRUCTION_SURFACES = (
    CANONICAL / "SKILL.md",
    *CANONICAL_REFERENCES,
    CODEBUDDY,
    MIRROR / "SKILL.md",
    *MIRROR_REFERENCES,
    ASSISTANT,
    *DOCS,
)
ENTRYPOINTS = (
    CANONICAL / "SKILL.md",
    CANONICAL / "references" / "quickstart.md",
    CODEBUDDY,
    MIRROR / "SKILL.md",
    MIRROR / "references" / "quickstart.md",
    *DOCS,
)
SECRET_IMPORT_SURFACES = ENTRYPOINTS
FINALIZE_EVIDENCE_SURFACES = (
    CANONICAL / "SKILL.md",
    CODEBUDDY,
    MIRROR / "SKILL.md",
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
)
ROLE_ACTION_SEQUENCE_SURFACES = (
    CANONICAL / "SKILL.md",
    CANONICAL / "references" / "quickstart.md",
    CODEBUDDY,
    MIRROR / "SKILL.md",
    MIRROR / "references" / "quickstart.md",
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
    ROOT / "docs" / "workbuddy-smoke-checklist.md",
    ASSISTANT,
)
ACTION_SURFACES = ACTIVE_INSTRUCTION_SURFACES
CANONICAL_STATUS_COMMAND = (
    '& $BriefLoop status --workspace "<workspace>" --json'
)
DIAGNOSE_INSTRUCTION_SURFACES = (
    CANONICAL / "SKILL.md",
    CANONICAL / "references" / "quickstart.md",
    CANONICAL / "references" / "workspace-workflow.md",
    CANONICAL / "references" / "status-and-gates.md",
    CANONICAL / "references" / "repair-protocol.md",
    CANONICAL / "references" / "workbuddy-safety.md",
    CANONICAL / "references" / "workbuddy-delegation.md",
    CODEBUDDY,
    MIRROR / "SKILL.md",
    MIRROR / "references" / "quickstart.md",
    MIRROR / "references" / "workspace-workflow.md",
    MIRROR / "references" / "status-and-gates.md",
    MIRROR / "references" / "repair-protocol.md",
    MIRROR / "references" / "workbuddy-delegation.md",
    ASSISTANT,
    ROOT / "docs" / "workbuddy.md",
    ROOT / "docs" / "workbuddy.zh-CN.md",
    ROOT / "docs" / "workbuddy-smoke-checklist.md",
)

EXACT_ROLES = (
    "briefloop-scout",
    "briefloop-screener",
    "briefloop-claim-ledger",
    "briefloop-analyst",
    "briefloop-editor",
    "briefloop-auditor",
    "briefloop-formatter",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(path: Path) -> str:
    return re.sub(r"\s+", " ", _read(path))


def _fenced_blocks(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL))


def test_required_reference_inventory_drives_active_surface_ratchet() -> None:
    expected = (
        "quickstart.md",
        "workspace-workflow.md",
        "artifact-boundary.md",
        "status-and-gates.md",
        "repair-protocol.md",
        "workbuddy-safety.md",
        "workbuddy-delegation.md",
    )
    assert tuple(path.name for path in CANONICAL_REFERENCES) == expected
    assert tuple(path.name for path in MIRROR_REFERENCES) == expected
    assert all(path.is_file() for path in ACTIVE_INSTRUCTION_SURFACES)


def test_windows_entrypoints_bind_one_powershell_cli_identity() -> None:
    required = (
        '$ErrorActionPreference = "Stop"',
        "$BriefLoopCommand = Get-Command",
        "-Name briefloop",
        "-CommandType Application",
        "Select-Object -First 1",
        "$BriefLoop = $BriefLoopCommand.Path",
        "$BriefLoop -notmatch",
        "^(?:[A-Za-z]:\\\\|\\\\\\\\[^\\\\]+\\\\[^\\\\]+\\\\)",
        "& $BriefLoop version",
        "py -3 --version",
        "git --version",
    )
    for path in ENTRYPOINTS:
        text = _read(path)
        for phrase in required:
            assert phrase.lower() in text.lower(), (path, phrase)
        assert (
            "diagnostic only" in text.lower()
            or "只是诊断" in text
            or "只是诊断信息" in text
        ), path

    combined = "\n".join(_read(path) for path in ENTRYPOINTS).lower()
    for forbidden in (
        "bash",
        "which",
        "command -v",
        "export",
        "/c/users/...",
        "source .venv/bin/activate",
        "bash scripts/setup.sh",
    ):
        assert forbidden in combined
    for phrase in (
        "git bash",
        "actual shell",
        "stop",
        "do not guess",
        "does not claim git bash support",
    ):
        assert phrase in combined


@pytest.mark.skipif(os.name != "nt", reason="PowerShell application lookup is Windows-specific")
def test_powershell_application_binding_ignores_function_and_alias_shadowing(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "briefloop.cmd"
    executable.write_text("@echo off\r\n", encoding="utf-8")
    script = "\n".join(
        (
            f'$env:PATH = "{tmp_path};$env:PATH"',
            'function global:briefloop { "function-shadow" }',
            'Set-Alias -Name briefloop -Value Get-Date -Scope Global -Force',
            "$BriefLoopCommand = Get-Command -Name briefloop -CommandType Application "
            "-ErrorAction Stop | Select-Object -First 1",
            "$BriefLoop = $BriefLoopCommand.Path",
            (
                "if ($BriefLoop -notmatch "
                "'^(?:[A-Za-z]:\\\\|\\\\\\\\[^\\\\]+\\\\[^\\\\]+\\\\)') { exit 7 }"
            ),
            "Write-Output $BriefLoop",
        )
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()).resolve() == executable.resolve()


def test_windows_commands_reuse_bound_cli_and_persist_tavily_secret() -> None:
    combined = "\n".join(_read(path) for path in ENTRYPOINTS)
    for phrase in (
        "& $BriefLoop secrets import",
        '--workspace "<workspace>"',
        '$SecretSource = Join-Path $HOME ".briefloop-secrets.env"',
        "Test-Path -LiteralPath $SecretSource -PathType Leaf",
        "--from $SecretSource",
        "--keys TAVILY_API_KEY",
        "--json",
        "& $BriefLoop run",
        "--runtime codebuddy",
        '--repo-workdir "<canonical BriefLoop source checkout>"',
        "& $BriefLoop runtime next",
    ):
        assert phrase in combined

    bare_control_command = re.compile(
        r"(?<![\w$])(?:briefloop|multi-agent-brief)\s+"
        r"(?:workbuddy|new|run|status|state|quality|doctor|secrets|gates|gate|"
        r"repair|finalize|delivery|version|init|onboard|prepare|reset|sources|"
        r"source|archive|freeze|check|audit|render|package|completion|artifact)\b",
    )
    for path in ACTIVE_INSTRUCTION_SURFACES:
        assert not bare_control_command.search(_read(path)), path

    lowered = combined.lower()
    for phrase in (
        "do not temporarily export",
        "do not display or commit",
        "inject a key into one command",
    ):
        assert phrase in lowered


def test_tavily_import_inventory_requires_confirmed_private_file() -> None:
    for path in SECRET_IMPORT_SURFACES:
        text = _read(path)
        compact = _compact(path)
        assert '$SecretSource = Join-Path $HOME ".briefloop-secrets.env"' in text, path
        assert "Test-Path -LiteralPath $SecretSource -PathType Leaf" in compact, path
        assert "--from $SecretSource" in compact, path
        assert '--from "$HOME\\.briefloop-secrets.env"' not in text, path
        assert (
            "only the environment variable exists" in compact
            or "只有环境变量" in compact
        ), path
        assert "auto-copy" in compact or "自动复制" in compact, path


def test_operator_docs_gate_tavily_import_on_online_search_choice() -> None:
    english = _compact(ROOT / "docs" / "workbuddy.md")
    chinese = _compact(ROOT / "docs" / "workbuddy.zh-CN.md")

    assert "If the user enabled Tavily, persist its key" in english
    assert "If the user disabled online search, skip this import" in english
    assert "只有用户已启用 Tavily 时" in chinese
    assert "如果用户已禁用在线搜索，跳过此导入" in chinese


def test_finalize_evidence_inventory_does_not_publish_incomplete_commands() -> None:
    for path in FINALIZE_EVIDENCE_SURFACES:
        compact = _compact(path)
        assert "& $BriefLoop finalize" not in compact, path
        assert "& $BriefLoop state finalize-complete" not in compact, path
        assert (
            "workspace-config-bound finalize transaction succeeded" in compact
            or "workspace-config 绑定的 finalize 事务成功" in compact
        ), path
        assert (
            "current-workspace-bound finalize-complete transaction with a recorded reason succeeded"
            in compact
            or "workspace 绑定、记录了 reason 的 finalize-complete 事务成功" in compact
        ), path


def test_current_workbuddy_status_instruction_inventory_contains_canonical_command(
) -> None:
    assert set(DIAGNOSE_INSTRUCTION_SURFACES) <= set(ACTIVE_INSTRUCTION_SURFACES)
    for path in DIAGNOSE_INSTRUCTION_SURFACES:
        assert CANONICAL_STATUS_COMMAND in _compact(path), path
        compact = _compact(path)
        start = 0
        while True:
            idx = compact.find("workbuddy diagnose", start)
            if idx == -1:
                break
            window = compact[idx : idx + 120]
            assert "retired" in window or "退役" in window, (path, window)
            start = idx + 1


def test_current_action_inventory_invokes_roles_only_when_assigned() -> None:
    for path in ROLE_ACTION_SEQUENCE_SURFACES:
        compact = _compact(path)
        assert (
            "explicitly assigns role-owned draft work" in compact
            or "明确 指派 role-owned draft work" in compact
            or "明确指派 role-owned draft work" in compact
        ), path
        assert "deterministic-only" in compact, path
        assert (
            "invoke no role" in compact
            or "invokes no role" in compact
            or "不调用任何角色" in compact
        ), path
        assert (
            "authorized transaction" in compact or "获授权事务" in compact
        ), path


def test_new_workspace_precedes_secret_import_on_first_use_surfaces() -> None:
    ordered_surfaces = (
        CANONICAL / "SKILL.md",
        CANONICAL / "references" / "quickstart.md",
        CODEBUDDY,
        MIRROR / "SKILL.md",
        MIRROR / "references" / "quickstart.md",
        ROOT / "docs" / "workbuddy.md",
        ROOT / "docs" / "workbuddy.zh-CN.md",
        ROOT / "docs" / "workbuddy-smoke-checklist.md",
    )
    for path in ordered_surfaces:
        text = _read(path)
        assert text.index("& $BriefLoop new") < text.index(
            "& $BriefLoop secrets import"
        ), path
    ordering_contract = "\n".join(_read(path) for path in ordered_surfaces)
    assert "before `& $BriefLoop new`" in ordering_contract
    assert "之前运行" in ordering_contract


def test_every_first_workspace_command_persists_the_search_choice() -> None:
    first_use_surfaces = (
        CANONICAL / "SKILL.md",
        CANONICAL / "references" / "quickstart.md",
        CODEBUDDY,
        MIRROR / "SKILL.md",
        MIRROR / "references" / "quickstart.md",
        ROOT / "docs" / "workbuddy.md",
        ROOT / "docs" / "workbuddy.zh-CN.md",
        ROOT / "docs" / "workbuddy-smoke-checklist.md",
    )
    for path in first_use_surfaces:
        commands = tuple(
            line.strip()
            for line in _read(path).splitlines()
            if line.strip().startswith("& $BriefLoop new ")
        )
        assert commands, path
        assert any("--search-backend tavily" in command for command in commands), path
        assert any("--web-search-mode disabled" in command for command in commands), path
        assert all(
            "--search-backend tavily" in command
            or "--web-search-mode disabled" in command
            for command in commands
        ), (path, commands)


def test_generated_required_commands_only_rebind_the_executable() -> None:
    for path in (
        CANONICAL / "references" / "repair-protocol.md",
        MIRROR / "references" / "repair-protocol.md",
    ):
        text = _read(path).lower()
        for phrase in (
            "required_commands",
            "leading token" if path.parent.parent == MIRROR else "首 token",
            "$briefloop",
            "invoke-expression",
            "cmd /c",
            "path",
            "unknown" if path.parent.parent == MIRROR else "未知",
        ):
            assert phrase in text, (path, phrase)


def test_codebuddy_handoff_and_visible_role_evidence_are_exact() -> None:
    delegation = _read(CANONICAL / "references" / "workbuddy-delegation.md")
    assert delegation == _read(MIRROR / "references" / "workbuddy-delegation.md")
    combined = "\n".join(
        (
            _read(CANONICAL / "SKILL.md"),
            _read(CODEBUDDY),
            delegation,
            *(_read(path) for path in DOCS),
        )
    )
    for phrase in (
        "runtime == codebuddy",
        "runtime_capabilities.runtime == codebuddy",
        "runtime_capabilities.delegation_supported == true",
        "runtime_capabilities.subagent_names",
        "declared handoff capability",
        "host-visible",
    ):
        assert phrase.lower() in combined.lower()
    for role in EXACT_ROLES:
        assert role in combined
    for rejected_label in ("Generic Team", "Expert", "helper", "Send Message"):
        assert rejected_label.lower() in combined.lower()


def test_role_return_never_owns_deterministic_transactions() -> None:
    combined = "\n".join(_read(path) for path in ACTION_SURFACES)
    for phrase in (
        "complete stages",
        "run gates",
        "freeze the Claim Ledger",
        "finalize",
        "approve/report delivery",
        "role return is not a stage pass",
        "main session",
    ):
        assert phrase.lower() in combined.lower()

    for phrase in (
        "read-only finalize-readiness reporter",
        "Markdown to DOCX",
        "reader delivery artifacts",
        "reader-clean",
    ):
        assert phrase.lower() in combined.lower()


def test_role_invocation_stage_and_audit_truth_are_distinct() -> None:
    role_surfaces = (
        CANONICAL / "SKILL.md",
        CANONICAL / "references" / "quickstart.md",
        CANONICAL / "references" / "workspace-workflow.md",
        CANONICAL / "references" / "workbuddy-safety.md",
        CODEBUDDY,
        MIRROR / "SKILL.md",
        MIRROR / "references" / "quickstart.md",
        MIRROR / "references" / "workspace-workflow.md",
        MIRROR / "references" / "workbuddy-safety.md",
        ASSISTANT,
        ROOT / "docs" / "workbuddy.md",
        ROOT / "docs" / "workbuddy.zh-CN.md",
    )
    combined = "\n".join(_read(path) for path in role_surfaces).lower()
    for phrase in (
        "host-visible",
        "exact-role",
        "stage/transaction truth",
        "deterministic verdict/status",
        "stale event",
        "manual file",
    ):
        assert phrase in combined
    assert "unless the matching artifact, event" not in combined


def test_public_run_cards_use_status_projection_delivery_truth() -> None:
    for path in (
        ROOT / "docs" / "workbuddy.md",
        ROOT / "docs" / "workbuddy.zh-CN.md",
        ROOT / "docs" / "workbuddy-smoke-checklist.md",
    ):
        text = _read(path)
        for field in (
            "terminal_state:",
            "package_ready:",
            "delivered:",
            "store_revision:",
            "next_action:",
        ):
            assert field in text, (path, field)
        compact = re.sub(r"\s+", " ", text).lower()
        assert "run_integrity" in compact
        assert "recovery" in compact
        assert "next_action" in compact

def test_doctor_error_cannot_be_human_overridden() -> None:
    combined = "\n".join(_read(path) for path in ACTION_SURFACES).lower()
    for phrase in (
        "request_human_review",
        "user confirmation",
        "standalone pass",
        "cannot override",
        "same `$briefloop`",
        "interruption",
    ):
        assert phrase in combined

    assert "wait for user confirmation" not in combined
    assert "等待用户确认" not in combined


def test_run_integrity_never_routes_completed_non_reference_delivery() -> None:
    checklist = _read(ROOT / "docs" / "workbuddy-smoke-checklist.md").lower()
    for phrase in (
        "run_integrity` never selects",
        "completed_non_reference",
        "package_ready=true",
        "invalid or nonterminal recovery",
    ):
        assert phrase in checklist
    assert "stopped finalize/delivery/export/share on contaminated" not in checklist


def test_handoff_and_diagnose_own_next_action_routing() -> None:
    combined = "\n".join(_read(path) for path in ACTION_SURFACES).lower()
    for phrase in (
        "after every start",
        "role return",
        "interruption",
        "reread",
        "diagnose",
        "current action",
        "audit evidence only",
        "not an action router",
    ):
        assert phrase in combined

    for raw_source in (
        "workflow state",
        "event log",
        "registry",
        "timestamps",
        "file existence",
    ):
        assert raw_source in combined


def test_operator_handoff_cannot_masquerade_as_delegated_execution() -> None:
    combined = "\n".join(_read(path) for path in ACTION_SURFACES).lower()
    for phrase in (
        "operator handoff",
        "user requests subagents",
        "stop using it",
        "regenerate",
        "do not continue operator",
        "never claim",
    ):
        assert phrase in combined


def test_formatter_and_formal_finalize_require_deterministic_truth() -> None:
    authoritative_surfaces = (
        CANONICAL / "SKILL.md",
        CODEBUDDY,
        MIRROR / "SKILL.md",
        ROOT / "docs" / "workbuddy.md",
        ROOT / "docs" / "workbuddy.zh-CN.md",
    )
    for path in authoritative_surfaces:
        text = re.sub(r"\s+", " ", _read(path).lower())
        for phrase in (
            "finalize_report.json",
            "reader-clean",
                "promoted",
                "render_transaction",
                "finalize-complete",
            "package_ready=true",
            "delivered",
            "draft/manual/unverified",
            ):
                assert phrase in text, (path, phrase)
        assert re.search(r"finalize (?:quality[- ]?)?gate", text), path

    combined = "\n".join(_read(path) for path in authoritative_surfaces).lower()
    for residue in ("cl-*", "src-*", "claim ledger", "local path"):
        assert residue in combined
    assert "eligibility" in combined or "只是资格" in combined
    assert "not evidence that delivery occurred" in combined or "不是交付发生证据" in combined


def test_synthetic_manual_docx_incident_cannot_be_relabelled() -> None:
    incident = {
        "handoff_runtime": "operator",
        "visible_exact_role_invocations": (),
        "manual_docx": True,
        "finalize_report": None,
        "finalize_event": None,
    }
    assert incident["handoff_runtime"] == "operator"
    assert incident["manual_docx"] is True
    assert not incident["visible_exact_role_invocations"]
    assert incident["finalize_report"] is None
    assert incident["finalize_event"] is None

    checklist = _read(ROOT / "docs" / "workbuddy-smoke-checklist.md").lower()
    for phrase in (
        "operator handoff",
        "manual docx",
        "no finalize",
        "draft/manual/unverified",
        "must not claim subagents ran",
        "formal finalize pipeline completed",
        "reader-clean passed",
        "delivery completed",
    ):
        assert phrase in checklist


def test_workbuddy_english_and_chinese_docs_share_pilot_boundary() -> None:
    english = _read(ROOT / "docs" / "workbuddy.md").lower()
    chinese = _read(ROOT / "docs" / "workbuddy.zh-CN.md").lower()
    for token in (
        "$briefloop",
        "--runtime codebuddy",
        "--repo-workdir",
        "request_human_review",
        "briefloop-formatter",
        "draft/manual/unverified",
        "finalize_report.json",
        "reader-clean",
        "package_ready=true",
    ):
        assert token in english, token
        assert token in chinese, token
