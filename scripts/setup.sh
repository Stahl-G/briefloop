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
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            PYTHON="$cmd"
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
VENV_CLI=".venv/bin/briefloop"

# 3. Install package — try editable first, fall back to standard install
echo "[3/4] Installing package..."
"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install -e ".[dev]" -q

# Verify the import works; on macOS with iCloud, .pth files can be marked
# hidden (UF_HIDDEN), causing Python to skip them.  Fall back to a standard
# (non-editable) install in that case.
if ! "$VENV_PYTHON" -c "import multi_agent_brief" >/dev/null 2>&1; then
    echo "[3/4] Editable install did not expose the package (common on macOS with iCloud)."
    echo "       Falling back to standard install..."
    "$VENV_PYTHON" -m pip install ".[dev]" -q --force-reinstall
fi

# Final verification — if import still fails, bail out with a clear message.
if ! "$VENV_PYTHON" -c "import multi_agent_brief" >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Installation failed. multi_agent_brief cannot be imported."
    echo ""
    echo "Possible causes:"
    echo "  - macOS iCloud Drive marking .pth files as hidden"
    echo "  - Python version incompatibility (need 3.9+)"
    echo "  - Corrupted virtual environment"
    echo ""
    echo "Try:"
    echo "  rm -rf .venv && bash scripts/setup.sh"
    exit 1
fi

# 4. Verify CLI entry point
echo "[4/4] Verifying installation..."
"$VENV_PYTHON" -m multi_agent_brief.cli.main version
"$VENV_CLI" version

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  source .venv/bin/activate"
echo "  briefloop init my-workspace"
echo "  # Add source files to my-workspace/input/"
echo "  briefloop doctor --config my-workspace/config.yaml"
echo "  # Optional: make /briefloop visible in Claude Desktop Code tab"
echo "  briefloop claude install --repo-workdir ."
echo "  Then use /briefloop run my-workspace in Claude Code"
