from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import build_parser, main
from multi_agent_brief.contracts.v2 import (
    Invocation,
    RunIdentity,
    StageState,
    WorkspaceRunHead,
)
from multi_agent_brief.control_store import SQLiteControlStore


RUN_ID = "RUN-PR3-CLI-001"
WORKSPACE_ID = "WS-PR3-CLI-001"
NOW = "2026-07-15T12:00:00Z"


def _record(model_type, **values):
    return model_type.model_validate(
        {"schema_version": model_type.schema_id, **values},
        strict=True,
    )


def _seed_workspace(workspace: Path) -> None:
    workspace.mkdir()
    with SQLiteControlStore.create(
        workspace / "briefloop.db",
        workspace_id=WORKSPACE_ID,
        clock=lambda: datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    ) as store:
        unit = store.begin(RUN_ID, "TX-CLI-SEED-001", "private_test_seed", 0)
        unit.put_run(
            _record(
                RunIdentity,
                run_id=RUN_ID,
                workspace_id=WORKSPACE_ID,
                runtime="operator",
                created_at=NOW,
            )
        )
        unit.put_workspace_run_head(
            _record(
                WorkspaceRunHead,
                workspace_id=WORKSPACE_ID,
                current_run_id=RUN_ID,
                updated_at=NOW,
            )
        )
        unit.put_stage_state(
            _record(
                StageState,
                run_id=RUN_ID,
                stage_id="source-discovery",
                status="ready",
                revision=0,
                updated_at=NOW,
            )
        )
        unit.put_invocation(
            _record(
                Invocation,
                invocation_id="INV-SOURCE-001",
                run_id=RUN_ID,
                role_id="source-provider",
                runtime="operator",
                status="active",
                started_at=NOW,
            )
        )
        unit.commit()


def _source_request(workspace: Path) -> Path:
    scratch = workspace / "scratch" / "INV-SOURCE-001"
    scratch.mkdir(parents=True)
    content = b"Synthetic public filing bytes.\n"
    (scratch / "source_content.pdf").write_bytes(content)
    (scratch / "source_proposal.json").write_text(
        json.dumps(
            {
                "schema_version": "briefloop.source_proposal.v2",
                "proposal_id": "PROP-SOURCE-001",
                "run_id": RUN_ID,
                "source_id": "SRC-001",
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "locator": {
                    "kind": "file",
                    "path": "scratch/INV-SOURCE-001/source_content.pdf",
                },
                "title": "Synthetic public filing",
                "publisher": None,
                "published_at": None,
                "retrieved_at": NOW,
                "source_category": "regulator",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "filing",
                "raw_underlying_evidence_type": None,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "application/pdf",
                "raw_payload_sha256": None,
                "raw_payload_media_type": None,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    request = scratch / "submit_request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "briefloop.source_commit_request.v2",
                "request_id": "REQ-SOURCE-001",
                "run_id": RUN_ID,
                "invocation_id": "INV-SOURCE-001",
                "proposal_path": "scratch/INV-SOURCE-001/source_proposal.json",
                "content_path": "scratch/INV-SOURCE-001/source_content.pdf",
                "raw_payload_path": None,
                "expected_store_revision": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return request


def test_hidden_intake_cli_commits_source_and_emits_one_json_object(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    _seed_workspace(workspace)
    request = _source_request(workspace).relative_to(workspace).as_posix()

    exit_code = main(
        [
            "intake-v2",
            "source",
            "--workspace",
            str(workspace),
            "--request",
            request,
            "--json",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert output.out.count("\n") == 1
    payload = json.loads(output.out)
    assert payload["status"] == "committed"
    assert payload["source_id"] == "SRC-001"
    assert payload["receipt"]["source_ids"] == ["SRC-001"]
    with SQLiteControlStore.open(workspace / "briefloop.db") as store:
        assert [item.source_id for item in store.load_snapshot(RUN_ID).sources] == [
            "SRC-001"
        ]


def test_intake_cli_json_only_workspace_never_creates_sqlite_fallback(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scratch = workspace / "scratch" / "INV-SOURCE-001"
    scratch.mkdir(parents=True)
    request = scratch / "submit_request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "briefloop.source_commit_request.v2",
                "request_id": "REQ-SOURCE-001",
                "run_id": RUN_ID,
                "invocation_id": "INV-SOURCE-001",
                "proposal_path": "scratch/INV-SOURCE-001/source_proposal.json",
                "content_path": "scratch/INV-SOURCE-001/source_content.pdf",
                "raw_payload_path": None,
                "expected_store_revision": 0,
            }
        ),
        encoding="utf-8",
    )
    (scratch / "source_proposal.json").write_text("{}", encoding="utf-8")
    (scratch / "source_content.pdf").write_bytes(b"x")

    exit_code = main(
        [
            "intake-v2",
            "source",
            "--workspace",
            str(workspace),
            "--request",
            request.relative_to(workspace).as_posix(),
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed_uncommitted",
        "error_code": "control_store_not_found",
    }
    assert not (workspace / "briefloop.db").exists()
    assert not (workspace / "briefloop.db.blobs").exists()


def test_intake_cli_is_labelled_internal_and_requires_json() -> None:
    parser = build_parser(prog="briefloop")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "intake-v2",
                "candidate",
                "--workspace",
                "workspace",
                "--request",
                "scratch/INV/submit_request.json",
            ]
        )
    assert exc.value.code == 2

    intake_action = next(
        action
        for action in parser._actions
        if getattr(action, "choices", None) and "intake-v2" in action.choices
    )
    intake_parser = intake_action.choices["intake-v2"]
    assert "not the active runtime path" in intake_parser.description


def test_intake_v2_imports_are_confined_to_dormant_package_and_cli_adapter() -> None:
    package_root = Path(__file__).parents[1] / "src" / "multi_agent_brief"
    allowed = {
        "cli/core_v2_commands.py",
        "cli/intake_v2_commands.py",
        "intake_v2/__init__.py",
        "intake_v2/service.py",
    }
    findings: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module == "multi_agent_brief.intake_v2" or module.startswith(
                "multi_agent_brief.intake_v2."
            ):
                if (
                    relative not in allowed
                    and not relative.startswith("intake_v2/")
                    and not relative.startswith("core_run_v2/")
                ):
                    findings.append(f"{relative}:{node.lineno}")
    assert findings == []


def test_intake_v2_has_no_json_control_file_writer() -> None:
    package = Path(__file__).parents[1] / "src" / "multi_agent_brief" / "intake_v2"
    forbidden_calls = {"write_text", "write_bytes", "json.dump", "json.dumps"}
    findings: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    name = f"{node.func.value.id}.{node.func.attr}"
                else:
                    name = node.func.attr
            if name in forbidden_calls:
                findings.append(f"{path.name}:{node.lineno}:{name}")
    assert findings == []
