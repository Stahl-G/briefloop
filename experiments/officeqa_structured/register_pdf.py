"""Register a used PDF as the BriefLoop episode run's source (arm B only).

Strict mode gives both arms the same raw PDF directory (§6.1 parity); the
only asymmetry is BriefLoop's own discipline: documents an answer relies on
must be registered with provenance before they can carry evidence.  This
helper copies the PDF into the episode workspace sources/ and records
sha256 + page count (pdfinfo).  Arm A never runs it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", help="path to the PDF actually used")
    parser.add_argument("--workspace", required=True, help="episode workspace root")
    args = parser.parse_args(argv)
    src = Path(args.pdf).expanduser().resolve()
    if not src.is_file() or src.suffix.lower() != ".pdf":
        print(json.dumps({"error": "not a PDF file", "path": str(src)}, ensure_ascii=False))
        return 2
    ws = Path(args.workspace).expanduser().resolve()
    sources = ws / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    dst = sources / src.name
    if not dst.exists():
        shutil.copy2(src, dst)
    payload = dst.read_bytes()
    pages = None
    info = subprocess.run(["pdfinfo", str(dst)], capture_output=True, text=True)
    for line in info.stdout.splitlines():
        if line.startswith("Pages:"):
            pages = int(line.split(":", 1)[1].strip())
    record = {"file": src.name, "sha256": hashlib.sha256(payload).hexdigest(),
              "bytes": len(payload), "pages": pages, "registered_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ")}
    (sources / (src.stem + ".provenance.json")).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"registered": record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
