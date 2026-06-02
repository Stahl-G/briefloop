from pathlib import Path

from multi_agent_brief.cli.main import main


def test_cli_init_and_run_with_config(tmp_path):
    demo_dir = tmp_path / "demo"

    assert main(["init", str(demo_dir)]) == 0
    assert (demo_dir / "config.yaml").exists()
    assert (demo_dir / "input" / "news.json").exists()

    assert main(["run", "--config", str(demo_dir / "config.yaml")]) == 0
    assert (demo_dir / "output" / "brief.md").exists()
    assert (demo_dir / "output" / "claim_ledger.json").exists()


def test_cli_audit_existing_brief(tmp_path):
    demo_dir = tmp_path / "demo"
    main(["init", str(demo_dir)])
    main(["run", "--config", str(demo_dir / "config.yaml")])

    audit_output = tmp_path / "audit.json"
    exit_code = main(
        [
            "audit",
            str(demo_dir / "output" / "brief.md"),
            "--ledger",
            str(demo_dir / "output" / "claim_ledger.json"),
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
