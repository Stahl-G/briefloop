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


# TEST-SLIM-1 (ruling da184ba5): the full 89-id x detail matrix renders the same
# SchemaRegistry machinery per id; exact rendering and wheel parity are now
# proven on a representative cross-family sample. Coverage citation (TD-2):
# SchemaRegistry exactness is owned per contract by tests/test_contract_registry.py
# and the non-editable wheel parity test below; the unsampled ids exercise the
# identical code path (`contract show` -> SchemaRegistry).
_REPRESENTATIVE_CONTRACT_IDS = [
    "briefloop.source_proposal.v2",
    "briefloop.claim_record.v2",
    "briefloop.event_envelope.v2",
    "briefloop.runtime_adapter_binding.v2",
    "briefloop.core_run_next_action.v2",
]


@pytest.mark.parametrize("contract_id", _REPRESENTATIVE_CONTRACT_IDS)
@pytest.mark.parametrize("detail", ("minimal", "full"))
def test_contract_show_examples_are_exact_and_valid(contract_id, detail, capsys) -> None:
    assert main(["contract", "show", contract_id, "--example", detail]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == SchemaRegistry.example(contract_id, detail)
    assert SchemaRegistry.validate(contract_id, payload) == []


@pytest.mark.parametrize("contract_id", _REPRESENTATIVE_CONTRACT_IDS)
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

    intake_script = r'''
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

from multi_agent_brief.cli.main import main
from multi_agent_brief.contracts.v2 import (
    Invocation,
    RunIdentity,
    StageState,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import SQLiteControlStore

workspace = Path(sys.argv[1])
workspace.mkdir()
now = "2026-07-15T12:00:00Z"
clock = lambda: datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

def record(model, **values):
    return model.model_validate(
        {"schema_version": model.schema_id, **values},
        strict=True,
    )

with SQLiteControlStore.create(
    workspace / "briefloop.db",
    workspace_id="WS-WHEEL-001",
    clock=clock,
) as store:
    unit = store.begin("RUN-WHEEL-001", "TX-WHEEL-SEED", "private_test_seed", 0)
    unit.put_run(record(
        RunIdentity,
        run_id="RUN-WHEEL-001",
        workspace_id="WS-WHEEL-001",
        runtime="operator",
        created_at=now,
    ))
    unit.put_workspace_run_head(record(
        WorkspaceRunHead,
        workspace_id="WS-WHEEL-001",
        current_run_id="RUN-WHEEL-001",
        updated_at=now,
    ))
    for stage_id in ("source-discovery", "scout"):
        unit.put_stage_state(record(
            StageState,
            run_id="RUN-WHEEL-001",
            stage_id=stage_id,
            status="ready",
            revision=0,
            updated_at=now,
        ))
    for invocation_id, role_id in (
        ("INV-WHEEL-SOURCE", "source-provider"),
        ("INV-WHEEL-SCOUT", "scout"),
    ):
        unit.put_invocation(record(
            Invocation,
            invocation_id=invocation_id,
            run_id="RUN-WHEEL-001",
            role_id=role_id,
            runtime="operator",
            status="active",
            started_at=now,
        ))
    unit.commit()

source_dir = workspace / "scratch" / "INV-WHEEL-SOURCE"
source_dir.mkdir(parents=True)
content = b"Packaged synthetic source.\n"
(source_dir / "source_content.txt").write_bytes(content)
(source_dir / "source_proposal.json").write_text(json.dumps({
    "schema_version": "briefloop.source_proposal.v2",
    "proposal_id": "PROP-WHEEL-SOURCE",
    "run_id": "RUN-WHEEL-001",
    "source_id": "SRC-WHEEL-001",
    "origin_type": "uploaded_file",
    "acquisition_method": "manual_upload",
    "material_kind": "uploaded_file",
    "provider": None,
    "locator": {"kind": "file", "path": "scratch/INV-WHEEL-SOURCE/source_content.txt"},
    "title": "Packaged synthetic source",
    "publisher": None,
    "published_at": None,
    "retrieved_at": now,
    "source_category": "other",
    "retrieval_source_type": "local_file",
    "underlying_evidence_type": "unknown",
    "raw_underlying_evidence_type": None,
    "content_sha256": hashlib.sha256(content).hexdigest(),
    "content_media_type": "text/plain",
    "raw_payload_sha256": None,
    "raw_payload_media_type": None,
}, sort_keys=True), encoding="utf-8")
(source_dir / "submit_request.json").write_text(json.dumps({
    "schema_version": "briefloop.source_commit_request.v2",
    "request_id": "REQ-WHEEL-SOURCE",
    "run_id": "RUN-WHEEL-001",
    "invocation_id": "INV-WHEEL-SOURCE",
    "proposal_path": "scratch/INV-WHEEL-SOURCE/source_proposal.json",
    "content_path": "scratch/INV-WHEEL-SOURCE/source_content.txt",
    "raw_payload_path": None,
    "expected_store_revision": 1,
}, sort_keys=True), encoding="utf-8")

candidate_dir = workspace / "scratch" / "INV-WHEEL-SCOUT"
candidate_dir.mkdir(parents=True)
(candidate_dir / "candidate_claims.json").write_text(json.dumps({
    "schema_version": "briefloop.candidate_claims_proposal.v2",
    "proposal_id": "PROP-WHEEL-CANDIDATE",
    "run_id": "RUN-WHEEL-001",
    "created_at": now,
    "candidates": [{
        "candidate_id": "CAND-WHEEL-001",
        "source_id": "SRC-WHEEL-001",
        "statement": "A packaged synthetic source was supplied.",
        "evidence_text": "Packaged synthetic source.",
        "topic": "packaging",
        "claim_type": "fact",
        "confidence": "high",
    }],
}, sort_keys=True), encoding="utf-8")
(candidate_dir / "submit_request.json").write_text(json.dumps({
    "schema_version": "briefloop.artifact_submit_request.v2",
    "request_id": "REQ-WHEEL-CANDIDATE",
    "run_id": "RUN-WHEEL-001",
    "artifact_id": "candidate_claims",
    "invocation_id": "INV-WHEEL-SCOUT",
    "input_path": "scratch/INV-WHEEL-SCOUT/candidate_claims.json",
    "expected_store_revision": 2,
    "expected_artifact_revision": 0,
}, sort_keys=True), encoding="utf-8")

stream = io.StringIO()
with redirect_stdout(stream):
    source_rc = main([
        "intake-v2", "source", "--workspace", str(workspace),
        "--request", "scratch/INV-WHEEL-SOURCE/submit_request.json", "--json",
    ])
    candidate_rc = main([
        "intake-v2", "candidate", "--workspace", str(workspace),
        "--request", "scratch/INV-WHEEL-SCOUT/submit_request.json", "--json",
    ])
results = [json.loads(line) for line in stream.getvalue().splitlines()]
with SQLiteControlStore.open(workspace / "briefloop.db") as store:
    snapshot = store.load_snapshot("RUN-WHEEL-001")
    summary = {
        "return_codes": [source_rc, candidate_rc],
        "statuses": [item["status"] for item in results],
        "source_ids": [item.source_id for item in snapshot.sources],
        "proposal_ids": [item.proposal_id for item in snapshot.accepted_proposals],
        "binding_ids": [
            [item.proposal_id, item.source_id]
            for item in snapshot.proposal_source_bindings
        ],
        "artifact_revision_count": len(snapshot.artifact_revisions),
        "revision": snapshot.store_revision,
    }
print(json.dumps(summary, sort_keys=True))
'''
    intake = subprocess.run(
        [sys.executable, "-c", intake_script, str(tmp_path / "wheel-workspace")],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert intake.returncode == 0, intake.stdout + intake.stderr
    assert json.loads(intake.stdout) == {
        "artifact_revision_count": 2,
        "binding_ids": [["PROP-WHEEL-CANDIDATE", "SRC-WHEEL-001"]],
        "proposal_ids": ["PROP-WHEEL-CANDIDATE"],
        "return_codes": [0, 0],
        "revision": 3,
        "source_ids": ["SRC-WHEEL-001"],
        "statuses": ["committed", "committed"],
    }
