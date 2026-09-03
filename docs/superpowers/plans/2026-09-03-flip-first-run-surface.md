# 方案 A：翻转首屏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让陌生人执行 `bash scripts/demo.sh` 后，终端第一屏第一个路径是 `brief.md`，且 `briefloop --help` 不再列出 6 个 Experimental 命令。

**Architecture:** 纯呈现层改动。新增一个 CLI 隐藏辅助模块（`argparse` 的 `_choices_actions` 移除 + `metavar` 覆盖，命令保持可调用），把 `demo.py` 的输出拆成「产物 / 可选审计」两段，把散落的声明边界集中到 `docs/claims.md`。**不删除任何代码、测试、contract 或不变量。**

**Tech Stack:** Python 3.12+、argparse、pytest。无新依赖。

**基线：** `main` @ `3f1334e8`。规格见 [`docs/superpowers/specs/2026-09-03-briefloop-usability-and-evaluation-design.md`](../specs/2026-09-03-briefloop-usability-and-evaluation-design.md) §3。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/multi_agent_brief/cli/experimental.py` | **新建**。判定 experimental 开关；从 parser 隐藏命令。唯一接触 argparse 私有属性的地方。 |
| `src/multi_agent_brief/cli/main.py` | **修改**。`build_parser` 末尾调用隐藏逻辑。 |
| `scripts/demo.py` | **修改**。`_print_success` 拆出可测的 `_success_message`。 |
| `docs/claims.md` | **新建**。集中所有声明边界文本。 |
| `docs/15-minute-pilot.md` | **重写**。指向 brief，移出 "What BriefLoop Is Not"。 |
| `README.md` | **精简**至 ≤120 行。 |
| `docs/build-week.md` | **新建**。承接 README 的 Build Week 表格。 |
| `docs/support-matrix-experimental.md` | **新建**。承接 36 Experimental + 19 Retired。 |
| `tests/test_cli_experimental_surface.py` | **新建**。隐藏机制回归。 |
| `tests/test_demo_output_surface.py` | **新建**。demo 输出顺序回归。 |
| `tests/test_docs_first_run_surface.py` | **新建**。文档验收标准回归。 |

---

## Task 1: CLI 隐藏机制

**Files:**
- Create: `src/multi_agent_brief/cli/experimental.py`
- Test: `tests/test_cli_experimental_surface.py`

**背景：** argparse 没有官方的「隐藏子命令」API。`help=argparse.SUPPRESS` 对子解析器无效。已验证可行的组合是：从 `subparsers._choices_actions` 移除条目（去掉说明行）**并且**覆盖 `subparsers.metavar`（去掉 usage 行里的枚举）。`subparsers.choices` 不动，因此命令仍可调用。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_cli_experimental_surface.py`：

```python
"""Experimental commands are hidden from default help but stay callable."""

from __future__ import annotations

import argparse

import pytest

from multi_agent_brief.cli.experimental import (
    EXPERIMENTAL_COMMANDS,
    experimental_enabled,
    hide_experimental_commands,
)


def _parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    parser = argparse.ArgumentParser(prog="briefloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("keep", help="visible command")
    subparsers.add_parser("experiments", help="experimental harness")
    subparsers.add_parser("quality", help="experimental quality surface")
    return parser, subparsers


def test_hidden_commands_absent_from_help():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    text = parser.format_help()
    assert "experiments" not in text
    assert "quality" not in text
    assert "keep" in text


def test_hidden_commands_remain_callable():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    assert parser.parse_args(["experiments"]).command == "experiments"
    assert parser.parse_args(["quality"]).command == "quality"


def test_hiding_does_not_shrink_choices():
    parser, subparsers = _parser()
    before = set(subparsers.choices)
    hide_experimental_commands(subparsers)
    assert set(subparsers.choices) == before


def test_experimental_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    assert experimental_enabled() is False
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")
    assert experimental_enabled() is True
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "0")
    assert experimental_enabled() is False


def test_experimental_command_list_is_frozen():
    assert EXPERIMENTAL_COMMANDS == frozenset(
        {"experiments", "new", "packs", "validate-report-spec", "extract", "quality"}
    )


def test_hide_is_idempotent():
    parser, subparsers = _parser()
    hide_experimental_commands(subparsers)
    first = parser.format_help()
    hide_experimental_commands(subparsers)
    assert parser.format_help() == first


def test_hide_tolerates_absent_commands():
    parser = argparse.ArgumentParser(prog="briefloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("keep", help="visible command")
    hide_experimental_commands(subparsers)
    assert "keep" in parser.format_help()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cli_experimental_surface.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'multi_agent_brief.cli.experimental'`

