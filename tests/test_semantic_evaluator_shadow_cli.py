"""Experimental CLI routing and value-free rendering tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from multi_agent_brief.cli.main import main


FIXTURES = Path(__file__).parent / "fixtures" / "semantic_evaluator_shadow"


def _argv(tmp_path: Path, *, json_output: bool = True) -> list[str]:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("report.md", "bounded_context.json", "instrument.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    values = [
        "experiments",
        "laj",
        "shadow-run",
        "--report",
        str(inputs / "report.md"),
        "--bounded-context",
        str(inputs / "bounded_context.json"),
        "--profile",
        "research_design_report_zh_v1",
        "--instrument",
        str(inputs / "instrument.json"),
        "--trial-id",
        "trial-cli-v1",
        "--archive-root",
        str(tmp_path / "archives"),
    ]
    if json_output:
        values.append("--json")
    return values


def test_shadow_cli_publishes_json_and_exactly_replays(tmp_path: Path, capsys) -> None:
    argv = _argv(tmp_path)
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"] is True
    assert first["replayed"] is False
    assert first["archive_complete"] is True
    assert first["run_status"] == "completed"
    assert first["validation_status"] == "accepted"
    assert first["qualification_eligible"] is False

    assert main(argv) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert replay["receipt_id"] == first["receipt_id"]


def test_non_json_shadow_cli_always_labels_experimental_advisory_scope(
    tmp_path: Path,
    capsys,
) -> None:
    assert main(_argv(tmp_path, json_output=False)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Experimental / Offline shadow / Advisory only"
    assert "PASS" not in "\n".join(lines)


def test_cli_duplicate_json_member_is_value_free_and_writes_no_archive(
    tmp_path: Path,
    capsys,
) -> None:
    argv = _argv(tmp_path)
    context_path = Path(argv[argv.index("--bounded-context") + 1])
    context_path.write_text(
        '{"schema_version":"x","schema_version":"y"}', encoding="utf-8"
    )
    assert main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "archive_complete": False,
        "archive_path": None,
        "ok": False,
        "qualification_eligible": False,
        "reason_codes": ["shadow_request_invalid"],
        "receipt_id": None,
        "replayed": False,
        "run_status": None,
        "validation_status": None,
    }
    assert not (tmp_path / "archives").exists()


def test_cli_missing_path_does_not_render_path_or_library_detail(
    tmp_path: Path,
    capsys,
) -> None:
    argv = _argv(tmp_path)
    hidden = "private-hidden-report-name.md"
    argv[argv.index("--report") + 1] = str(tmp_path / hidden)
    assert main(argv) == 1
    rendered = capsys.readouterr().out
    assert hidden not in rendered
    assert "Traceback" not in rendered
    assert json.loads(rendered)["reason_codes"] == ["shadow_request_invalid"]
