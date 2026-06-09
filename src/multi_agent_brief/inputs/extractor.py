"""MinerU-backed input document extraction."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from multi_agent_brief.inputs.contracts import (
    DIR_ROLES,
    MINERU_EXTRACTABLE_SUFFIXES,
    extracted_markdown_path,
    safe_rel,
)


def extract_input_documents(
    *,
    input_path: Path,
    workspace: Path,
    output_dir: Path,
    backend: str = "pipeline",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Convert extractable input documents into Markdown next to source files."""
    files = _scan_extractable_files(input_path)
    report: dict[str, Any] = {
        "schema_version": "multi-agent-brief-input-extraction/v1",
        "extractor": "mineru",
        "backend": backend,
        "input_path": safe_rel(input_path, workspace),
        "output_policy": "write .mineru.md next to each original input file",
        "dry_run": dry_run,
        "extracted": [],
        "skipped": [],
        "errors": [],
    }
    if not files:
        report["status"] = "no_extractable_inputs"
        return report

    if dry_run:
        report["status"] = "dry_run"
        for file_path in files:
            role = _input_role_for_path(file_path, input_path)
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="dry_run",
                    reason="dry_run",
                )
            )
        return report

    mineru_bin = shutil.which("mineru")
    if not mineru_bin:
        report["status"] = "failed"
        report["errors"].append(
            "MinerU CLI not found. Install with `pip install \"mineru[all]\"` "
            "or use a runtime/source-provider flow configured for remote MinerU."
        )
        for file_path in files:
            role = _input_role_for_path(file_path, input_path)
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="missing_mineru_cli",
                    reason="missing_mineru_cli",
                )
            )
        return report

    mineru_output_base = output_dir / "mineru_output" / "input_extract"
    mineru_output_base.mkdir(parents=True, exist_ok=True)

    for file_path in files:
        role = _input_role_for_path(file_path, input_path)
        if role == "unknown":
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="skipped",
                    reason="unknown_input_subdir",
                )
            )
            continue

        target_path = extracted_markdown_path(file_path)
        if target_path.exists() and not force:
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="already_exists",
                    reason="already_exists",
                    markdown_path=target_path,
                )
            )
            continue

        run_dir = mineru_output_base / _stable_file_key(file_path, input_path)
        if run_dir.exists() and force:
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            mineru_bin,
            "-p",
            str(file_path.resolve()),
            "-o",
            str(run_dir.resolve()),
            "-b",
            backend,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="failed",
                    reason=type(exc).__name__,
                    markdown_path=target_path,
                )
            )
            continue

        if result.returncode != 0:
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="failed",
                    reason="mineru_cli_failed",
                    markdown_path=target_path,
                    detail=(result.stderr or result.stdout or "")[:500],
                )
            )
            continue

        extracted_markdown = _read_mineru_markdown(run_dir)
        if not extracted_markdown.strip():
            report["skipped"].append(
                _extraction_record(
                    file_path,
                    input_path,
                    workspace,
                    role=role,
                    status="failed",
                    reason="empty_mineru_output",
                    markdown_path=target_path,
                )
            )
            continue

        target_path.write_text(
            _render_extracted_markdown(
                file_path=file_path,
                workspace=workspace,
                role=role,
                backend=backend,
                extracted_markdown=extracted_markdown,
            ),
            encoding="utf-8",
        )
        report["extracted"].append(
            _extraction_record(
                file_path,
                input_path,
                workspace,
                role=role,
                status="extracted",
                markdown_path=target_path,
            )
        )

    has_failed_extraction = any(
        item.get("status") == "failed" for item in report.get("skipped", [])
    )
    if has_failed_extraction and report["extracted"]:
        report["status"] = "completed_with_errors"
    elif has_failed_extraction:
        report["status"] = "failed"
    else:
        report["status"] = "completed"
    return report


def _scan_extractable_files(input_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
            continue
        if path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() in MINERU_EXTRACTABLE_SUFFIXES:
            files.append(path)
    return files


def _input_role_for_path(path: Path, input_dir: Path) -> str:
    rel = path.relative_to(input_dir)
    if len(rel.parts) == 1:
        return "evidence"
    return DIR_ROLES.get(rel.parts[0], "unknown")


def _stable_file_key(path: Path, input_dir: Path) -> str:
    rel = path.relative_to(input_dir).as_posix()
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
    safe_stem = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in path.stem)
    return f"{safe_stem}_{digest}"


def _read_mineru_markdown(run_dir: Path) -> str:
    parts: list[str] = []
    for md_file in sorted(run_dir.rglob("*.md")):
        if md_file.stem.lower() == "metadata":
            continue
        try:
            text = md_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        rel = md_file.relative_to(run_dir).as_posix()
        parts.append(f"## MinerU output: {rel}\n\n{text}")
    return "\n\n".join(parts).strip()


def _render_extracted_markdown(
    *,
    file_path: Path,
    workspace: Path,
    role: str,
    backend: str,
    extracted_markdown: str,
) -> str:
    original_rel = safe_rel(file_path, workspace)
    metadata = {
        "schema_version": "multi-agent-brief-input-extraction/v1",
        "extractor": "mineru",
        "backend": backend,
        "original_path": original_rel,
        "input_role": role,
    }
    return (
        f"<!-- mabw-input-extraction: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)} -->\n"
        f"# Extracted Input Document: {file_path.name}\n\n"
        f"- Original file: `{original_rel}`\n"
        f"- Input role: `{role}`\n"
        f"- Extractor: MinerU (`{backend}`)\n\n"
        "---\n\n"
        f"{extracted_markdown.strip()}\n"
    )


def _extraction_record(
    file_path: Path,
    input_path: Path,
    workspace: Path,
    *,
    role: str,
    status: str,
    reason: str = "",
    markdown_path: Path | None = None,
    detail: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": safe_rel(file_path, workspace),
        "input_relative_path": file_path.relative_to(input_path).as_posix(),
        "name": file_path.name,
        "role": role,
        "status": status,
    }
    if markdown_path is not None:
        record["markdown_path"] = safe_rel(markdown_path, workspace)
    if reason:
        record["reason"] = reason
    if detail:
        record["detail"] = detail
    return record

