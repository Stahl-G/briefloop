import json
from functools import partial
from pathlib import Path

import pytest

from multi_agent_brief.cli.main import main
from multi_agent_brief.provenance.model import PROVENANCE_GRAPH_FILE
from multi_agent_brief.provenance.validator import validate_graph_payload
from tests.helpers import write_legacy_control_files, write_workspace_files_under


REPO = Path(__file__).resolve().parents[1]


_workspace = partial(
    write_workspace_files_under,
    config_text="\n".join([
        "project:",
        "  name: Synthetic TargetCo",
        "report:",
        "  date: '2026-06-09'",
        "output:",
        "  path: output",
        "input:",
        "  path: input",
        "",
    ]),
    user_text="# User\n\nSynthetic TargetCo\n",
)






























def test_provenance_validator_rejects_hard_graph_errors():
    graph = {
        "schema_version": "multi-agent-brief-provenance-graph/v1",
        "run_id": "RUN",
        "workspace": ".",
        "source_files": [{"path": "/etc/hosts"}],
        "nodes": [
            {"id": "run:RUN", "type": "run", "ref": "."},
            {"id": "run:RUN", "type": "run", "ref": "."},
            {"id": "artifact:bad", "type": "artifact", "ref": "../bad.json"},
        ],
        "edges": [
            {"from": "source:S1", "to": "claim:C1", "type": "source_supports_claim"},
            {"from": "run:RUN", "to": "missing:node", "type": "run_has_stage"},
            {"from": "run:RUN", "to": "artifact:bad", "type": "unknown_edge"},
        ],
        "warnings": [],
    }

    result = validate_graph_payload(graph)

    assert result["ok"] is False
    assert any("duplicated" in error for error in result["errors"])
    assert any("semantic" in error for error in result["errors"])
    assert any("missing node" in error for error in result["errors"])
    assert any("relative" in error or "traversal" in error for error in result["errors"])






@pytest.mark.parametrize("action", ("build", "show", "validate"))
def test_provenance_cli_is_retired_with_zero_writes(tmp_path, capsys, action):
    ws = write_legacy_control_files(_workspace(tmp_path))
    argv = ["provenance", action, "--workspace", str(ws), "--json"]
    if action == "build":
        argv.extend(["--repo-workdir", str(REPO)])
    before_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }

    rc = main(argv)

    # the JSON provenance CLI payloads are removed with the
    # retired legacy-workspace public surface; rejection is now fail-closed.
    assert rc == 1
    assert capsys.readouterr().out == "legacy_workspace_unsupported\n"
    after_files = {
        path.relative_to(ws).as_posix(): path.read_bytes()
        for path in ws.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert not (ws / PROVENANCE_GRAPH_FILE).exists()




