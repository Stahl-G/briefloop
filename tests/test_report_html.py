"""Unit tests for the A4 research-report HTML projection."""

from __future__ import annotations

from pathlib import Path

from multi_agent_brief.product.brief_html.report import render_report_html

_MARKDOWN = """# ExampleCo 周报

**报告窗口：2026-08-03 至 2026-08-25 ｜ 语言：中文**

> 编辑说明：叙事依据经核验的事实清单。

## 一、摘要

- 第一要点 [S1]。
- 第二要点 [S2]。

## 四、比较表

| 指标 | DEMO | FSLR |
| --- | ---: | ---: |
| 收盘价 | 4.38 | 208.31 |
| 1周涨跌 % | -14.95 | -1.29 |

## 十、催化剂日历

（本节由系统生成）

---

**编号 RUN-TEST · 不构成投资建议**
"""


def test_report_html_renders_pages_tables_and_footer(tmp_path: Path) -> None:
    payload = render_report_html(
        reader_markdown=_MARKDOWN,
        workspace=tmp_path,
        page_footer_left="BriefLoop · RUN-TEST · 不构成投资建议",
    )
    text = payload.decode("utf-8")
    assert text.count('<article class="page">') >= 1
    assert "打印 / 存为 PDF" in text
    assert "<th>指标</th>" in text
    assert "<td>4.38</td>" in text
    assert "1 / " in text
    assert "RUN-TEST" in text
    # citations are already labels in the reader copy; nothing internal leaks
    assert "[src:" not in text
    # deterministic for identical input
    again = render_report_html(
        reader_markdown=_MARKDOWN,
        workspace=tmp_path,
        page_footer_left="BriefLoop · RUN-TEST · 不构成投资建议",
    )
    assert payload == again
