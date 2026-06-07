#!/usr/bin/env bash
# Release script — bump VERSION first, then run this.
# Usage:
#   echo "0.5.9" > VERSION
#   python scripts/bump_version.py
#   bash scripts/release.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VERSION="$(cat VERSION)"
TAG="v$VERSION"

echo "=== Release $TAG ==="

echo "[1/4] Checking version consistency..."
python scripts/check_version_consistency.py

echo "[2/4] Running tests..."
python -m pytest -q

echo "[3/4] Checking git status..."
if ! git diff --exit-code --quiet; then
  echo "ERROR: Uncommitted changes detected. Commit or stash them first."
  exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: Tag $TAG already exists."
  exit 1
fi

echo "[4/4] Creating tag and pushing..."
git tag -a "$TAG" -m "$TAG"
git push origin main
git push origin "$TAG"

echo ""
echo "=== Released $TAG ==="
