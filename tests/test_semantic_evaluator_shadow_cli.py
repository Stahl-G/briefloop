"""Experimental LAJ CLI routing and value-free rendering tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

from multi_agent_brief.cli import experiments_commands
from multi_agent_brief.cli.main import build_parser, main


@dataclass(frozen=True)
class _Result:
    ok: bool = True
    replayed: bool = False
    archive_complete: bool = True
    archive_path: str | None = "/private/secret/archive"
    receipt_id: str | None = "receipt-public-identity"
    run_status: str | None = "completed"
    validation_status: str | None = "accepted"
    reason_codes: tuple[str, ...] = ()
    qualification_eligible: bool = False


def _argv(*, json_output: bool = True) -> list[str]:
    values = [
        "experiments",
        "laj",
        "shadow-run",
        "--report",
        "/private/input/report.md",
        "--bounded-context",
        "/private/input/context.json",
        "--profile",
        "research_design_report_zh_v1",
        "--instrument",
        "/private/input/instrument.json",
        "--trial-id",
        "trial-cli-v4",
        "--archive-root",
        "/private/output/archive",
    ]
    if json_output:
        values.append("--json")
    return values


def _fake_runner(monkeypatch, *, result: _Result | None = None) -> list[dict]:
    calls: list[dict] = []

    def run_shadow(**kwargs):
        calls.append(kwargs)
        return result or _Result()

    module = SimpleNamespace(run_shadow=run_shadow)
    monkeypatch.setattr(
        experiments_commands.importlib,
        "import_module",
        lambda name: module,
    )
    return calls


def test_shadow_cli_is_registered_only_below_experiments_laj() -> None:
    args = build_parser().parse_args(_argv())
    assert args.command == "experiments"
    assert args.experiments_action == "laj"
    assert args.experiment_laj_action == "shadow-run"


def test_shadow_cli_emits_fixed_json_without_paths_or_provider_material(
    monkeypatch,
    capsys,
) -> None:
    calls = _fake_runner(monkeypatch)
    assert main(_argv()) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "archive_complete": True,
        "ok": True,
        "qualification_eligible": False,
        "reason_codes": [],
        "receipt_id": "receipt-public-identity",
        "replayed": False,
        "run_status": "completed",
        "validation_status": "accepted",
    }
    assert captured.err == ""
    assert "/private" not in captured.out
    assert calls == [
        {
            "report": "/private/input/report.md",
            "bounded_context": "/private/input/context.json",
            "profile": "research_design_report_zh_v1",
            "instrument": "/private/input/instrument.json",
            "trial_id": "trial-cli-v4",
            "archive_root": "/private/output/archive",
        }
    ]


def test_shadow_cli_replay_projects_only_fixed_result_fields(
    monkeypatch, capsys
) -> None:
    result = _Result(replayed=True)
    _fake_runner(monkeypatch, result=result)
    assert main(_argv()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["replayed"] is True
    assert payload["receipt_id"] == result.receipt_id
    assert "archive_path" not in payload


def test_shadow_cli_non_json_is_explicitly_experimental_and_advisory(
    monkeypatch,
    capsys,
) -> None:
    _fake_runner(monkeypatch)
    assert main(_argv(json_output=False)) == 0
    rendered = capsys.readouterr().out
    assert rendered.splitlines()[0] == "Experimental / Offline shadow / Advisory only"
    assert "PASS" not in rendered
    assert "/private" not in rendered


def test_shadow_cli_untyped_exception_is_value_free(monkeypatch, capsys) -> None:
    secret = "PRIVATE_PROVIDER_BODY_98431"

    def fail_import(_name: str):
        raise RuntimeError(secret)

    monkeypatch.setattr(experiments_commands.importlib, "import_module", fail_import)
    assert main(_argv()) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "archive_complete": False,
        "ok": False,
        "qualification_eligible": False,
        "reason_codes": ["shadow_adapter_unavailable"],
        "receipt_id": None,
        "replayed": False,
        "run_status": None,
        "validation_status": None,
    }
    assert secret not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_shadow_cli_real_runner_rejects_missing_input_without_echo(capsys) -> None:
    hidden = "PRIVATE_MISSING_REPORT_39271.md"
    argv = _argv()
    argv[argv.index("--report") + 1] = f"/tmp/{hidden}"
    assert main(argv) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["reason_codes"] == ["shadow_request_invalid"]
    assert hidden not in captured.out + captured.err
    assert "/tmp" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
