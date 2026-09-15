"""B-arm source registration for Track A (protocol v2.0).

The JSON twin of register_pdf.py: a document becomes citable evidence only
after its SHA-256 and shape (element/page counts) are registered against the
episode workspace.  Mirrors the strict-mode provenance chain but for the
shared parsed corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    src = Path(args.json_path).expanduser().resolve()
    if not src.is_file():
        print(f"not a file: {src}")
        return 2
    data = json.loads(src.read_text(encoding="utf-8"))
    doc = data.get("document") or {}
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    entry = {
        "kind": "parsed-json",
        "name": src.name,
        "sha256": digest,
        "elements": len(doc.get("elements") or []),
        "pages": len(doc.get("pages") or []),
    }
    ws = Path(args.workspace).expanduser().resolve()
    (ws / "registered-sources.jsonl").open("a", encoding="utf-8").write(
        json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
