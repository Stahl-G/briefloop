# setup.ps1 - Windows PowerShell setup for multi-agent-brief-workflow
# Run from the repository root:
#   .\scripts\setup.ps1
# If your policy blocks scripts:
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== multi-agent-brief-workflow setup ===" -ForegroundColor Cyan

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Candidate
    )

    try {
        $version = & $Candidate.File @($Candidate.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            return $version.Trim()
        }
    } catch {
        return $null
    }
    return $null
}

function New-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$File,
        [string[]]$Args = @(),
        [string]$Label = ""
    )

    if (-not $Label) {
        $Label = if ($Args.Count -gt 0) { "$File $($Args -join ' ')" } else { $File }
    }
    [pscustomobject]@{
        File = $File
        Args = $Args
        Label = $Label
    }
}

function Find-Python {
    $candidates = @(
        (New-PythonCandidate -File "py" -Args @("-3") -Label "py -3"),
        (New-PythonCandidate -File "py"),
        (New-PythonCandidate -File "python"),
        (New-PythonCandidate -File "python3")
    )

    $searchPatterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "C:\Program Files\Python*\python.exe",
        "C:\Python*\python.exe",
        "$env:USERPROFILE\.cache\codex-runtimes\*\dependencies\python\python.exe",
        "$env:USERPROFILE\.codex\runtimes\*\python.exe"
    )

    foreach ($pattern in $searchPatterns) {
        Get-Item $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object {
                $candidates += New-PythonCandidate -File $_.FullName -Label $_.FullName
            }
    }

    foreach ($candidate in $candidates) {
        $version = Test-PythonCandidate -Candidate $candidate
        if ($version) {
            return [pscustomobject]@{
                Candidate = $candidate
                Version = $version
            }
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Candidate,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $Candidate.File @($Candidate.Args) @Arguments
}

$pythonInfo = Find-Python

if (-not $pythonInfo) {
    Write-Host ""
    Write-Host "ERROR: Python 3.9+ was not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "PowerShell may be resolving 'python' to the Microsoft Store placeholder." -ForegroundColor Yellow
    Write-Host "Install real Python, then reopen PowerShell:" -ForegroundColor Yellow
    Write-Host "  winget install Python.Python.3.12" -ForegroundColor White
    Write-Host "or download from:" -ForegroundColor Yellow
    Write-Host "  https://www.python.org/downloads/windows/" -ForegroundColor White
    Write-Host ""
    Write-Host "During python.org install, select 'Add python.exe to PATH'." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$python = $pythonInfo.Candidate
Write-Host "[1/4] Found Python $($pythonInfo.Version): $($python.Label)" -ForegroundColor Green

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvCli = Join-Path $venvDir "Scripts\briefloop.exe"

if (-not (Test-Path $venvDir)) {
    Write-Host "[2/4] Creating virtual environment..." -ForegroundColor Yellow
    & $python.File @($python.Args) -m venv $venvDir
} else {
    Write-Host "[2/4] Virtual environment already exists." -ForegroundColor Green
}

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Virtual environment Python was not created at $venvPython" -ForegroundColor Red
    exit 1
}

Write-Host "[3/4] Installing package and development dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip -q
& $venvPython -m pip install -e ".[dev]" -q

# Verify import works.  Use cmd /c to avoid PowerShell 5.1 ErrorActionPreference
# issues with external command failures.
$importOk = $false
cmd /c "$venvPython -c `"import multi_agent_brief`" 2>nul" | Out-Null
if ($LASTEXITCODE -eq 0) {
    $importOk = $true
}

if (-not $importOk) {
    Write-Host "Editable install did not expose the package (common on macOS with iCloud)." -ForegroundColor Yellow
    Write-Host "Falling back to standard install..." -ForegroundColor Yellow
    & $venvPython -m pip install ".[dev]" -q --force-reinstall
    cmd /c "$venvPython -c `"import multi_agent_brief`" 2>nul" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ERROR: Installation failed. multi_agent_brief cannot be imported." -ForegroundColor Red
        Write-Host "Try: Remove-Item -Recurse -Force .venv; .\scripts\setup.ps1" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "[4/4] Verifying console scripts..." -ForegroundColor Yellow
& $venvPython -m multi_agent_brief.cli.main version
if (-not (Test-Path $venvCli)) {
    Write-Host "ERROR: Console script was not created at $venvCli" -ForegroundColor Red
    exit 1
}
& $venvCli version

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:"
Write-Host "  cd `"$projectRoot`""
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  briefloop version"
Write-Host "  briefloop init my-workspace"
Write-Host "  # Add source files to my-workspace/input/"
Write-Host "  briefloop doctor --config my-workspace/config.yaml"
Write-Host "  # Optional: make /briefloop visible in Claude Desktop Code tab"
Write-Host "  briefloop claude install --repo-workdir ."
Write-Host "  Then use /briefloop run my-workspace in Claude Code"
Write-Host ""
Write-Host "If Activate.ps1 is blocked, run this once for your user account:" -ForegroundColor Yellow
Write-Host "  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned" -ForegroundColor White
