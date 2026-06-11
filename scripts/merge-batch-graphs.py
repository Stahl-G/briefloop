#!/usr/bin/env python3
"""Merge Understand Anything batch graphs with deterministic import filtering.

The file analyzer may describe files and import edges, but the final graph must
only trust the deterministic scanner/import-map outputs for file nodes and
``imports`` edges.
"""

from __future__ import annotations

import argparse
import json
import posixpath
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


IMPORT_EDGE_TYPE = "imports"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    node_id: str | None = None
    source: str | None = None
    target: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class CanonicalGraphInputs:
    files: frozenset[str]
    import_edges: frozenset[tuple[str, str]]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def normalize_repo_path(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("\\", "/")
    if raw.startswith("file:"):
        raw = raw[5:]
    raw = raw.split("#", 1)[0]
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
        return None
    if normalized.startswith("/"):
        return None
    return normalized


def file_path_from_node_id(node_id: str | None) -> str | None:
    if not isinstance(node_id, str) or not node_id.startswith("file:"):
        return None
    return normalize_repo_path(node_id[5:])


def _maybe_file_endpoint(node_id: str | None) -> str | None:
    return file_path_from_node_id(node_id)


def _node_repo_path(node: dict[str, Any]) -> str | None:
    node_id = node.get("id")
    file_from_id = file_path_from_node_id(node_id)
    if file_from_id is not None:
        return file_from_id
    file_path = normalize_repo_path(node.get("filePath"))
    if file_path is not None:
        return file_path
    return None


def _iter_batch_files(batches_payload: Any) -> Iterable[str]:
    if isinstance(batches_payload, dict):
        for file_item in batches_payload.get("files", []) or []:
            if isinstance(file_item, dict):
                path = normalize_repo_path(file_item.get("path"))
                if path:
                    yield path
        for path in (batches_payload.get("exportsByPath") or {}).keys():
            normalized = normalize_repo_path(path)
            if normalized:
                yield normalized
        for batch in batches_payload.get("batches", []) or []:
            yield from _iter_batch_files(batch)


def _iter_import_edges_from_map(import_map: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(import_map, dict):
        return
    for source, targets in import_map.items():
        source_path = normalize_repo_path(source)
        if source_path is None:
            continue
        if not isinstance(targets, list):
            continue
        for target in targets:
            target_path = normalize_repo_path(target)
            if target_path is not None:
                yield (source_path, target_path)


def _canonical_from_import_map(payload: Any) -> CanonicalGraphInputs:
    import_map = payload.get("importMap") if isinstance(payload, dict) else None
    edges = frozenset(_iter_import_edges_from_map(import_map))
    files = {path for edge in edges for path in edge}
    if isinstance(import_map, dict):
        files.update(path for path in (normalize_repo_path(path) for path in import_map.keys()) if path)
    return CanonicalGraphInputs(files=frozenset(files), import_edges=edges)


def _canonical_from_batch_payload(payload: Any) -> CanonicalGraphInputs:
    files = set(_iter_batch_files(payload))
    import_edges: set[tuple[str, str]] = set()
    if isinstance(payload, dict):
        import_edges.update(_iter_import_edges_from_map(payload.get("batchImportData")))
        for batch in payload.get("batches", []) or []:
            if isinstance(batch, dict):
                import_edges.update(_iter_import_edges_from_map(batch.get("batchImportData")))
    files.update(path for edge in import_edges for path in edge)
    return CanonicalGraphInputs(files=frozenset(files), import_edges=frozenset(import_edges))


def load_canonical_inputs(
    *,
    batches_path: Path | None = None,
    import_map_path: Path | None = None,
    batch_payload_paths: Iterable[Path] = (),
) -> CanonicalGraphInputs:
    files: set[str] = set()
    import_edges: set[tuple[str, str]] = set()

    if batches_path is not None:
        canonical = _canonical_from_batch_payload(_load_json(batches_path))
        files.update(canonical.files)
        import_edges.update(canonical.import_edges)

    if import_map_path is not None:
        canonical = _canonical_from_import_map(_load_json(import_map_path))
        files.update(canonical.files)
        import_edges.update(canonical.import_edges)

    for path in batch_payload_paths:
        canonical = _canonical_from_batch_payload(_load_json(path))
        files.update(canonical.files)
        import_edges.update(canonical.import_edges)

    if not files:
        raise ValueError("canonical file set is empty; provide batches.json or import map output")

    return CanonicalGraphInputs(files=frozenset(files), import_edges=frozenset(import_edges))


def _is_ignored_or_unscanned(path: str, repo_root: Path) -> bool:
    return (repo_root / path).exists()


def _dedupe_by_id(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def sanitize_graph(
    graph: dict[str, Any],
    canonical: CanonicalGraphInputs,
    *,
    repo_root: Path,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    kept_nodes: list[dict[str, Any]] = []
    kept_node_ids: set[str] = set()

    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        node_path = _node_repo_path(node)
        if node_id.startswith("file:"):
            if node_path not in canonical.files:
                code = (
                    "ignored_or_unscanned_file_node"
                    if node_path and _is_ignored_or_unscanned(node_path, repo_root)
                    else "unknown_file_node"
                )
                diagnostics.append(
                    Diagnostic(
                        code=code,
                        message="Dropped analyzer-created file node outside canonical scan set.",
                        node_id=node_id,
                        path=node_path,
                    )
                )
                continue
        elif node_path is not None and node_path not in canonical.files:
            diagnostics.append(
                Diagnostic(
                    code="unknown_file_owned_node",
                    message="Dropped analyzer-created node attached to noncanonical file path.",
                    node_id=node_id,
                    path=node_path,
                )
            )
            continue
        kept_nodes.append(node)
        kept_node_ids.add(node_id)

    kept_edges: list[dict[str, Any]] = []
    for edge in graph.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            continue

        if edge.get("type") == IMPORT_EDGE_TYPE:
            source_path = _maybe_file_endpoint(source)
            target_path = _maybe_file_endpoint(target)
            if source_path not in canonical.files:
                diagnostics.append(
                    Diagnostic(
                        code="import_source_not_canonical_file",
                        message="Dropped import edge with noncanonical source file.",
                        source=source,
                        target=target,
                        path=source_path,
                    )
                )
                continue
            if target_path not in canonical.files:
                code = (
                    "ignored_or_unscanned_target"
                    if target_path and _is_ignored_or_unscanned(target_path, repo_root)
                    else "import_target_not_canonical_file"
                )
                diagnostics.append(
                    Diagnostic(
                        code=code,
                        message="Dropped import edge with target outside canonical scan/import map.",
                        source=source,
                        target=target,
                        path=target_path,
                    )
                )
                continue
            if (source_path, target_path) not in canonical.import_edges:
                diagnostics.append(
                    Diagnostic(
                        code="import_edge_not_in_import_map",
                        message="Dropped analyzer-created import edge absent from deterministic import map.",
                        source=source,
                        target=target,
                    )
                )
                continue
            if source not in kept_node_ids or target not in kept_node_ids:
                diagnostics.append(
                    Diagnostic(
                        code="import_edge_endpoint_missing_after_sanitize",
                        message="Dropped import edge whose endpoint file node was not retained.",
                        source=source,
                        target=target,
                    )
                )
                continue
            kept_edges.append(edge)
            continue

        source_file = _maybe_file_endpoint(source)
        target_file = _maybe_file_endpoint(target)
        if source_file is not None and source_file not in canonical.files:
            diagnostics.append(
                Diagnostic(
                    code="edge_source_noncanonical_file",
                    message="Dropped edge from noncanonical file node.",
                    source=source,
                    target=target,
                    path=source_file,
                )
            )
            continue
        if target_file is not None and target_file not in canonical.files:
            diagnostics.append(
                Diagnostic(
                    code="edge_target_noncanonical_file",
                    message="Dropped edge to noncanonical file node.",
                    source=source,
                    target=target,
                    path=target_file,
                )
            )
            continue
        if source not in kept_node_ids or target not in kept_node_ids:
            diagnostics.append(
                Diagnostic(
                    code="edge_endpoint_missing_after_sanitize",
                    message="Dropped edge whose endpoint node was not retained.",
                    source=source,
                    target=target,
                )
            )
            continue
        kept_edges.append(edge)

    sanitized = dict(graph)
    sanitized["nodes"] = _dedupe_by_id(kept_nodes)
    sanitized["edges"] = kept_edges
    sanitized["diagnostics"] = [asdict(item) for item in diagnostics]
    sanitized["validation"] = validate_sanitized_graph(sanitized, canonical)
    return sanitized, diagnostics


def validate_sanitized_graph(
    graph: dict[str, Any],
    canonical: CanonicalGraphInputs,
) -> dict[str, int]:
    file_node_paths = {
        path
        for node in graph.get("nodes", []) or []
        if isinstance(node, dict)
        for path in [file_path_from_node_id(node.get("id"))]
        if path is not None
    }
    import_edges = [edge for edge in graph.get("edges", []) or [] if isinstance(edge, dict) and edge.get("type") == IMPORT_EDGE_TYPE]
    node_ids = {
        node.get("id")
        for node in graph.get("nodes", []) or []
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }

    noncanonical_file_nodes = sum(1 for path in file_node_paths if path not in canonical.files)
    noncanonical_endpoint_edges = 0
    import_edges_not_in_map = 0
    edges_with_missing_endpoint = 0
    for edge in import_edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_ids or target not in node_ids:
            edges_with_missing_endpoint += 1
            continue
        source_path = file_path_from_node_id(edge.get("source"))
        target_path = file_path_from_node_id(edge.get("target"))
        if source_path not in canonical.files or target_path not in canonical.files:
            noncanonical_endpoint_edges += 1
        elif (source_path, target_path) not in canonical.import_edges:
            import_edges_not_in_map += 1

    return {
        "missing_file_nodes": noncanonical_file_nodes,
        "import_edges_with_noncanonical_endpoint": noncanonical_endpoint_edges,
        "import_edges_not_in_import_map": import_edges_not_in_map,
        "edges_with_missing_endpoint": edges_with_missing_endpoint,
    }


def _load_batch_graphs(paths: Iterable[Path]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    graph_metadata: dict[str, Any] = {"version": "1.0.0"}

    for path in paths:
        payload = _load_json(path)
        if not isinstance(payload, dict):
            continue
        for key in ("version", "project", "layers", "tour"):
            if key in payload and key not in graph_metadata:
                graph_metadata[key] = payload[key]
        nodes.extend(item for item in payload.get("nodes", []) or [] if isinstance(item, dict))
        edges.extend(item for item in payload.get("edges", []) or [] if isinstance(item, dict))

    graph_metadata["nodes"] = nodes
    graph_metadata["edges"] = edges
    return graph_metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=Path, help="Path to batches.json.")
    parser.add_argument("--import-map", type=Path, help="Path to ua-import-map-output.json.")
    parser.add_argument(
        "--batch-payload",
        action="append",
        type=Path,
        default=[],
        help="Optional batch payload JSON with batchImportData. May be repeated.",
    )
    parser.add_argument("--graph", action="append", type=Path, required=True, help="Batch graph JSON. May be repeated.")
    parser.add_argument("--output", type=Path, required=True, help="Output sanitized graph path.")
    parser.add_argument("--diagnostics", type=Path, help="Optional diagnostics JSON path.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root for ignored/unscanned classification.")
    args = parser.parse_args(argv)

    canonical = load_canonical_inputs(
        batches_path=args.batches,
        import_map_path=args.import_map,
        batch_payload_paths=args.batch_payload,
    )
    graph = _load_batch_graphs(args.graph)
    sanitized, diagnostics = sanitize_graph(graph, canonical, repo_root=args.repo_root)
    _write_json(args.output, sanitized)
    if args.diagnostics is not None:
        _write_json(args.diagnostics, [asdict(item) for item in diagnostics])
    validation = sanitized["validation"]
    return 0 if all(value == 0 for value in validation.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
