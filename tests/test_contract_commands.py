"""CLI and non-editable package parity for strict v2 contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts import SchemaRegistry, V2_CONTRACT_IDS


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("contract_id", V2_CONTRACT_IDS)
@pytest.mark.parametrize("detail", ("minimal", "full"))
def test_contract_show_examples_are_exact_and_valid(contract_id, detail, capsys) -> None:
    assert main(["contract", "show", contract_id, "--example", detail]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == SchemaRegistry.example(contract_id, detail)
    assert SchemaRegistry.validate(contract_id, payload) == []


@pytest.mark.parametrize("contract_id", V2_CONTRACT_IDS)
def test_contract_show_schema_is_exact(contract_id, capsys) -> None:
    assert main(["contract", "show", contract_id, "--schema"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == SchemaRegistry.json_schema(contract_id)
    assert payload["$id"] == contract_id
    assert len(payload["examples"]) == 2


def test_contract_show_requires_one_output_mode() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["contract", "show", V2_CONTRACT_IDS[0]])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        main(["contract", "show", V2_CONTRACT_IDS[0], "--schema", "--example", "minimal"])
    assert exc.value.code == 2


def test_contract_show_rejects_legacy_or_unknown_ids() -> None:
    for contract_id in ("source_item", "briefloop.unknown.v2"):
        with pytest.raises(SystemExit) as exc:
            main(["contract", "show", contract_id, "--schema"])
        assert exc.value.code == 2


def test_built_wheel_exposes_identical_schema_and_examples(tmp_path: Path) -> None:
    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)

    contract_id = "briefloop.transaction_receipt.v2"
    expected = {
        "schema": SchemaRegistry.json_schema(contract_id),
        "minimal": SchemaRegistry.example(contract_id, "minimal"),
        "full": SchemaRegistry.example(contract_id, "full"),
    }
    script = """
import json
from multi_agent_brief.contracts import SchemaRegistry
contract_id = 'briefloop.transaction_receipt.v2'
print(json.dumps({
    'schema': SchemaRegistry.json_schema(contract_id),
    'minimal': SchemaRegistry.example(contract_id, 'minimal'),
    'full': SchemaRegistry.example(contract_id, 'full'),
}, sort_keys=True))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout) == expected

    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "multi_agent_brief.cli.main",
            "contract",
            "show",
            contract_id,
            "--example",
            "full",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert json.loads(cli.stdout) == expected["full"]
