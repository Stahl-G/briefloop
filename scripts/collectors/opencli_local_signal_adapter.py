#!/usr/bin/env python3
"""OpenCLI Local Signal Adapter — process local materials into source-like records.

This adapter reads collector_tasks.json and user-authorized raw local materials,
then optionally calls OpenCLI if installed to extract text from screenshots,
audio, or PDF files.

Important boundaries:
- OpenCLI is a local evidence processor, NOT a social-media crawler.
- It does NOT open websites, bypass login walls, or scrape platforms.
- It only processes local files already provided by the user.

Usage:
    python scripts/collectors/opencli_local_signal_adapter.py \
        --tasks output/intermediate/collector_tasks.json \
        --samples input/local_signal_samples.jsonl \
        --raw-dir input/local_signal_raw \
        --output-format source_items
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_collector_tasks(path: Path) -> list[dict[str, Any]]:
    """Load collector_tasks.json."""
    if not path.exists():
        print(f"[adapter] collector_tasks.json not found at {path}", file=sys.stderr)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("tasks", [])


def load_samples(path: Path) -> list[dict[str, Any]]:
    """Load local_signal_samples.jsonl."""
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return samples


def check_opencli_available() -> bool:
    """Check if OpenCLI is installed and available."""
    try:
        result = subprocess.run(
            ["opencli", "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def process_image_with_opencli(image_path: Path, prompt: str) -> dict[str, Any] | None:
    """Extract text from image using OpenCLI i2t command."""
    try:
        result = subprocess.run(
            [
                "opencli", "i2t",
                "-m", "mlx-community/Qwen3-VL-4B-Instruct-4bit",
                "-i", str(image_path),
                "-p", prompt,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return {"text": result.stdout.strip(), "tool": "opencli i2t"}
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def process_audio_with_opencli(audio_path: Path) -> dict[str, Any] | None:
    """Extract text from audio using OpenCLI asr command."""
    try:
        result = subprocess.run(
            [
                "opencli", "asr",
                "-m", "mlx-community/Qwen3-ASR-0.6B-4bit",
                "-i", str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return {"text": result.stdout.strip(), "tool": "opencli asr"}
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def scan_raw_directory(raw_dir: Path) -> list[dict[str, Any]]:
    """Scan raw directory for processable files."""
    if not raw_dir.exists():
        return []

    files = []
    for subdir in ["screenshots", "audio", "text_exports", "pdfs"]:
        subdir_path = raw_dir / subdir
        if subdir_path.exists():
            for f in subdir_path.iterdir():
                if f.is_file():
                    files.append({
                        "path": str(f),
                        "category": subdir,
                        "name": f.name,
                        "suffix": f.suffix.lower(),
                    })
    return files


def process_raw_files(
    raw_files: list[dict[str, Any]],
    opencli_available: bool,
) -> list[dict[str, Any]]:
    """Process raw files into extracted text records."""
    records = []

    for file_info in raw_files:
        path = Path(file_info["path"])
        category = file_info["category"]
        extracted = None

        if category == "screenshots" and opencli_available:
            prompt = (
                "Extract visible product review text, platform name, language, "
                "and consumer pain points. Return JSON."
            )
            extracted = process_image_with_opencli(path, prompt)
        elif category == "audio" and opencli_available:
            extracted = process_audio_with_opencli(path)
        elif category == "text_exports":
            # Read text files directly
            try:
                text = path.read_text(encoding="utf-8")
                extracted = {"text": text, "tool": "direct_read"}
            except Exception:
                continue
        elif category == "pdfs":
            # PDFs would need OpenCLI or external tool
            if opencli_available:
                # TODO: Add OpenCLI PDF extraction when available
                pass
            continue

        if extracted and extracted.get("text"):
            records.append({
                "raw_file": str(path),
                "category": category,
                "extracted_text": extracted["text"],
                "extraction_tool": extracted["tool"],
            })

    return records


def build_source_items(
    extracted_records: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert extracted records and samples to source-like items."""
    source_items = []
    now = datetime.now(timezone.utc).isoformat()

    # From extracted records
    for record in extracted_records:
        # Try to match to a task by raw_file path
        matching_sample = None
        for sample in samples:
            if sample.get("raw_file") == record["raw_file"]:
                matching_sample = sample
                break

        task_id = matching_sample.get("task_id", "") if matching_sample else ""
        platform = matching_sample.get("platform", "unknown") if matching_sample else "unknown"
        market = matching_sample.get("market", "unknown") if matching_sample else "unknown"
        language = matching_sample.get("language", "en") if matching_sample else "en"

        source_items.append({
            "title": f"Local signal sample: {platform} {market}",
            "content": record["extracted_text"][:2000],
            "url": "",
            "published_at": now[:10],
            "metadata": {
                "source_family": "local_signal",
                "collector": "opencli",
                "collector_task_id": task_id,
                "platform": platform,
                "market": market,
                "language": language,
                "signal_type": "consumer_discussion",
                "sample_type": record["category"],
                "access_level": "user_authorized",
                "contains_personal_data": False,
                "raw_file": record["raw_file"],
                "extraction_tool": record["extraction_tool"],
            },
        })

    # From samples (text excerpts)
    for sample in samples:
        text_excerpt = sample.get("text_excerpt", "")
        if not text_excerpt:
            continue

        source_items.append({
            "title": f"Local signal sample: {sample.get('platform', 'unknown')} {sample.get('market', 'unknown')}",
            "content": text_excerpt,
            "url": "",
            "published_at": sample.get("collected_at", now)[:10],
            "metadata": {
                "source_family": "local_signal",
                "collector": sample.get("collector", "unknown"),
                "collector_task_id": sample.get("task_id", ""),
                "platform": sample.get("platform", ""),
                "market": sample.get("market", ""),
                "language": sample.get("language", ""),
                "signal_type": sample.get("signal_type", "consumer_discussion"),
                "sample_type": sample.get("sample_type", "text_export"),
                "access_level": sample.get("access_level", "unknown"),
                "contains_personal_data": False,
                "sample_size": sample.get("sample_size", 0),
                "raw_file": sample.get("raw_file", ""),
                "collector_notes": sample.get("collector_notes", ""),
            },
        })

    return source_items


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenCLI Local Signal Adapter — process local materials into source records."
    )
    parser.add_argument("--tasks", required=True, help="Path to collector_tasks.json")
    parser.add_argument("--samples", required=True, help="Path to local_signal_samples.jsonl")
    parser.add_argument("--raw-dir", default="input/local_signal_raw", help="Path to raw materials directory")
    parser.add_argument("--output-format", default="source_items", choices=["source_items", "full"],
                        help="Output format")
    args = parser.parse_args()

    tasks_path = Path(args.tasks)
    samples_path = Path(args.samples)
    raw_dir = Path(args.raw_dir)

    # Load inputs
    tasks = load_collector_tasks(tasks_path)
    samples = load_samples(samples_path)

    if not tasks and not samples:
        print("[adapter] No tasks or samples found. Nothing to process.", file=sys.stderr)
        print(json.dumps([]))
        return 0

    # Check OpenCLI availability
    opencli_available = check_opencli_available()
    if not opencli_available:
        print("[adapter] OpenCLI not installed. Processing samples only.", file=sys.stderr)

    # Scan and process raw files
    raw_files = scan_raw_directory(raw_dir)
    extracted_records = process_raw_files(raw_files, opencli_available)

    # Build source items
    source_items = build_source_items(extracted_records, samples, tasks)

    # Output
    if args.output_format == "source_items":
        print(json.dumps(source_items, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "tasks_processed": len(tasks),
            "samples_processed": len(samples),
            "raw_files_scanned": len(raw_files),
            "records_extracted": len(extracted_records),
            "source_items": source_items,
        }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
