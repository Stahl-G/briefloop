# Windows PowerShell Guide

MABW supports a native Windows PowerShell path. Windows users do not need WSL
or Git Bash to install and run the CLI.

## Supported Environment

- Windows 10 or Windows 11
- Windows PowerShell 5.1 or PowerShell 7
- Python 3.9+
- Git for Windows

PowerShell is the recommended Windows path. WSL is an optional advanced path,
not a requirement. CMD is not the primary supported shell.

## Install Python

The simplest option is:

```powershell
winget install Python.Python.3.12
```

You can also install Python from:

```text
https://www.python.org/downloads/windows/
```

If you use the python.org installer, select `Add python.exe to PATH`, then open
a new PowerShell window.

## Clone And Set Up

```powershell
git clone https://github.com/Stahl-G/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
multi-agent-brief version
```

`scripts/setup.ps1` will:

- find `py -3`, `py`, `python`, `python3`, or common Python install paths;
- require Python 3.9+;
- create `.venv`;
- install `.[dev]`;
- verify `python -m multi_agent_brief.cli.main version`;
- verify `.venv\Scripts\multi-agent-brief.exe version`.

If PowerShell blocks script execution, bypass the policy only for this setup
script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

## Create Your First Brief

The real user path is onboarding, workspace initialization, and runtime handoff:

```powershell
multi-agent-brief onboard
multi-agent-brief init .\mabw-workspace --from-onboarding onboarding.json
multi-agent-brief run --workspace .\mabw-workspace
```

`run` creates the runtime handoff. It does not make Python write a full brief.

## Optional: Inspect The Demo

The demo is for inspecting the control surfaces and evidence chain on synthetic
materials. It is not a required step before using MABW.

```powershell
multi-agent-brief init .\mabw-demo --demo --force
multi-agent-brief run --workspace .\mabw-demo
```

If you use Claude Code from a source clone, you can also install the Claude
writer command:

```powershell
multi-agent-brief claude install --repo-workdir .
```

## Initialize A Workspace Directly

```powershell
multi-agent-brief init my-workspace
```

For non-interactive runs, create `onboarding.json` first and use:

```powershell
multi-agent-brief init my-workspace --from-onboarding onboarding.json
```

## Advanced: Experimental Installer

There is also a Windows installer asset:

```powershell
irm https://raw.githubusercontent.com/Stahl-G/multi-agent-brief-workflow/main/scripts/install.ps1 | iex
```

It is currently listed in the support matrix as an Experimental CLI-only
installer asset. The default README path remains source clone plus
`scripts/setup.ps1`.

## Run Tests

```powershell
python -m pytest -q
```

## Agent Config Check

```powershell
python scripts/generate_agent_configs.py --check
```

To regenerate generated agent files:

```powershell
python scripts/generate_agent_configs.py --write
python scripts/generate_agent_configs.py --check
```

## No-Install Run

PowerShell does not use Bash's `PYTHONPATH=src command` syntax. Use:

```powershell
$env:PYTHONPATH = "src"
python -m multi_agent_brief.cli.main version
Remove-Item Env:PYTHONPATH
```

## Common Issues

### Activate.ps1 cannot be loaded

If PowerShell blocks virtual environment activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then retry:

```powershell
.\.venv\Scripts\Activate.ps1
```

You can also bypass the policy only for setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

### python opens Microsoft Store

This usually means `python` points to the Microsoft Store placeholder instead
of a real Python install.

Install Python and reopen PowerShell:

```powershell
winget install Python.Python.3.12
```

You can also disable Python App execution aliases in Windows Settings.

### python3 is not recognized

On Windows, prefer:

```powershell
python
```

or the Python launcher:

```powershell
py -3
```

Windows examples in this repository use `python`, not `python3`.

### PYTHONPATH=src does not work in PowerShell

`PYTHONPATH=src python ...` is Bash syntax. In PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m multi_agent_brief.cli.main version
Remove-Item Env:PYTHONPATH
```

### Paths With Spaces

Quote paths that contain spaces:

```powershell
cd "C:\Users\you\Documents\multi-agent-brief-workflow"
python scripts/generate_agent_configs.py --check
```

### WSL Is Optional

WSL and Git Bash can run the macOS/Linux Bash commands, but Windows users do
not need WSL to use MABW.

### Git Hook Is Optional

`.githooks/pre-push` is an optional maintainer hook. Regular Windows users do
not need to install it.