- [ ] **Step 3: 实现**

创建 `src/multi_agent_brief/cli/experimental.py`：

```python
"""Default-surface gating for Experimental CLI commands.

Hidden commands stay callable: only their help entries are removed.  This is
the single place that touches argparse internals, so the private-attribute
risk is contained and covered by tests.
"""

from __future__ import annotations

import argparse
import os

EXPERIMENTAL_ENV_VAR = "BRIEFLOOP_EXPERIMENTAL"

EXPERIMENTAL_COMMANDS = frozenset(
    {
        "experiments",
        "new",
        "packs",
        "validate-report-spec",
        "extract",
        "quality",
    }
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def experimental_enabled() -> bool:
    """Return True when the caller opted into the Experimental surface."""
    return os.environ.get(EXPERIMENTAL_ENV_VAR, "").strip().lower() in _TRUTHY


def hide_experimental_commands(
    subparsers: argparse._SubParsersAction,
    *,
    commands: frozenset[str] = EXPERIMENTAL_COMMANDS,
) -> None:
    """Remove Experimental commands from help output without unregistering them.

    argparse renders subcommands twice: once as a ``{a,b,c}`` metavar on the
    usage line, and once as a description list built from
    ``_choices_actions``.  Both have to be adjusted, and ``choices`` must be
    left intact so existing scripts keep working.
    """
    visible = [
        action.dest
        for action in subparsers._choices_actions
        if action.dest not in commands
    ]
    for action in list(subparsers._choices_actions):
        if action.dest in commands:
            subparsers._choices_actions.remove(action)
    subparsers.metavar = "{" + ",".join(visible) + "}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cli_experimental_surface.py -q`
Expected: PASS，7 passed

- [ ] **Step 5: 提交**

```bash
git add src/multi_agent_brief/cli/experimental.py tests/test_cli_experimental_surface.py
git commit -m "feat(cli): add experimental command hiding helper"
```

---

## Task 2: 接入 build_parser

**Files:**
- Modify: `src/multi_agent_brief/cli/main.py:39-95`（`build_parser`）
- Test: `tests/test_cli_experimental_surface.py`（追加）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_cli_experimental_surface.py` 末尾追加：

```python
def _command_subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices"):
            return action
    raise AssertionError("command subparsers not found")


