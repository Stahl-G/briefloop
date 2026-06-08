# Windows PowerShell 原生指南

本项目支持 Windows 原生 PowerShell 路径，不要求 WSL 或 Git Bash。

## 支持环境

- Windows 10 / Windows 11
- Windows PowerShell 5.1 或 PowerShell 7
- Python 3.9+
- Git for Windows

PowerShell 是 Windows 默认推荐路径。WSL 是可选高级路径，不是必需条件。CMD 不是主要支持目标。

## 安装 Python

推荐任选一种方式：

```powershell
winget install Python.Python.3.12
```

或从 python.org 下载：

```text
https://www.python.org/downloads/windows/
```

使用 python.org 安装器时，请勾选 `Add python.exe to PATH`。安装后重新打开 PowerShell。

## Clone And Setup

```powershell
git clone https://github.com/your-username/multi-agent-brief-workflow.git
cd multi-agent-brief-workflow
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
multi-agent-brief version
```

`scripts/setup.ps1` 会：

- 搜索 `py -3`、`py`、`python`、`python3` 和常见安装路径
- 严格检查 Python 3.9+
- 创建 `.venv`
- 安装 `.[dev]`
- 验证 `python -m multi_agent_brief.cli.main version`
- 验证 `.venv\Scripts\multi-agent-brief.exe version`

## Run Demo

```powershell
multi-agent-brief init --demo
# Then use /generate-brief inside Claude Code, or use:
# multi-agent-brief run --workspace <workspace>
```

或直接运行示例输入：

```powershell
# Use /generate-brief only inside Claude Code for example workspaces.
# Generic chat clients should use multi-agent-brief run --workspace <workspace>.
```

## Init Workspace

```powershell
multi-agent-brief init my-workspace
```

Answer the interactive onboarding questions. For non-interactive agent runs, generate `onboarding.json` first and use `multi-agent-brief init my-workspace --from-onboarding onboarding.json`.

## Run Tests

```powershell
python -m pytest -q
```

## Agent Config Check

```powershell
python scripts/generate_agent_configs.py --check
```

重新生成自动文件：

```powershell
python scripts/generate_agent_configs.py --write
python scripts/generate_agent_configs.py --check
```

## No-Install Run

PowerShell 不能使用 Bash 的 `PYTHONPATH=src command` 写法。请这样写：

```powershell
$env:PYTHONPATH = "src"
python -m multi_agent_brief.cli.main run tests/fixtures/basic_market_brief/input --output output/basic_market_brief
Remove-Item Env:PYTHONPATH
```

## 常见问题

### Activate.ps1 cannot be loaded

如果激活虚拟环境时报 ExecutionPolicy 错误，运行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新执行：

```powershell
.\.venv\Scripts\Activate.ps1
```

也可以只对 setup 脚本临时绕过：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

### python opens Microsoft Store

这通常说明 `python` 指向 Microsoft Store placeholder，而不是真实 Python。

解决方式：

```powershell
winget install Python.Python.3.12
```

安装后重新打开 PowerShell。也可以在 Windows 设置里关闭 App execution aliases 中的 Python alias。

### python3 not recognized

Windows 上推荐使用：

```powershell
python
```

或 Python launcher：

```powershell
py -3
```

本项目文档中的 Windows 命令统一使用 `python`，不是 `python3`。

### PYTHONPATH=src does not work in PowerShell

`PYTHONPATH=src python ...` 是 Bash 写法。PowerShell 使用：

```powershell
$env:PYTHONPATH = "src"
python -m multi_agent_brief.cli.main version
Remove-Item Env:PYTHONPATH
```

### Paths With Spaces

路径中有空格时请加引号：

```powershell
cd "C:\Users\you\Documents\multi-agent-brief-workflow"
python scripts/generate_agent_configs.py --check
```

### WSL Is Optional

WSL/Git Bash 可以使用 macOS/Linux 文档里的 Bash 命令，但 Windows 用户不需要安装 WSL 才能使用本项目。

### Git Hook Is Optional

`.githooks/pre-push` 是维护者可选 hook，普通 Windows 用户不需要安装。
