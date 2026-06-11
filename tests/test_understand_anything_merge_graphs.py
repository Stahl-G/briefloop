from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_merge_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "merge-batch-graphs.py"
    spec = importlib.util.spec_from_file_location("merge_batch_graphs", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical(module):
    return module.CanonicalGraphInputs(
        files=frozenset(
            {
                "src/a.py",
                "src/b.py",
                "src/real_ignored.py",
            }
        ),
        import_edges=frozenset({("src/a.py", "src/b.py")}),
    )


def test_merge_drops_import_edge_absent_from_import_map(tmp_path: Path):
    module = _load_merge_module()
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
            {"id": "file:src/b.py", "type": "file", "filePath": "src/b.py"},
            {"id": "file:src/real_ignored.py", "type": "file", "filePath": "src/real_ignored.py"},
        ],
        "edges": [
            {"source": "file:src/a.py", "target": "file:src/real_ignored.py", "type": "imports"},
        ],
    }

    sanitized, diagnostics = module.sanitize_graph(graph, _canonical(module), repo_root=tmp_path)

    assert sanitized["edges"] == []
    assert [item.code for item in diagnostics] == ["import_edge_not_in_import_map"]
    assert sanitized["validation"] == {
        "missing_file_nodes": 0,
        "import_edges_with_noncanonical_endpoint": 0,
        "import_edges_not_in_import_map": 0,
        "edges_with_missing_endpoint": 0,
    }


def test_merge_drops_agent_created_nonexistent_file_node(tmp_path: Path):
    module = _load_merge_module()
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
            {
                "id": "file:src/sources/filing_resolver_provider.py",
                "type": "file",
                "filePath": "src/sources/filing_resolver_provider.py",
            },
        ],
        "edges": [],
    }

    sanitized, diagnostics = module.sanitize_graph(graph, _canonical(module), repo_root=tmp_path)

    assert [node["id"] for node in sanitized["nodes"]] == ["file:src/a.py"]
    assert [item.code for item in diagnostics] == ["unknown_file_node"]
    assert sanitized["validation"]["missing_file_nodes"] == 0


def test_merge_drops_import_edge_from_noncanonical_source_even_when_node_exists(tmp_path: Path):
    module = _load_merge_module()
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
            {"id": "file:src/b.py", "type": "file", "filePath": "src/b.py"},
            {"id": "file:src/hallucinated.py", "type": "file", "filePath": "src/hallucinated.py"},
        ],
        "edges": [
            {"source": "file:src/hallucinated.py", "target": "file:src/b.py", "type": "imports"},
        ],
    }

    sanitized, diagnostics = module.sanitize_graph(graph, _canonical(module), repo_root=tmp_path)

    assert "file:src/hallucinated.py" not in {node["id"] for node in sanitized["nodes"]}
    assert sanitized["edges"] == []
    assert [item.code for item in diagnostics] == [
        "unknown_file_node",
        "import_source_not_canonical_file",
    ]


def test_merge_classifies_existing_but_unscanned_import_target(tmp_path: Path):
    module = _load_merge_module()
    ignored = tmp_path / "src" / "ignored.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("# intentionally outside scan set\n", encoding="utf-8")

    canonical = module.CanonicalGraphInputs(
        files=frozenset({"src/a.py"}),
        import_edges=frozenset(),
    )
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
        ],
        "edges": [
            {"source": "file:src/a.py", "target": "file:src/ignored.py", "type": "imports"},
        ],
    }

    sanitized, diagnostics = module.sanitize_graph(graph, canonical, repo_root=tmp_path)

    assert sanitized["edges"] == []
    assert [item.code for item in diagnostics] == ["ignored_or_unscanned_target"]
    assert sanitized["validation"] == {
        "missing_file_nodes": 0,
        "import_edges_with_noncanonical_endpoint": 0,
        "import_edges_not_in_import_map": 0,
        "edges_with_missing_endpoint": 0,
    }


def test_merge_drops_canonical_import_edge_when_endpoint_node_is_missing(tmp_path: Path):
    module = _load_merge_module()
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
        ],
        "edges": [
            {"source": "file:src/a.py", "target": "file:src/b.py", "type": "imports"},
        ],
    }

    sanitized, diagnostics = module.sanitize_graph(graph, _canonical(module), repo_root=tmp_path)

    assert sanitized["edges"] == []
    assert [item.code for item in diagnostics] == ["import_edge_endpoint_missing_after_sanitize"]
    assert sanitized["validation"] == {
        "missing_file_nodes": 0,
        "import_edges_with_noncanonical_endpoint": 0,
        "import_edges_not_in_import_map": 0,
        "edges_with_missing_endpoint": 0,
    }


def test_graph_validation_reports_missing_edge_endpoint():
    module = _load_merge_module()
    graph = {
        "nodes": [
            {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
        ],
        "edges": [
            {"source": "file:src/a.py", "target": "file:src/b.py", "type": "imports"},
        ],
    }

    assert module.validate_sanitized_graph(graph, _canonical(module)) == {
        "missing_file_nodes": 0,
        "import_edges_with_noncanonical_endpoint": 0,
        "import_edges_not_in_import_map": 0,
        "edges_with_missing_endpoint": 1,
    }


def test_cli_writes_diagnostics_and_clean_graph(tmp_path: Path):
    module = _load_merge_module()
    batches = tmp_path / "batches.json"
    import_map = tmp_path / "ua-import-map-output.json"
    graph = tmp_path / "batch.json"
    output = tmp_path / "assembled.json"
    diagnostics = tmp_path / "diagnostics.json"

    batches.write_text(
        json.dumps(
            {
                "batches": [
                    {
                        "files": [
                            {"path": "src/a.py"},
                            {"path": "src/b.py"},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    import_map.write_text(
        json.dumps({"importMap": {"src/a.py": ["src/b.py"], "src/b.py": []}}),
        encoding="utf-8",
    )
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "file:src/a.py", "type": "file", "filePath": "src/a.py"},
                    {"id": "file:src/b.py", "type": "file", "filePath": "src/b.py"},
                    {"id": "file:src/fake.py", "type": "file", "filePath": "src/fake.py"},
                ],
                "edges": [
                    {"source": "file:src/a.py", "target": "file:src/b.py", "type": "imports"},
                    {"source": "file:src/fake.py", "target": "file:src/b.py", "type": "imports"},
                ],
            }
        ),
        encoding="utf-8",
    )

    rc = module.main(
        [
            "--batches",
            str(batches),
            "--import-map",
            str(import_map),
            "--graph",
            str(graph),
            "--output",
            str(output),
            "--diagnostics",
            str(diagnostics),
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {node["id"] for node in payload["nodes"]} == {"file:src/a.py", "file:src/b.py"}
    assert payload["edges"] == [{"source": "file:src/a.py", "target": "file:src/b.py", "type": "imports"}]
    assert any(item["code"] == "unknown_file_node" for item in json.loads(diagnostics.read_text(encoding="utf-8")))