def test_build_parser_hides_experimental_by_default(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    from multi_agent_brief.cli.main import build_parser

    parser = build_parser()
    text = parser.format_help()
    for command in EXPERIMENTAL_COMMANDS:
        assert command not in text, f"{command} should be hidden by default"
    assert "status" in text
    assert "runtime" in text


def test_build_parser_shows_experimental_when_opted_in(monkeypatch):
    monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")
    from multi_agent_brief.cli.main import build_parser

    text = build_parser().format_help()
    for command in EXPERIMENTAL_COMMANDS:
        assert command in text, f"{command} should be visible when opted in"


def test_hidden_experimental_commands_still_registered(monkeypatch):
    monkeypatch.delenv("BRIEFLOOP_EXPERIMENTAL", raising=False)
    from multi_agent_brief.cli.main import build_parser

    subparsers = _command_subparsers(build_parser())
    for command in EXPERIMENTAL_COMMANDS:
        assert command in subparsers.choices
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cli_experimental_surface.py -q`
Expected: FAIL —— `assert "experiments" not in text`（默认仍然可见）

- [ ] **Step 3: 实现**

在 `src/multi_agent_brief/cli/main.py` 的 import 区加入：

```python
from multi_agent_brief.cli.experimental import (
    experimental_enabled,
    hide_experimental_commands,
)
```

把 `build_parser` 结尾的

```python
    # Meta
    subparsers.add_parser("version", help="Print package version.")

    return parser
```

改为

```python
    # Meta
    subparsers.add_parser("version", help="Print package version.")

    # Experimental commands stay registered and callable, but are removed from
    # the default help surface.  Set BRIEFLOOP_EXPERIMENTAL=1 to list them.
    if not experimental_enabled():
        hide_experimental_commands(subparsers)

    return parser
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cli_experimental_surface.py -q`
Expected: PASS，10 passed

- [ ] **Step 5: 确认没有破坏既有 CLI 测试**

Run: `python -m pytest tests/test_cli.py tests/test_start_commands.py -q`
Expected: PASS（若有测试断言 `--help` 含被隐藏命令，改为在该测试内 `monkeypatch.setenv("BRIEFLOOP_EXPERIMENTAL", "1")`，不要改产品代码）

- [ ] **Step 6: 提交**

```bash
git add src/multi_agent_brief/cli/main.py tests/test_cli_experimental_surface.py
git commit -m "feat(cli): hide experimental commands from default help"
```

---

## Task 3: demo 输出拆成产物 / 审计两段

**Files:**
- Modify: `scripts/demo.py:139-160`（`_print_success`）
- Test: `tests/test_demo_output_surface.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_demo_output_surface.py`：

```python
"""The demo surfaces the brief before audit projections."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def _demo_module():
    spec = importlib.util.spec_from_file_location("_demo", ROOT / "scripts" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_demo"] = module
    spec.loader.exec_module(module)
    return module


def test_brief_is_the_first_path_in_the_message():
    message = _demo_module()._success_message(Path("/tmp/ws"))
    paths = [line for line in message.splitlines() if line.strip().startswith("- /")]
    assert paths, "expected at least one path line"
    assert paths[0].endswith("output/delivery/brief.md")


def test_audit_artifacts_appear_after_the_brief():
    message = _demo_module()._success_message(Path("/tmp/ws"))
    assert message.index("brief.md") < message.index("quality_panel.html")
    assert message.index("brief.md") < message.index("claim_ledger.json")


def test_source_appendix_is_in_the_first_block():
    message = _demo_module()._success_message(Path("/tmp/ws"))
    head = message.split("How it was checked")[0]
    assert "source_appendix.md" in head


def test_disclaimer_is_one_line_and_links_to_claims_doc():
    message = _demo_module()._success_message(Path("/tmp/ws"))
    assert "docs/claims.md" in message
    assert "not a semantic proof" in message.lower()
    tail = message.split("How it was checked")[1]
    disclaimer_lines = [
        line
        for line in tail.splitlines()
        if line.strip() and not line.strip().startswith("-")
    ]
    assert len(disclaimer_lines) == 1, disclaimer_lines
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_demo_output_surface.py -q`
Expected: FAIL —— `AttributeError: module '_demo' has no attribute '_success_message'`

- [ ] **Step 3: 实现**

把 `scripts/demo.py` 中整个 `_print_success` 函数替换为：

```python
def _success_message(workspace: Path) -> str:
    delivery = workspace / "output" / "delivery"
    intermediate = workspace / "output" / "intermediate"
    return f"""BriefLoop demo complete.

Workspace:
- {workspace}

Your brief:
- {delivery / "brief.md"}
- {workspace / "output" / "source_appendix.md"}

How it was checked (optional):
- {intermediate / "quality_panel.html"}
- {intermediate / "claim_ledger.json"}
- {intermediate / "quality_summary.md"}
- {intermediate / "quality_panel.json"}
- {intermediate / "event_log_excerpt.jsonl"}

Deterministic demo: no LLM call, no source fetch, no API key. It shows traceability, not a semantic proof — see docs/claims.md."""


def _print_success(workspace: Path) -> None:
    print(_success_message(workspace))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_demo_output_surface.py -q`
Expected: PASS，4 passed

- [ ] **Step 5: 端到端跑一次 demo**

Run: `python scripts/demo.py`
Expected: 输出中 `Your brief:` 块在 `How it was checked (optional):` 之前，且 `brief.md` 是第一个路径

- [ ] **Step 6: 提交**

```bash
git add scripts/demo.py tests/test_demo_output_surface.py
git commit -m "feat(demo): surface the brief before audit projections"
```

---

## Task 4: 新建 docs/claims.md

**Files:**
- Create: `docs/claims.md`
- Test: `tests/test_docs_first_run_surface.py`

**说明：** 这是**搬运，不是删减**。README 的 "Evidence boundary" 节与 15-min pilot 的 "What BriefLoop Is Not" 节原文迁入。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_docs_first_run_surface.py`：

```python
"""First-run documentation surface acceptance criteria."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "docs" / "claims.md"
PILOT = ROOT / "docs" / "15-minute-pilot.md"
README = ROOT / "README.md"


def test_claims_doc_exists():
    assert CLAIMS.is_file()


def test_claims_doc_retains_the_boundary_statements():
    text = CLAIMS.read_text(encoding="utf-8")
    for phrase in (
        "not a semantic proof engine",
        "not an automatic truth checker",
        "not a replacement for human review",
        "NOT MEASURED",
    ):
        assert phrase in text, f"missing boundary statement: {phrase}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: FAIL —— `assert False` on `test_claims_doc_exists`

- [ ] **Step 3: 创建 docs/claims.md**

```markdown
# What BriefLoop Claims — And What It Does Not

This page collects every claim boundary in one place. Nothing here is new:
these statements previously appeared inline in `README.md` and
`docs/15-minute-pilot.md`, where they competed with the getting-started path.

## What BriefLoop Is Not

BriefLoop is not:

- a semantic proof engine;
- an automatic truth checker;
- a replacement for human review;
- a report publisher or delivery approval system;
- evidence that output quality improved.

The demo shows the artifact chain. It does not prove that a real report is
ready to send.

## Evidence Boundary

The Prompt, Skill, and BriefLoop comparison is reported only from frozen
artifacts, hashes, and completed review records. BriefLoop does not claim that
it has already won the comparison, automatically resolves every knowledge
conflict, or removes the need for human review. It does not prove semantic
truth.

## What Is Not Measured

Reuse utility is **NOT MEASURED**. Approved guidance has no later-run effect
until a Human explicitly starts a compatible successor with reuse enabled, and
no experiment currently reports whether that reuse improves output.

The measurement harness that would close this gap was retired in LD2-3. See
`docs/superpowers/specs/2026-09-03-briefloop-usability-and-evaluation-design.md`
for the plan to rebuild it.

## Supported Versus Experimental

`docs/support-matrix.md` lists Supported capabilities. Experimental, Retired,
and Deprecated capabilities are listed in
`docs/support-matrix-experimental.md`. Experimental commands are hidden from
default CLI help; set `BRIEFLOOP_EXPERIMENTAL=1` to list them.
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: PASS，2 passed

- [ ] **Step 5: 提交**

```bash
git add docs/claims.md tests/test_docs_first_run_surface.py
git commit -m "docs: consolidate claim boundaries into docs/claims.md"
```

---

## Task 5: 重写 15-minute-pilot

**Files:**
- Modify: `docs/15-minute-pilot.md`
- Test: `tests/test_docs_first_run_surface.py`（追加）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_docs_first_run_surface.py`：

```python
def test_pilot_points_at_the_brief_first():
    text = PILOT.read_text(encoding="utf-8")
    assert "output/delivery/brief.md" in text
    assert text.index("brief.md") < text.index("quality_panel.html")


def test_pilot_has_no_not_measured_text():
    assert "NOT MEASURED" not in PILOT.read_text(encoding="utf-8")


def test_pilot_delegates_boundaries_to_claims_doc():
    text = PILOT.read_text(encoding="utf-8")
    assert "claims.md" in text
    assert "## What BriefLoop Is Not" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: FAIL —— `assert "## What BriefLoop Is Not" not in text`

- [ ] **Step 3: 用以下内容整体替换 `docs/15-minute-pilot.md`**

```markdown
# Fifteen-Minute Pilot

See BriefLoop produce a brief before reading any architecture doc.

## Run The Local Demo

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

Deterministic: no LLM call, no source fetch, no API key.

On Windows, use the PowerShell setup flow from
[`getting-started.md`](getting-started.md), then run `python scripts/demo.py`.

## Read The Brief

The demo prints the workspace path. Open these two files:

| File | What it is |
|---|---|
| `output/delivery/brief.md` | The brief a reader would receive |
| `output/source_appendix.md` | Where every number came from |

## Then Ask It Questions

The point of BriefLoop is that the brief stays questionable after it is
written. These files answer the follow-ups:

| Question | File |
|---|---|
| Which claims were registered? | `output/intermediate/claim_ledger.json` |
| Which checks passed or failed? | `output/intermediate/quality_panel.html` |
| What is the run status? | `output/intermediate/quality_summary.md` |

Treat these as review surfaces, not authority to publish. Delivery stays
human-triggered and gated.

## What This Does And Does Not Prove

It shows the artifact chain. It does not prove a report is ready to send.
Full claim boundaries: [`claims.md`](claims.md).

For the longer first-user path, continue with
[`getting-started.md`](getting-started.md).
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: PASS，5 passed

- [ ] **Step 5: 提交**

```bash
git add docs/15-minute-pilot.md tests/test_docs_first_run_surface.py
git commit -m "docs(pilot): lead with the brief, delegate boundaries to claims.md"
```

---

## Task 6: 拆分 support matrix

**Files:**
- Modify: `docs/support-matrix.md`
- Create: `docs/support-matrix-experimental.md`
- Test: `tests/test_docs_first_run_surface.py`（追加）

**说明：** 逐行搬运，**不改任何一行的状态文字**。主表只留 `Supported`，其余整行迁到新文件。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_docs_first_run_surface.py`：

```python
MATRIX = ROOT / "docs" / "support-matrix.md"
MATRIX_EXP = ROOT / "docs" / "support-matrix-experimental.md"


def _status_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        for status in ("Supported", "Experimental", "Retired", "Deprecated"):
            if f"| {status}" in line or f"| **{status}**" in line:
                counts[status] = counts.get(status, 0) + 1
    return counts


def test_experimental_matrix_exists_and_is_linked():
    assert MATRIX_EXP.is_file()
    assert "support-matrix-experimental.md" in MATRIX.read_text(encoding="utf-8")


def test_main_matrix_has_no_experimental_rows():
    assert _status_counts(MATRIX).get("Experimental", 0) == 0


def test_experimental_rows_were_moved_not_deleted():
    counts = _status_counts(MATRIX_EXP)
    assert counts.get("Experimental", 0) >= 36
    assert counts.get("Retired", 0) >= 19
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: FAIL —— `test_experimental_matrix_exists_and_is_linked`

- [ ] **Step 3: 执行搬运**

先记录基线计数：

```bash
grep -oE "\| *(Supported|Experimental|Retired|Deprecated)" docs/support-matrix.md \
  | grep -oE "(Supported|Experimental|Retired|Deprecated)" | sort | uniq -c
```
Expected: `36 Experimental / 50 Supported / 19 Retired / 4 Deprecated`

创建 `docs/support-matrix-experimental.md`，以下列内容开头：

```markdown
# Support Matrix — Experimental, Retired, And Deprecated

This page holds every capability that is not Supported. Rows are moved here
verbatim from `docs/support-matrix.md`; no status wording is changed.

Experimental CLI commands are hidden from default help. Set
`BRIEFLOOP_EXPERIMENTAL=1` to list them.

See `docs/support-matrix.md` for Supported capabilities and the status legend.
```

然后把 `docs/support-matrix.md` 中每一行状态为 `Experimental`、`Retired`、
`Deprecated` 的表格行**整行剪切**到新文件，保持其原有的章节标题结构。

在 `docs/support-matrix.md` 的状态图例之后插入一行：

```markdown
> Experimental, Retired, and Deprecated capabilities are listed separately in
> [`support-matrix-experimental.md`](support-matrix-experimental.md).
```

- [ ] **Step 4: 验证搬运无损**

```bash
grep -oE "\| *(Supported|Experimental|Retired|Deprecated)" \
  docs/support-matrix.md docs/support-matrix-experimental.md \
  | grep -oE "(Supported|Experimental|Retired|Deprecated)" | sort | uniq -c
```
Expected: 合计仍为 `36 Experimental / 50 Supported / 19 Retired / 4 Deprecated`

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: PASS，8 passed

- [ ] **Step 6: 提交**

```bash
git add docs/support-matrix.md docs/support-matrix-experimental.md tests/test_docs_first_run_surface.py
git commit -m "docs(matrix): split non-Supported rows into a separate page"
```

---

## Task 7: README 精简至 ≤120 行

**Files:**
- Modify: `README.md`（当前 676 行）
- Create: `docs/build-week.md`
- Test: `tests/test_docs_first_run_surface.py`（追加）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_docs_first_run_surface.py`：

```python
BUILD_WEEK = ROOT / "docs" / "build-week.md"


def test_readme_is_one_screen():
    lines = README.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 120, f"README is {len(lines)} lines"


def test_readme_has_no_not_measured_text():
    assert "NOT MEASURED" not in README.read_text(encoding="utf-8")


def test_readme_links_to_the_moved_pages():
    text = README.read_text(encoding="utf-8")
    for target in ("docs/claims.md", "docs/build-week.md", "docs/15-minute-pilot.md"):
        assert target in text, f"README should link to {target}"


def test_build_week_page_retains_the_authority_table():
    text = BUILD_WEEK.read_text(encoding="utf-8")
    assert "Authority boundary" in text
    assert "Human maintainer" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: FAIL —— `README is 676 lines`

- [ ] **Step 3: 搬运 Build Week 内容**

创建 `docs/build-week.md`，把 `README.md` 中 `## OpenAI Build Week 2026` 整节
（含三行参与者/授权边界表格与 Academic Research Skill 段落）原样迁入，
文件开头加：

```markdown
# OpenAI Build Week 2026

How BriefLoop was built, and who held which authority.
```

- [ ] **Step 4: 重写 README.md**

保留结构：标题与一句话定位 / 语言切换 / judge quickstart / 产出物 /
四个可追溯问题 / 文档索引。移出内容按下表：

| 原 README 内容 | 去向 |
|---|---|
| `## OpenAI Build Week 2026` 全节 | `docs/build-week.md` |
| `### Evidence boundary` | `docs/claims.md` |
| v0.15.3 successor / guidance 段落 | `docs/support-matrix-experimental.md` |
| 详细问题陈述、受众、示例 | `docs/getting-started.md` 已有，删除重复 |

README 必须包含指向 `docs/claims.md`、`docs/build-week.md`、
`docs/15-minute-pilot.md` 的链接。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_docs_first_run_surface.py -q`
Expected: PASS，12 passed

- [ ] **Step 6: 同步中文 README**

对 `README.zh-CN.md` 做同样的精简与链接调整。

Run: `wc -l README.md README.zh-CN.md`
Expected: 两者均 ≤120 行

- [ ] **Step 7: 提交**

```bash
git add README.md README.zh-CN.md docs/build-week.md tests/test_docs_first_run_surface.py
git commit -m "docs(readme): trim to one screen, move Build Week to its own page"
```

---

## Task 8: 全量回归与验收

**Files:** 无新增

- [ ] **Step 1: 全量测试**

Run: `python -m pytest -q`
Expected: PASS。

已知例外：Python 3.13+ 上有 2 个与 `Path.resolve()` 符号链接语义变化相关的失败，
与本改动无关（CI 跑 3.12）。若出现其它失败，必须修复而非豁免。

- [ ] **Step 2: 逐条核对规格 §3 A6 验收标准**

```bash
python scripts/demo.py 2>&1 | head -8
```
Expected: `Your brief:` 块中第一个路径以 `output/delivery/brief.md` 结尾

```bash
python -c "from multi_agent_brief.cli.main import build_parser; print(build_parser().format_help())" \
  | grep -cE '\b(experiments|packs|extract|quality|validate-report-spec)\b'
```
Expected: `0`

```bash
wc -l README.md
```
Expected: ≤ 120

```bash
grep -c "NOT MEASURED" README.md docs/15-minute-pilot.md
```
Expected: 两个文件均为 `0`

```bash
git diff --stat main...HEAD -- tests/ | tail -1
```
Expected: 只有新增测试，**无测试被删除**

- [ ] **Step 3: 确认 Experimental 开关可逆**

```bash
BRIEFLOOP_EXPERIMENTAL=1 python -c "from multi_agent_brief.cli.main import build_parser; print(build_parser().format_help())" \
  | grep -cE '\b(experiments|packs|extract|quality|validate-report-spec)\b'
```
Expected: `> 0`

```bash
python -m multi_agent_brief.cli.main experiments --help
```
Expected: 正常输出（隐藏不等于禁用）

- [ ] **Step 4: 提交**

```bash
git commit --allow-empty -m "chore: verify plan A acceptance criteria"
```
