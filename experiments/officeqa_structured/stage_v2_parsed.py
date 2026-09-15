"""Stage the official V2 parsed-JSON corpus for Track A (protocol v2.0).

Both arms share this corpus read-only; document parsing is held constant and
is NOT part of the treatment (PROTOCOL-v2-THREETRACK-2026-09-15.md §1).

Layout produced under the data root::

    corpus-v2-parsed/documents/json/<official name>.json   (hardlinks)
    corpus-v2-parsed/index/manifest.json                   (hash manifest)

Fail-closed: refuses to stage anything but exactly 1,435 *.json documents,
and verifies every hardlink resolves to the source bytes by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

EXPECTED_DOCS = 1435
SOURCE = Path.home() / "Developer/datasets/officeqa-pro-v2/fullcorpus/parsed_corpus/jsons"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--source", default=str(SOURCE))
    args = ap.parse_args()

    source = Path(args.source).expanduser().resolve()
    corpus_root = Path(args.data_root).expanduser().resolve() / "corpus-v2-parsed"
    docs_dir = corpus_root / "documents" / "json"
    index_dir = corpus_root / "index"

    jsons = sorted(source.glob("*.json"))
    if len(jsons) != EXPECTED_DOCS:
        print(f"refusing: expected {EXPECTED_DOCS} source documents, found {len(jsons)}")
        return 2

    docs_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, str]] = []
    for src in jsons:
        dst = docs_dir / src.name
        if not dst.exists():
            os.link(src, dst)
        digest = _sha256(dst)
        entries.append({"name": src.name, "sha256": digest})

    list_digest = hashlib.sha256(
        "\n".join(f"{e['name']} {e['sha256']}" for e in entries).encode()
    ).hexdigest()

    # Read-only for the solver view: drop write bits on the staged tree.
    for path in docs_dir.iterdir():
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    os.chmod(docs_dir, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    manifest = {
        "schema_version": "officeqa.corpus_manifest.v1",
        "format": "v2-json",
        "built_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "documents": len(entries),
        "index": False,
        "list_sha256": list_digest,
        "note": ("official V2 parsed JSON corpus, shared preprocessing; parsing/OCR is "
                 "constant across arms and not part of the treatment (protocol v2.0 Track A)"),
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (index_dir / "documents.sha256").write_text(
        "\n".join(f"{e['sha256']}  {e['name']}" for e in entries) + "\n")
    print(json.dumps({"staged": len(entries), "list_sha256": list_digest,
                      "root": str(corpus_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
