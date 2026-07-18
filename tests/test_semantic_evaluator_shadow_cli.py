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
    execution_origin: str | None = "synthetic_fixture"
    qualification_class: str | None = "synthetic_only"
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


def _present_argv(*, json_output: bool = True) -> list[str]:
    values = [
        "experiments",
        "laj",
        "present",
        "--archive",
        "/private/input/archive",
        "--output-dir",
        "/private/output/reader",
    ]
    if json_output:
        values.append("--json")
    return values


def _demo_argv(*, json_output: bool = True) -> list[str]:
    values = [
        "experiments",
        "laj",
        "demo",
        "--archive-root",
        "/private/output/archive",
        "--output-dir",
        "/private/output/laj-advisory-demo",
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

    present = build_parser().parse_args(_present_argv())
    assert present.command == "experiments"
    assert present.experiments_action == "laj"
    assert present.experiment_laj_action == "present"

    demo = build_parser().parse_args(_demo_argv())
    assert demo.command == "experiments"
    assert demo.experiments_action == "laj"
    assert demo.experiment_laj_action == "demo"


def test_demo_cli_projects_synthetic_nonqualifying_result(monkeypatch, capsys) -> None:
    result = SimpleNamespace(
        archive_complete=True,
        execution_origin="synthetic_fixture",
        finding_count=0,
        ok=True,
        output_files=("laj.html", "laj.json", "laj.md"),
        presentation_available=True,
        qualification_class="synthetic_demo_only",
        reader_status="available",
        reason_codes=(),
        receipt_id="receipt-demo",
        replayed=False,
        view_sha256="2" * 64,
    )
    calls: list[dict[str, object]] = []

    def run_public_safe_laj_demo(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        experiments_commands.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            run_public_safe_laj_demo=run_public_safe_laj_demo
        ),
    )
    assert main(_demo_argv()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "advisory_only": True,
        "archive_complete": True,
        "execution_origin": "synthetic_fixture",
        "finding_count": 0,
        "ok": True,
        "output_files": ["laj.html", "laj.json", "laj.md"],
        "presentation_available": True,
        "qualification_class": "synthetic_demo_only",
        "qualification_eligible": False,
        "reader_status": "available",
        "reason_codes": [],
        "receipt_id": "receipt-demo",
        "replayed": False,
        "runtime_authority": False,
        "view_sha256": "2" * 64,
    }
    assert calls == [
        {
            "archive_root": "/private/output/archive",
            "output_dir": "/private/output/laj-advisory-demo",
        }
    ]
    assert "/private" not in json.dumps(payload)


def test_demo_cli_failure_is_value_free(monkeypatch, capsys) -> None:
    secret = "PRIVATE_DEMO_FAILURE_9201"

    def fail_import(_name: str):
        raise RuntimeError(secret)

    monkeypatch.setattr(experiments_commands.importlib, "import_module", fail_import)
    assert main(_demo_argv()) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["reason_codes"] == ["shadow_adapter_unavailable"]
    assert payload["qualification_eligible"] is False
    assert payload["runtime_authority"] is False
    assert secret not in captured.out + captured.err
    assert "/private" not in captured.out + captured.err


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
        "execution_origin": "synthetic_fixture",
        "ok": True,
        "qualification_class": "synthetic_only",
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
        "execution_origin": None,
        "ok": False,
        "qualification_class": None,
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


def test_present_cli_projects_fixed_paths_and_advisory_boundary(
    monkeypatch,
    capsys,
) -> None:
    view = SimpleNamespace(
        finding_count=2,
        status="available",
        view_sha256="1" * 64,
    )
    result = SimpleNamespace(view=view)
    calls: list[dict[str, object]] = []

    def write_laj_reader_artifacts(**kwargs):
        calls.append(kwargs)
        return result

    module = SimpleNamespace(
        LAJ_READER_FILENAMES=("laj.html", "laj.json", "laj.md"),
        write_laj_reader_artifacts=write_laj_reader_artifacts,
    )
    monkeypatch.setattr(
        experiments_commands.importlib,
        "import_module",
        lambda _name: module,
    )
    assert main(_present_argv()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "advisory_only": True,
        "finding_count": 2,
        "ok": True,
        "output_files": ["laj.html", "laj.json", "laj.md"],
        "runtime_authority": False,
        "status": "available",
        "view_sha256": "1" * 64,
    }
    assert calls == [
        {
            "archive_path": "/private/input/archive",
            "output_dir": "/private/output/reader",
            "expected_report_sha256": None,
        }
    ]


def test_present_cli_failure_is_value_free(monkeypatch, capsys) -> None:
    secret = "PRIVATE_ARCHIVE_PATH_48291"

    def fail_import(_name: str):
        raise RuntimeError(secret)

    monkeypatch.setattr(experiments_commands.importlib, "import_module", fail_import)
    assert main(_present_argv()) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "advisory_only": True,
        "finding_count": 0,
        "ok": False,
        "output_files": [],
        "runtime_authority": False,
        "status": "unavailable",
        "view_sha256": None,
    }
    assert secret not in captured.out + captured.err
    assert "/private" not in captured.out + captured.err
