#!/usr/bin/env bash
# User installer for multi-agent-brief-workflow.
# Intended for:
#   curl -fsSL https://raw.githubusercontent.com/Stahl-G/briefloop/main/scripts/install.sh | bash
set -euo pipefail

REPO_URL="${MABW_REPO:-https://github.com/Stahl-G/briefloop}"
REF="${MABW_REF:-main}"
PREFIX="${MABW_PREFIX:-$HOME/.local/share/multi-agent-brief}"
BIN_DIR="${MABW_BIN_DIR:-$HOME/.local/bin}"
WITH_DOCX=1
DRY_RUN=0

usage() {
    cat <<'EOF'
Install multi-agent-brief for the current user.

Usage:
  install.sh [options]

Options:
  --prefix DIR        Install venv under DIR (default: ~/.local/share/multi-agent-brief)
  --bin-dir DIR       Link multi-agent-brief into DIR (default: ~/.local/bin)
  --repo URL          GitHub repository URL (default: upstream project)
  --ref REF           Git ref to install (default: main)
  --without-docx      Skip python-docx optional dependency
  --dry-run           Print planned actions without changing files
  -h, --help          Show this help

Environment overrides:
  MABW_PREFIX, MABW_BIN_DIR, MABW_REPO, MABW_REF, MABW_ARCHIVE_URL, MABW_PACKAGE_SPEC

Examples:
  curl -fsSL https://raw.githubusercontent.com/Stahl-G/briefloop/main/scripts/install.sh | bash
  MABW_REF=v0.3.4 bash scripts/install.sh
  bash scripts/install.sh --prefix "$HOME/.local/share/mabw" --bin-dir "$HOME/.local/bin"
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            PREFIX="${2:?missing value for --prefix}"
            shift 2
            ;;
        --bin-dir)
            BIN_DIR="${2:?missing value for --bin-dir}"
            shift 2
            ;;
        --repo)
            REPO_URL="${2:?missing value for --repo}"
            shift 2
            ;;
        --ref)
            REF="${2:?missing value for --ref}"
            shift 2
            ;;
        --without-docx)
            WITH_DOCX=0
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            echo "" >&2
            usage >&2
            exit 2
            ;;
    esac
done

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

find_python() {
    for cmd in python3 python python3.14 python3.13 python3.12; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
                command -v "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

echo "=== multi-agent-brief user installer ==="

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
    echo ""
    echo "ERROR: Python 3.12+ not found."
    echo ""
    echo "Install Python 3.12+ first:"
    echo "  macOS:  brew install python"
    echo "          (python@3.12 is keg-only; if you use it, run: brew link python@3.12)"
    echo "  Ubuntu 24.04+: sudo apt install python3 python3-venv"
    echo "  Older Ubuntu:  sudo apt install python3.12 python3.12-venv (deadsnakes PPA)"
    echo "  Python: https://www.python.org/downloads/"
    exit 1
fi

BASE_REPO_URL="${REPO_URL%.git}"
BASE_REPO_URL="${BASE_REPO_URL%/}"
ARCHIVE_URL="${MABW_ARCHIVE_URL:-$BASE_REPO_URL/archive/$REF.tar.gz}"
PACKAGE_SPEC="${MABW_PACKAGE_SPEC:-$ARCHIVE_URL}"
VENV_DIR="$PREFIX/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_CLI="$VENV_DIR/bin/multi-agent-brief"
BIN_CLI="$BIN_DIR/multi-agent-brief"

echo "[1/5] Python: $("$PYTHON" --version 2>&1)"
echo "[2/5] Install prefix: $PREFIX"

run mkdir -p "$PREFIX" "$BIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
    echo "[3/5] Creating virtual environment..."
    run "$PYTHON" -m venv "$VENV_DIR"
elif [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
    echo "[3/5] Reusing virtual environment."
else
    echo "[3/5] Recreating virtual environment (existing one is broken or below the Python 3.12 floor)..."
    run rm -rf "$VENV_DIR"
    run "$PYTHON" -m venv "$VENV_DIR"
fi

if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: Virtual environment Python was not created at $VENV_PYTHON" >&2
    exit 1
fi

echo "[4/5] Installing multi-agent-brief from $REF..."
run "$VENV_PYTHON" -m pip install --upgrade pip -q
if [ "$WITH_DOCX" -eq 1 ]; then
    run "$VENV_PYTHON" -m pip install --upgrade "python-docx>=1.0.0" -q
fi
run "$VENV_PYTHON" -m pip install --upgrade --force-reinstall "$PACKAGE_SPEC" -q

echo "[5/5] Linking CLI..."
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -e "$BIN_CLI" ] || [ -L "$BIN_CLI" ]; then
        if [ -L "$BIN_CLI" ]; then
            rm -f "$BIN_CLI"
        else
            echo "ERROR: $BIN_CLI already exists and is not a symlink." >&2
            echo "Remove it or choose a different --bin-dir." >&2
            exit 1
        fi
    fi
    ln -s "$VENV_CLI" "$BIN_CLI"
else
    run ln -s "$VENV_CLI" "$BIN_CLI"
fi

if [ "$DRY_RUN" -eq 0 ]; then
    "$VENV_PYTHON" -m multi_agent_brief.cli.main version >/dev/null
    "$BIN_CLI" version >/dev/null
fi

echo ""
echo "=== Installation complete ==="
echo "CLI: $BIN_CLI"
echo ""
if [ "$DRY_RUN" -eq 0 ]; then
    "$BIN_CLI" version
fi

case ":$PATH:" in
    *":$BIN_DIR:"*)
        echo ""
        echo "Next:"
        echo "  multi-agent-brief init my-workspace"
        ;;
    *)
        echo ""
        echo "$BIN_DIR is not in PATH. Add this to your shell profile:"
        echo "  export PATH=\"$BIN_DIR:\$PATH\""
        echo ""
        echo "Then open a new shell and run:"
        echo "  multi-agent-brief init my-workspace"
        ;;
esac
