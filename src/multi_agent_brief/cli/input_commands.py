"""inputs — workspace input classification and governance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

# Known input subdirectory → classification role
DIR_ROLES: dict[str, str] = {
    "sources": "evidence",
    "feedback": "feedback",
    "instructions": "instruction",
    "context": "context",
}

# Non-evidence input subdirectories that ManualProvider must block
NON_EVIDENCE_SUBDIRS = {"feedback", "instructions", "context"}

# Recognised text-file extensions for evidence content
RECOGNISED_EVIDENCE_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}

# Non-text documents that can be converted to Markdown before classification
MINERU_EXTRACTABLE_SUFFIXES = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
}

# All content files we attempt to classify (including non-text for skipped records)
SCANNABLE_SUFFIXES = RECOGNISED_EVIDENCE_SUFFIXES | MINERU_EXTRACTABLE_SUFFIXES

# ---------------------------------------------------------------------------
# Artifact hygiene — file names / patterns that signal old output artefacts
# ---------------------------------------------------------------------------

SUSPICIOUS_FILENAMES: set[str] = {
    "brief.md", "audited_brief.md", "reader_brief.md",
    "audit_report.json", "finalize_report.json",
    "claim_ledger.json", "candidate_claims.json", "screened_candidates.json",
    "input_classification.json", "classification.json",
    "run_manifest.json", "source_map.md", "source_coverage_report.json",
    "analysis_blocks.json", "rendered_output_report.json",
    "final_audit_report.json", "final_clean_report.json", "editor_draft.md",
    "analyst_draft.md", "draft_brief.md",
}

SUSPICIOUS_SUFFIX_PATTERNS: tuple[str, ...] = (
    "_output.md", "_final.md", "_audit.md", "_audited.md",
    "_reviewed.md", "_commented.md",
)

SUSPICIOUS_CONTENT_MARKERS: tuple[str, ...] = (
    "[src:", "audit_status", "claim_id", "CLAIM_ID",
    "Reader brief", "Internal [src:", "Finalized report",
    "Audit report", "Evidence ledger",
)

# ---------------------------------------------------------------------------
# Feedback / instruction / context detection by filename keyword
# ---------------------------------------------------------------------------

FEEDBACK_KEYWORDS: tuple[str, ...] = (
    "feedback", "comment", "comments", "annotated",
    "review", "revision",
    "批注", "修改意见", "反馈",
)

INSTRUCTION_KEYWORDS: tuple[str, ...] = (
    "instruction", "instructions", "prompt",
    "requirements", "briefing_request",
    "任务要求", "写作要求",
)

CONTEXT_KEYWORDS: tuple[str, ...] = (
    "context", "background", "company_profile", "profile",
    "背景", "公司介绍",
)


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the inputs subcommand group."""
    inputs_parser = subparsers.add_parser(
        "inputs", help="Input file classification and governance."
    )
    inputs_sub = inputs_parser.add_subparsers(
        dest="inputs_action", required=True
    )

    classify_parser = inputs_sub.add_parser(
        "classify",
        help="Classify input/ files by role (evidence / feedback / instruction / context).",
    )
    classify_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    classify_parser.add_argument(
        "--output",
        help="Output JSON path (default: <output.path>/input_classification.json).",
    )
    classify_parser.add_argument(
        "--quiet", action="store_true", help="Suppress summary output."
    )

    extract_parser = inputs_sub.add_parser(
        "extract",
        help=(
            "Convert PDF/DOCX/image files under input/ to Markdown using MinerU"
            " before classification."
        ),
    )
    extract_parser.add_argument(
        "--config", required=True, help="Path to workspace config.yaml."
    )
    extract_parser.add_argument(
        "--backend",
        default="pipeline",
        help="MinerU backend for local CLI mode. Default: pipeline.",
    )
    extract_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate .mineru.md files even when they already exist.",
    )
    extract_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List extractable input files without running MinerU or writing Markdown.",
    )
    extract_parser.add_argument(
        "--output",
        help="Output JSON path (default: <output.path>/input_extraction_report.json).",
    )
    extract_parser.add_argument(
        "--quiet", action="store_true", help="Suppress summary output."
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch inputs subcommands."""
    if args.inputs_action == "classify":
        return _handle_classify(args)
    if args.inputs_action == "extract":
        return _handle_extract(args)
    print(f"[inputs] Unknown action: {args.inputs_action}", flush=True)
    return 1


# ---------------------------------------------------------------------------
# Classify command
# ---------------------------------------------------------------------------

def _handle_classify(args: argparse.Namespace) -> int:
    """Classify input/ files by role."""
    resolved = _resolve_workspace_paths(args.config)
    if resolved.get("error"):
        print(resolved["error"], flush=True)
        return 1
    input_path = resolved["input_path"]
    output_path = Path(args.output) if args.output else resolved["output_dir"] / "input_classification.json"
    if args.output:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    classified = _classify_input_dir(input_path)

    payload = json.dumps(classified, ensure_ascii=False, indent=2)
    output_path.write_text(payload, encoding="utf-8")

    if not args.quiet:
        _print_summary(classified)

    print(f"[inputs] Classification written: {output_path}", flush=True)
    return 0


def _handle_extract(args: argparse.Namespace) -> int:
    """Extract binary input documents into Markdown with MinerU."""
    resolved = _resolve_workspace_paths(args.config)
    if resolved.get("error"):
        print(resolved["error"], flush=True)
        return 1

    input_path = resolved["input_path"]
    output_dir = resolved["output_dir"]
    report_path = Path(args.output) if args.output else output_dir / "input_extraction_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report = extract_input_documents(
        input_path=input_path,
        workspace=resolved["workspace"],
        output_dir=output_dir,
        backend=str(args.backend or "pipeline"),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not args.quiet:
        _print_extraction_summary(report)

    print(f"[inputs] Extraction report written: {report_path}", flush=True)
    return 1 if report.get("status") in {"failed", "completed_with_errors"} else 0


def _resolve_workspace_paths(config: str | Path) -> dict[str, Any]:
    """Resolve workspace, input path, and output dir from config.yaml."""
    config_path = Path(config).resolve()
    if not config_path.exists():
        return {"error": f"[inputs] config.yaml not found: {config_path}"}

    workspace = config_path.parent

    # ── load config to read input.path / output.path ──
    from multi_agent_brief.core.config import load_config
    try:
        cfg = load_config(config_path)
    except Exception as exc:
        return {"error": f"[inputs] Failed to read config: {exc}"}

    input_cfg = cfg.get("input", {}) or {}
    output_cfg = cfg.get("output", {}) or {}

    raw_input_path = input_cfg.get("path", "input")
    input_path = Path(raw_input_path)
    if not input_path.is_absolute():
        input_path = workspace / input_path

    if not input_path.is_dir():
        return {"error": f"[inputs] No input directory found: {input_path}"}

    raw_output_path = output_cfg.get("path", "output")
    output_dir = Path(raw_output_path)
    if not output_dir.is_absolute():
        output_dir = workspace / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    return {
        "workspace": workspace,
        "input_path": input_path,
        "output_dir": output_dir,
    }


# ---------------------------------------------------------------------------
# Core classification logic
# ---------------------------------------------------------------------------

def _classify_input_dir(input_dir: Path) -> dict[str, Any]:
    """Scan input_dir and classify every content file.

    Returns a dict with keys:
      "evidence", "feedback", "instruction", "context", "skipped"
    """
    evidence: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    instruction: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for entry in sorted(input_dir.iterdir()):
        if entry.name.startswith("."):
            continue

        if entry.is_dir():
            role = DIR_ROLES.get(entry.name)
            if role is None:
                # Unknown directory — record all files inside as skipped
                for item in _scan_dir_entries(entry):
                    item["reason"] = "unknown_input_subdir"
                    item["suggested_role"] = "unknown"
                    skipped.append(item)
                continue

            for item in _scan_dir_entries(entry):
                _assign_by_role(role, item, evidence, feedback, instruction, context, skipped)

        elif entry.is_file():
            # Root-level files — backward-compatible, but run hygiene check
            _classify_root_file(entry, evidence, skipped)

    return {
        "evidence": evidence,
        "feedback": feedback,
        "instruction": instruction,
        "context": context,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# MinerU extraction logic
# ---------------------------------------------------------------------------

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
        "input_path": _safe_rel(input_path, workspace),
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

        target_path = _extracted_markdown_path(file_path)
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
                input_path=input_path,
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


def _extracted_markdown_path(path: Path) -> Path:
    suffix_tag = path.suffix.lower().replace(".", "_")
    return path.with_name(f"{path.stem}{suffix_tag}.mineru.md")


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
    input_path: Path,
    workspace: Path,
    role: str,
    backend: str,
    extracted_markdown: str,
) -> str:
    original_rel = _safe_rel(file_path, workspace)
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
        "path": _safe_rel(file_path, workspace),
        "input_relative_path": file_path.relative_to(input_path).as_posix(),
        "name": file_path.name,
        "role": role,
        "status": status,
    }
    if markdown_path is not None:
        record["markdown_path"] = _safe_rel(markdown_path, workspace)
    if reason:
        record["reason"] = reason
    if detail:
        record["detail"] = detail
    return record


def _safe_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def _scan_dir_entries(subdir: Path) -> list[dict[str, Any]]:
    """Return metadata dicts for every scannable file one level deep under subdir."""
    items: list[dict[str, Any]] = []
    for f in sorted(subdir.iterdir()):
        if f.name.startswith(".") or f.name.lower() == "readme.md":
            continue
        if f.is_dir():
            # Nested directory — record as skipped
            items.append({
                "path": str(f),
                "name": f.name + "/",
                "bytes": 0,
                "reason": "nested_directory_not_supported",
            })
            continue
        try:
            size = f.stat().st_size
        except OSError:
            items.append({
                "path": str(f),
                "name": f.name,
                "bytes": 0,
                "reason": "unreadable",
            })
            continue
        items.append({"path": str(f), "name": f.name, "bytes": size})
    return items


def _classify_root_file(entry: Path, evidence: list, skipped: list) -> None:
    """Classify a file in the input/ root — legacy path with hygiene checks."""
    name_lower = entry.name.lower()

    # 1. Artifact hygiene — suspicious filename patterns
    if name_lower in SUSPICIOUS_FILENAMES:
        skipped.append(_skip_item(entry, "suspicious_output_artifact"))
        return

    for pattern in SUSPICIOUS_SUFFIX_PATTERNS:
        if name_lower.endswith(pattern):
            skipped.append(_skip_item(entry, "suspicious_output_artifact"))
            return

    # 2. Check for keyword-based role override
    override_role = _keyword_role(entry.name)
    if override_role in ("feedback", "instruction", "context"):
        skipped.append(_skip_item(entry, f"filename_suggests_{override_role}",
                                  suggested_role=override_role))
        return

    # 3. Check extension support
    if entry.suffix.lower() not in RECOGNISED_EVIDENCE_SUFFIXES:
        if entry.suffix.lower() in MINERU_EXTRACTABLE_SUFFIXES:
            skipped.append(_document_skip_item(entry, "evidence"))
        else:
            skipped.append(_skip_item(entry, "unsupported_extension"))
        return

    # 4. Check content for suspicious markers
    try:
        content = entry.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        skipped.append(_skip_item(entry, "binary_or_unreadable"))
        return

    if _has_suspicious_content(content):
        evidence.append(_content_item(entry, flagged=True))
        # Also note it in skipped as a warning-level finding
        skipped.append({
            **_base_item(entry),
            "reason": "suspicious_content_markers",
            "note": "File classified as evidence but contains markers of old output artefacts; review manually.",
        })
        return

    # 5. Clean — pass as evidence
    evidence.append(_content_item(entry))


def _assign_by_role(role: str, item: dict, evidence: list, feedback: list,
                    instruction: list, context: list, skipped: list) -> None:
    """Route an item from a known subdirectory to the right list."""
    # If item already has a `reason` (from _scan_dir_entries), it goes to skipped
    if "reason" in item:
        skipped.append(item)
        return

    # Check extension for content files
    ext = Path(item["name"]).suffix.lower()
    if ext not in SCANNABLE_SUFFIXES:
        item["reason"] = "unsupported_extension"
        item["suggested_role"] = role
        skipped.append(item)
        return

    if ext not in RECOGNISED_EVIDENCE_SUFFIXES:
        extracted = _extracted_markdown_path(Path(item["path"]))
        item["reason"] = "document_extracted" if extracted.exists() else "needs_document_extraction"
        item["suggested_role"] = role
        item["extract_with"] = "multi-agent-brief inputs extract"
        item["extracted_markdown"] = str(extracted) if extracted.exists() else ""
        skipped.append(item)
        return

    # Artifact-hygiene name check even within subdirs
    name_lower = item["name"].lower()
    if name_lower in SUSPICIOUS_FILENAMES:
        item["reason"] = "suspicious_output_artifact"
        item["suggested_role"] = role
        skipped.append(item)
        return

    # Content check (only for non-evidence roles — evidence files skip this
    # since suspicious content detection in subdirs is less reliable)
    if role != "evidence":
        try:
            content = Path(item["path"]).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            item["reason"] = "binary_or_unreadable"
            item["suggested_role"] = role
            skipped.append(item)
            return

        if _has_suspicious_content(content):
            item["reason"] = "suspicious_content_markers"
            item["suggested_role"] = role
            skipped.append(item)
            return

    # Route to final list
    target = {"evidence": evidence, "feedback": feedback,
              "instruction": instruction, "context": context}.get(role)
    if target is not None:
        target.append(item)


def _base_item(entry: Path) -> dict[str, Any]:
    try:
        size = entry.stat().st_size
    except OSError:
        size = 0
    return {"path": str(entry), "name": entry.name, "bytes": size}


def _content_item(entry: Path, flagged: bool = False) -> dict[str, Any]:
    item = _base_item(entry)
    if flagged:
        item["flagged"] = True
    return item


def _skip_item(entry: Path, reason: str, suggested_role: str = "") -> dict[str, Any]:
    item = _base_item(entry)
    item["reason"] = reason
    if suggested_role:
        item["suggested_role"] = suggested_role
    return item


def _document_skip_item(entry: Path, suggested_role: str) -> dict[str, Any]:
    item = _skip_item(entry, "needs_document_extraction", suggested_role=suggested_role)
    extracted = _extracted_markdown_path(entry)
    if extracted.exists():
        item["reason"] = "document_extracted"
        item["extracted_markdown"] = str(extracted)
    else:
        item["extract_with"] = "multi-agent-brief inputs extract"
    return item


def _keyword_role(filename: str) -> str | None:
    """Detect role from filename keywords."""
    name_lower = filename.lower()
    for kw in FEEDBACK_KEYWORDS:
        if kw in name_lower:
            return "feedback"
    for kw in INSTRUCTION_KEYWORDS:
        if kw in name_lower:
            return "instruction"
    for kw in CONTEXT_KEYWORDS:
        if kw in name_lower:
            return "context"
    return None


def _has_suspicious_content(content: str) -> bool:
    """Check if content contains markers suggesting it's an old output artifact."""
    if not content:
        return False
    # Quick scan — first 2000 chars are usually enough
    head = content[:2000].lower() if len(content) > 2000 else content.lower()
    for marker in SUSPICIOUS_CONTENT_MARKERS:
        if marker.lower() in head:
            return True
    return False


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

def _print_summary(classified: dict[str, Any]) -> None:
    """Print a human-readable summary."""
    print()
    print("=== Input Classification Summary ===")
    for role, label in [
        ("evidence", "📄 Evidence (claims-eligible)"),
        ("feedback", "✏️  Feedback (editorial direction)"),
        ("instruction", "📋 Instructions (task guidance)"),
        ("context", "📎 Context (background reference)"),
    ]:
        files = classified.get(role, [])
        count = len(files)
        print(f"  {label}: {count} file(s)")
        for f in files:
            note = ""
            if f.get("flagged"):
                note = " ⚠️ (suspicious content markers)"
            print(f"    - {f['name']}{note}")

    skipped_files = classified.get("skipped", [])
    skipped_count = len(skipped_files)
    print(f"  ⏭️  Skipped: {skipped_count} file(s)")
    if skipped_count > 0:
        for f in skipped_files:
            reason = f.get("reason", "unknown")
            suggested = f" → {f['suggested_role']}" if f.get("suggested_role") else ""
            print(f"    - {f['name']}  ({reason}{suggested})")
    print()


def _print_extraction_summary(report: dict[str, Any]) -> None:
    """Print a human-readable extraction summary."""
    print()
    print("=== Input Document Extraction Summary ===")
    print(f"  Status: {report.get('status', 'unknown')}")
    print(f"  Extracted: {len(report.get('extracted', []))} file(s)")
    for item in report.get("extracted", []):
        print(f"    - {item.get('input_relative_path')} -> {item.get('markdown_path')}")
    print(f"  Skipped: {len(report.get('skipped', []))} file(s)")
    for item in report.get("skipped", []):
        reason = item.get("reason") or item.get("status", "unknown")
        print(f"    - {item.get('input_relative_path', item.get('name'))} ({reason})")
    errors = report.get("errors", [])
    if errors:
        print("  Errors:")
        for error in errors:
            print(f"    - {error}")
    print()
