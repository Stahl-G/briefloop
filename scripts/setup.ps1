# setup.ps1 - Windows PowerShell setup for multi-agent-brief-workflow
# Run: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Or:  .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== multi-agent-brief-workflow setup ===" -ForegroundColor Cyan

# 1. Find Python (skip Windows Store placeholder)
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        # Verify it's real Python, not the Windows Store stub
        $version = & $cmd --version 2>&1
        if ($version -match "Python 3\.\d+") {
            $python = $cmd
            break
        }
    }
}
if (-not $python) {
    Write-Host "ERROR: Python 3.9+ not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "The 'python' on your system may be a Windows Store placeholder." -ForegroundColor Yellow
    Write-Host "Install real Python from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Or run: winget install Python.Python.3.12" -ForegroundColor Yellow
    Write-Host "Make sure to check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/3] Found Python: $python ($( & $python --version 2>&1 ))" -ForegroundColor Green

# 2. Create venv
if (-not (Test-Path ".venv")) {
    Write-Host "[2/3] Creating virtual environment..." -ForegroundColor Yellow
    & $python -m venv .venv
} else {
    Write-Host "[2/3] Virtual environment already exists." -ForegroundColor Green
}

# 3. Activate and install
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    # Fallback: try relative path from CWD
    $venvPython = ".venv\Scripts\python.exe"
}

Write-Host "[3/3] Installing package..." -ForegroundColor Yellow
& $venvPython -m pip install -e ".[dev]" -q

# 4. Verify
& $venvPython -c "from multi_agent_brief.cli.main import main; print('OK: multi-agent-brief is ready')"

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  multi-agent-brief init my-workspace --language zh-CN"
Write-Host "  # Add source files to my-workspace\input\"
Write-Host "  multi-agent-brief run --config my-workspace\config.yaml"
Write-Host ""
Write-Host "Or run the demo:"
Write-Host "  multi-agent-brief init --demo"
Write-Host "  multi-agent-brief run --config brief-demo\config.yaml"
