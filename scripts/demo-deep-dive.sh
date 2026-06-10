#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mabw-demo-deep-dive.XXXXXX")"
WORKSPACE="$TMP_DIR/workspace"

cleanup() {
  if [[ "${KEEP_MABW_DEMO:-0}" == "1" ]]; then
    echo "[demo-deep-dive] keeping workspace: $WORKSPACE"
    return
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[demo-deep-dive] python3 is required. Set PYTHON=/path/to/python if needed." >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
CLI=("$PYTHON_BIN" -m multi_agent_brief.cli.main)

echo "[demo-deep-dive] MABW Improvement Memory deep dive"
echo "[demo-deep-dive] repo: $ROOT"
echo "[demo-deep-dive] workspace: $WORKSPACE"
echo "[demo-deep-dive] no network, no LLM, no private fixtures"

"${CLI[@]}" init "$WORKSPACE" --demo --force >/dev/null

echo
echo "[demo-deep-dive] proposing synthetic human-authored audience guidance"
"${CLI[@]}" improve propose \
  --workspace "$WORKSPACE" \
  --guidance "Start with the decision-relevant implication before implementation detail when evidence supports it." \
  --category structure \
  --scope brief \
  --source-summary "Synthetic public demo preference." \
  --json > "$TMP_DIR/propose.json"

ENTRY_ID="$("$PYTHON_BIN" - "$TMP_DIR/propose.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
entry = payload.get("entry") or payload.get("current") or {}
entry_id = payload.get("entry_id") or entry.get("entry_id")
if not entry_id:
    raise SystemExit("Unable to find entry_id in improve propose output.")
print(entry_id)
PY
)"
echo "[demo-deep-dive] proposed entry: $ENTRY_ID"

echo "[demo-deep-dive] approving guidance"
"${CLI[@]}" improve approve \
  --workspace "$WORKSPACE" \
  --entry-id "$ENTRY_ID" \
  --by demo_operator \
  --json > "$TMP_DIR/approve.json"

echo "[demo-deep-dive] validating ledger and rebuilding projection"
"${CLI[@]}" improve validate --workspace "$WORKSPACE" --json > "$TMP_DIR/validate.json"
"${CLI[@]}" improve rebuild --workspace "$WORKSPACE" --json > "$TMP_DIR/rebuild.json"

echo "[demo-deep-dive] preparing runtime handoff"
"${CLI[@]}" run --workspace "$WORKSPACE" --skip-doctor >/dev/null

"$PYTHON_BIN" - "$WORKSPACE" <<'PY'
import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
manifest_path = workspace / "output" / "intermediate" / "runtime_manifest.json"
handoff_path = workspace / "output" / "intermediate" / "agent_handoff.json"
snapshot_rel = "output/intermediate/improvement_memory_snapshot.md"

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
improvement = manifest.get("improvement") or {}
handoff_text = json.dumps(handoff, ensure_ascii=False, sort_keys=True)

print("[demo-deep-dive] runtime_manifest.json.improvement:")
print(json.dumps(improvement, ensure_ascii=False, indent=2, sort_keys=True))

if not improvement.get("materialized_entry_ids"):
    raise SystemExit("Expected materialized_entry_ids in manifest improvement block.")
if improvement.get("snapshot_path") != snapshot_rel:
    raise SystemExit(f"Expected snapshot_path {snapshot_rel!r}.")
if snapshot_rel not in handoff_text:
    raise SystemExit("Handoff does not reference frozen improvement snapshot.")
if "improvement/memory.md" in handoff_text:
    raise SystemExit("Handoff must not expose live improvement/memory.md.")

snapshot = workspace / snapshot_rel
if not snapshot.exists():
    raise SystemExit("Frozen improvement snapshot was not created.")

print("[demo-deep-dive] handoff exposes frozen snapshot only.")
print("[demo-deep-dive] snapshot:", snapshot)
PY

echo
echo "[demo-deep-dive] complete: approved guidance was materialized into a frozen runtime snapshot."
echo "[demo-deep-dive] this demonstrates control behavior, not model output-quality improvement."
