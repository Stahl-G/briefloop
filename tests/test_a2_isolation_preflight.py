from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.cli.main import main
from multi_agent_brief.experiments.a2_isolation import (
    A2ForbiddenPaths,
    preflight_a2_procedural_isolation,
)


def _forbidden(root: Path) -> A2ForbiddenPaths:
    return A2ForbiddenPaths(
        risk_ledger=root / "risk_ledger.json",
        a0_run=root / "runs" / "A0",
        a1_run=root / "runs" / "A1",
        scoring_paths=(root / "scores" / "assessment.json",),
    )


def test_isolated_a2_layout_is_allowed_but_explicitly_not_a_sandbox(
    tmp_path: Path,
) -> None:
    isolated_parent = tmp_path / "isolated"
    project_root = isolated_parent / "a2-project"
    workspace = project_root / "workspace"
    source = project_root / "allowed" / "source-001.md"
    workspace.mkdir(parents=True)
    source.parent.mkdir()
    source.write_text("public frozen input", encoding="utf-8")

    result = preflight_a2_procedural_isolation(
        project_root=project_root,
        workspace=workspace,
        allowed_inputs=(source,),
        forbidden=_forbidden(tmp_path / "confidential-experiment"),
    )

    assert result.ok is True
    assert result.reason_codes == ()
    assert result.to_dict()["isolation_strength"] == "procedural_isolation"
    assert result.to_dict()["os_sandbox_enforced"] is False
    assert result.to_dict()["provider_calls"] == 0
    assert "does not prevent" in str(result.to_dict()["boundary"])


def test_project_root_nested_beside_a0_a1_and_ledger_is_blocked(tmp_path: Path) -> None:
    shared_experiment = tmp_path / "Hackthon"
    project_root = shared_experiment / "A2"
    workspace = project_root / "workspace"
    source = workspace / "input" / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    (shared_experiment / "risk_ledger.json").write_text("{}", encoding="utf-8")
    (shared_experiment / "runs" / "A0").mkdir(parents=True)
    (shared_experiment / "runs" / "A1").mkdir()
    (shared_experiment / "scores").mkdir()

    result = preflight_a2_procedural_isolation(
        project_root=project_root,
        workspace=workspace,
        allowed_inputs=(source,),
        forbidden=_forbidden(shared_experiment),
    )

    assert result.ok is False
    assert result.reason_codes == ("forbidden_path_exposed",)
    assert {finding.subject for finding in result.findings} == {
        "risk_ledger",
        "a0_run",
        "a1_run",
        "scoring_path_1",
    }
    assert {finding.exposed_root for finding in result.findings} == {
        str(shared_experiment)
    }


def test_allowed_input_outside_root_or_through_symlink_fails_closed(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "isolated" / "a2"
    workspace = project_root / "workspace"
    workspace.mkdir(parents=True)
    outside = tmp_path / "source.md"
    outside.write_text("source", encoding="utf-8")
    alias = project_root / "source-alias.md"
    alias.symlink_to(outside)

    result = preflight_a2_procedural_isolation(
        project_root=project_root,
        workspace=workspace,
        allowed_inputs=(outside, alias),
        forbidden=_forbidden(tmp_path / "elsewhere"),
    )

    assert result.ok is False
    assert result.reason_codes == ("allowed_input_outside_project_root",)
    assert [finding.subject for finding in result.findings] == [
        "allowed_input_1",
        "allowed_input_2",
    ]


def test_missing_required_layout_inputs_fail_closed(tmp_path: Path) -> None:
    project_root = tmp_path / "a2"
    project_root.mkdir()
    result = preflight_a2_procedural_isolation(
        project_root=project_root,
        workspace=project_root / "missing-workspace",
        allowed_inputs=(),
        forbidden=A2ForbiddenPaths(
            risk_ledger=tmp_path.parent / "risk_ledger.json",
            a0_run=tmp_path.parent / "A0",
            a1_run=tmp_path.parent / "A1",
            scoring_paths=(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "workspace_missing",
        "allowed_inputs_missing",
        "scoring_paths_declaration_missing",
    )


def test_cli_emits_machine_readable_block_and_never_calls_provider(
    tmp_path: Path, capsys
) -> None:
    shared = tmp_path / "shared"
    workspace = shared / "A2"
    source = workspace / "source.md"
    workspace.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")

    exit_code = main(
        [
            "experiments",
            "a2-isolation-preflight",
            "--project-root",
            str(workspace),
            "--a2-workspace",
            str(workspace),
            "--allowed-input",
            str(source),
            "--risk-ledger",
            str(shared / "risk_ledger.json"),
            "--a0-run",
            str(shared / "A0"),
            "--a1-run",
            str(shared / "A1"),
            "--scoring-path",
            str(shared / "scores.json"),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["decision"] == "blocked"
    assert payload["isolation_strength"] == "procedural_isolation"
    assert payload["os_sandbox_enforced"] is False
    assert payload["provider_calls"] == 0
    assert payload["runtime_authority"] is False
