#!/usr/bin/env bash
# Publish the committed release from main.
#
# The script reruns bump_version.py as a guard. If that creates changes,
# review and commit them, then rerun the release.
#
# Usage:
#   echo "0.5.9" > VERSION
#   python3 scripts/bump_version.py
#   git add VERSION README.md README.zh-CN.md CHANGELOG.md pyproject.toml src/ .agents/
#   git commit -m "chore(release): v0.5.9"
#   bash scripts/release.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v$VERSION"

echo "=== Release $TAG ==="

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: Python executable not found: $PYTHON"
  echo "Set PYTHON=/path/to/python or install python3."
  exit 1
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]]; then
  echo "ERROR: VERSION must be a semantic version, got '$VERSION'."
  exit 1
fi

echo "[1/7] Checking release branch..."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then
  echo "ERROR: Releases must run from main. Current branch: $BRANCH"
  exit 1
fi

echo "[2/7] Syncing version files..."
"$PYTHON" scripts/bump_version.py

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working tree is not clean after version sync."
  echo "Review, commit, and rerun release.sh."
  git status --short
  exit 1
fi

echo "[3/7] Refreshing origin/main and tags..."
git fetch origin --tags

if ! git merge-base --is-ancestor origin/main HEAD; then
  echo "ERROR: local main does not contain origin/main. Pull or rebase before releasing."
  exit 1
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "ERROR: Local tag $TAG already exists."
  exit 1
fi

if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  echo "ERROR: Remote tag $TAG already exists."
  exit 1
fi

echo "[4/7] Checking version consistency..."
"$PYTHON" scripts/check_version_consistency.py

echo "[5/7] Checking release consistency..."
"$PYTHON" scripts/check_release_consistency.py --no-tag
if [[ "$VERSION" =~ ^1\.0\. ]]; then
  echo "      Checking required v1.0 RC readiness..."
  "$PYTHON" scripts/check_v1_rc_readiness.py --require-satisfied
  echo "      Checking required v1.0 pilot evidence..."
  "$PYTHON" scripts/check_v1_pilot_evidence.py --require-satisfied
fi

echo "[6/7] Running tests..."
"$PYTHON" -m pytest -q

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working tree changed during release checks."
  git status --short
  exit 1
fi

echo "[7/7] Pushing main, tagging, and pushing tag..."
git push origin main

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
LOCAL_HEAD="$(git rev-parse HEAD)"
if [[ "$REMOTE_MAIN" != "$LOCAL_HEAD" ]]; then
  echo "ERROR: origin/main does not match local HEAD after push."
  exit 1
fi

git tag -a "$TAG" -m "$TAG"
git push origin "$TAG"

echo ""
echo "=== Released $TAG ==="
echo "If Homebrew formula metadata is published for this release, update Formula/ with the"
echo "new tag archive checksum after the GitHub tag/archive is available."
