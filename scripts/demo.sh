#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mabw-demo.XXXXXX")"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[demo] python3 is required. Set PYTHON=/path/to/python if needed." >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
CLI=("$PYTHON_BIN" -m multi_agent_brief.cli.main)

echo "[demo] MABW public-safe control-surface demo"
echo "[demo] repo: $ROOT"
echo "[demo] no network, no LLM, no private fixtures"

echo
echo "[demo] validating packaged evaluation cases"
"${CLI[@]}" eval-cases validate --json > "$TMP_DIR/eval-validate.json"

echo "[demo] running packaged evaluation cases"
"${CLI[@]}" eval-cases run --repo-workdir "$ROOT" --json > "$TMP_DIR/eval-run.json"

"$PYTHON_BIN" - "$TMP_DIR/eval-run.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"[demo] eval ok: {payload.get('ok')}")
print(f"[demo] passed: {payload.get('passed_count')} / {payload.get('case_count')}")

target_ids = {
    "unapproved_entry_not_materialized",
    "approved_guidance_materialized",
    "reverted_entry_removed_from_next_snapshot",
}
for case in payload.get("results", []):
    case_id = case.get("case_id")
    if case_id in target_ids:
        status = "passed" if case.get("passed") else "failed"
        print(f"[demo] improvement case {case_id}: {status}")

if not payload.get("ok"):
    raise SystemExit(1)
PY

echo
echo "[demo] manifest assertions use materialized_entry_ids:"
grep -n "materialized_entry_ids" \
  "$ROOT/src/multi_agent_brief/evaluation_cases/fixtures/manifest.yaml" |
  sed 's/^/[demo] /'

echo
echo "[demo] complete: deterministic control behavior demonstrated; no output-quality claim made."
