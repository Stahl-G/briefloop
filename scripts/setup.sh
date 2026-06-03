#!/usr/bin/env bash
# Setup script for multi-agent-brief-workflow
# Run this after cloning to get a working environment.
# Windows users: use scripts\setup.ps1 instead.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== multi-agent-brief-workflow setup ==="

# Find Python 3.9+: try python3, python
PYTHON=""
for cmd in python3 python; do
    if command -v $cmd >/dev/null 2>&1 || $cmd --version >/dev/null 2>&1; then
        if $cmd -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.9+ not found."
    echo ""
    echo "Install Python from https://www.python.org/downloads/"
    echo "Or on macOS: brew install python@3.12"
    echo "Or on Ubuntu: sudo apt install python3 python3-venv"
    exit 1
fi

echo "[1/4] Found Python: $PYTHON ($($PYTHON --version 2>&1))"

# 1. Create venv if missing
if [ ! -d ".venv" ]; then
    echo "[2/4] Creating virtual environment..."
    $PYTHON -m venv .venv
else
    echo "[2/4] Virtual environment already exists."
fi

# 2. Activate
source .venv/bin/activate
VENV_PYTHON=".venv/bin/python"
VENV_CLI=".venv/bin/multi-agent-brief"

# 3. Install package in editable mode with dev dependencies
echo "[3/4] Installing package..."
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install -e ".[dev]" -q
if ! "$VENV_PYTHON" -c "import multi_agent_brief" >/dev/null 2>&1; then
    echo "Editable install did not expose the package; falling back to a standard install..."
    "$VENV_PYTHON" -m pip install ".[dev]" -q --force-reinstall
fi

# 4. Verify
echo "[4/4] Verifying installation..."
"$VENV_PYTHON" -m multi_agent_brief.cli.main version
"$VENV_CLI" version

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  source .venv/bin/activate"
echo "  multi-agent-brief init my-workspace --language zh-CN"
echo "  # Add source files to my-workspace/input/"
echo "  multi-agent-brief run --config my-workspace/config.yaml"
