from pathlib import Path

from multi_agent_brief.cli.main import main


def test_cli_init_and_run(tmp_path):
    workspace = tmp_path / "ws"

    assert main(["init", str(workspace), "--language", "zh-CN", "--industry", "finance"]) == 0
    assert (workspace / "config.yaml").exists()
    assert (workspace / "sources.yaml").exists()

    # Add a source file
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "news.md").write_text("- Test signal for weekly brief.\n", encoding="utf-8")

    assert main(["run", "--config", str(workspace / "config.yaml")]) == 0
    assert (workspace / "output" / "brief.md").exists()
    assert (workspace / "output" / "intermediate" / "draft_brief.md").exists()
    assert (workspace / "output" / "intermediate" / "claim_ledger.json").exists()


def test_cli_run_with_industry(tmp_path):
    workspace = tmp_path / "ws"
    main(["init", str(workspace), "--language", "zh-CN", "--industry", "finance"])

    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "data.md").write_text("- Financial earnings report shows growth.\n", encoding="utf-8")

    assert main(["run", "--config", str(workspace / "config.yaml"), "--industry", "finance"]) == 0
    assert (workspace / "output" / "brief.md").exists()


def test_cli_audit_existing_brief(tmp_path):
    workspace = tmp_path / "ws"
    main(["init", str(workspace), "--language", "zh-CN"])
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "news.md").write_text("- Test signal for audit.\n", encoding="utf-8")
    main(["run", "--config", str(workspace / "config.yaml")])

    audit_output = tmp_path / "audit.json"
    exit_code = main(
        [
            "audit",
            str(workspace / "output" / "brief.md"),
            "--ledger",
            str(workspace / "output" / "intermediate" / "claim_ledger.json"),
            "--output",
            str(audit_output),
            "--report-date",
            "2026-06-02",
            "--max-source-age-days",
            "14",
            "--fail-on-stale-source",
        ]
    )

    assert exit_code == 0
    assert '"audit_status": "pass"' in audit_output.read_text(encoding="utf-8")


def test_cli_version(capsys):
    assert main(["version"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_cli_run_returns_2_on_audit_fail(tmp_path):
    """When audit_status is fail, run must return exit code 2."""
    from unittest.mock import patch
    from multi_agent_brief.core.schemas import AuditReport, AuditFinding

    workspace = tmp_path / "ws"
    main(["init", str(workspace), "--language", "zh-CN"])
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "input" / "news.md").write_text(
        "- Test signal for audit fail scenario.\n", encoding="utf-8"
    )

    fail_report = AuditReport(
        audit_status="fail",
        audit_score=0,
        findings=[
            AuditFinding(
                finding_id="TEST_FAIL_001",
                severity="high",
                finding_type="test",
                description="Forced high-severity finding for testing.",
                recommendation="Fix it.",
            )
        ],
        metadata={},
    )

    original_run = None

    def mock_audit_run(self, context, ledger):
        context.report_state.audit_report = fail_report
        from multi_agent_brief.core.schemas import AgentOutput
        return AgentOutput(agent_name=self.name, summary="Audit forced fail.")

    with patch.object(
        __import__(
            "multi_agent_brief.agents.auditor", fromlist=["AuditorAgent"]
        ).AuditorAgent,
        "run",
        mock_audit_run,
    ):
        exit_code = main(["run", "--config", str(workspace / "config.yaml")])

    assert exit_code == 2, f"Expected exit code 2 on audit fail, got {exit_code}"
