"""Deterministic A4 research-report HTML projection of the reader brief.

Takes the frozen reader markdown (labels, appendix, footer, auto
calendar already applied) plus the hash-bound chart PNGs and renders a
paginated sell-side-style document: cover masthead, accent section
headers, zebra metric tables, exhibit charts, per-page footer with run
identity and disclaimer, print-to-PDF button.  Pure projection — the
Store artifact bytes are the only input; no facts are added.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

REPORT_OUTPUT_RELATIVE_PATH = Path("output/brief_report.html")

_CSS = """
:root{
  --paper:#FFFFFF;--canvas:#8A9199;--accent:#0095C8;--accent-d:#00708F;
  --ink:#2B2B2B;--ink-2:#55595C;--ink-3:#82878B;--rule:#B9C1C6;--hair:#E1E5E8;
  --fill:#EAF3F7;--zebra:#F6F8F9;--pos:#1A7A4F;--neg:#B3271E;
  --sans:"Source Sans 3","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
html,body{background:var(--canvas)}
body{margin:0;font-family:var(--sans);color:var(--ink);font-size:11px;line-height:1.45;
  -webkit-font-smoothing:antialiased;padding:22px 0 40px}
p{margin:0 0 6px}
.page{width:794px;min-height:1123px;background:var(--paper);margin:0 auto 20px;
  padding:44px 52px 48px;display:flex;flex-direction:column;box-shadow:0 2px 14px rgba(0,0,0,.28)}
.page-body{flex:1 1 auto}
.mast{border-bottom:2px solid var(--accent);padding-bottom:10px;margin-bottom:14px}
.mast .doc{color:var(--accent);font-weight:700;font-size:12px}
.mast h1{font-size:20px;font-weight:600;margin:6px 0 2px;line-height:1.2}
.mast .meta{font-size:11px;color:var(--ink-2)}
h2{font-size:14px;font-weight:600;color:var(--accent);margin:14px 0 4px;
  border-bottom:1px solid var(--rule);padding-bottom:3px}
h3{font-size:12px;font-weight:700;margin:10px 0 3px}
h4{font-size:11px;font-weight:700;color:var(--accent-d);margin:8px 0 3px}
table{width:100%;border-collapse:collapse;font-size:10px;margin:4px 0 8px}
thead th{background:var(--fill);font-size:9.5px;font-weight:700;color:var(--ink-2);
  text-align:right;padding:3px 5px;border-bottom:1px solid var(--rule);white-space:nowrap}
thead th:first-child{text-align:left}
tbody th{text-align:left;font-weight:400;padding:2.8px 5px;border-bottom:1px solid var(--hair);white-space:nowrap}
tbody td{text-align:right;padding:2.8px 5px;border-bottom:1px solid var(--hair);white-space:nowrap}
tbody tr:nth-child(even) th,tbody tr:nth-child(even) td{background:var(--zebra)}
blockquote{border-left:3px solid var(--accent);background:var(--fill);margin:6px 0;
  padding:6px 10px;color:var(--ink-2);font-size:10.5px}
img.chart{max-width:100%;border:1px solid var(--hair);margin:4px 0 8px}
ul,ol{margin:0 0 7px;padding-left:18px}
li{margin-bottom:3px}
code{background:var(--zebra);padding:0 3px}
hr{border:0;border-top:1px solid var(--rule);margin:10px 0}
.rf{margin-top:auto;padding-top:8px;border-top:1px solid var(--rule);
  display:flex;justify-content:space-between;font-size:9px;color:var(--ink-3)}
.tb{width:794px;margin:0 auto 10px;display:flex;justify-content:flex-end}
.btn{font-size:11px;font-weight:600;padding:6px 14px;background:var(--accent);color:#fff;
  border:0;cursor:pointer}
.btn:hover{background:var(--accent-d)}
@media print{
  @page{size:A4 portrait;margin:0}
  html,body{background:#fff}body{padding:0}.tb{display:none}
  .page{width:210mm;min-height:0;margin:0;box-shadow:none;padding:11mm 13.5mm 12mm;
    break-after:page}
  .page:last-child{break-after:auto}
}
@media screen and (max-width:860px){
  body{padding:10px 0 20px}
  .page,.tb{width:100%;padding-left:18px;padding-right:18px}
}
"""

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    out = _escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"\*(.+?)\*", r"<i>\1</i>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out


def _markdown_to_html(markdown: str, *, images_base64: dict[str, str]) -> list[str]:
    """Render our reader markdown into HTML blocks (no external parsers)."""

    blocks: list[str] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{min(level + 1, 4)}>{_inline(heading.group(2))}</h{min(level + 1, 4)}>")
            index += 1
            continue
        if line.strip() == "":
            index += 1
            continue
        if line.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].startswith(">"):
                quote.append(lines[index].lstrip("> ").rstrip())
                index += 1
            blocks.append(f"<blockquote>{_inline(' '.join(quote))}</blockquote>")
            continue
        if line.startswith("```"):
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            blocks.append("<pre>" + _escape("\n".join(code)) + "</pre>")
            continue
        if line.startswith("|"):
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [
                    cell.strip()
                    for cell in _TABLE_ROW_RE.match(lines[index]).group(1).split("|")
                ]
                rows.append(cells)
                index += 1
            if len(rows) >= 2 and all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]
            ):
                header, body = rows[0], rows[2:]
            else:
                header, body = None, rows
            parts = ["<table>"]
            if header is not None:
                parts.append("<thead><tr>")
                parts.extend(
                    f"<th>{_inline(cell)}</th>" for cell in header
                )
                parts.append("</tr></thead>")
            parts.append("<tbody>")
            for row in body:
                parts.append("<tr>")
                parts.append(f"<th>{_inline(row[0])}</th>")
                parts.extend(f"<td>{_inline(cell)}</td>" for cell in row[1:])
                parts.append("</tr>")
            parts.append("</tbody></table>")
            blocks.append("".join(parts))
            continue
        match = re.match(r"^!\[(.*?)\]\((.+?)\)$", line.strip())
        if match:
            source = match.group(2)
            encoded = images_base64.get(source)
            if encoded:
                blocks.append(
                    f'<img class="chart" alt="{_escape(match.group(1))}" '
                    f'src="data:image/png;base64,{encoded}">'
                )
            index += 1
            continue
        if re.match(r"^[-*]\s+", line):
            items: list[str] = []
            while index < len(lines) and re.match(r"^[-*]\s+", lines[index]):
                items.append(re.sub(r"^[-*]\s+", "", lines[index]))
                index += 1
            blocks.append(
                "<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>"
            )
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while index < len(lines) and re.match(r"^\d+\.\s+", lines[index]):
                items.append(re.sub(r"^\d+\.\s+", "", lines[index]))
                index += 1
            blocks.append(
                "<ol>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ol>"
            )
            continue
        if line.strip() == "---":
            blocks.append("<hr>")
            index += 1
            continue
        paragraph: list[str] = []
        while (
            index < len(lines)
            and lines[index].strip()
            and not lines[index].startswith(("#", "|", ">", "```", "!"))
            and not re.match(r"^[-*]\s+", lines[index])
            and not re.match(r"^\d+\.\s+", lines[index])
        ):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return blocks


def _collect_chart_images(markdown: str, workspace: Path) -> dict[str, str]:
    """Base64-encode every referenced chart PNG so the file is self-contained."""

    encoded: dict[str, str] = {}
    for source in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
        if source in encoded:
            continue
        path = workspace / "output" / source.lstrip("/")
        try:
            encoded[source] = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
    return encoded


def render_report_html(
    *,
    reader_markdown: str,
    workspace: Path,
    page_footer_left: str,
) -> bytes:
    """Render one self-contained A4 research-report HTML document."""

    images = _collect_chart_images(reader_markdown, workspace)
    blocks = _markdown_to_html(reader_markdown, images_base64=images)

    # Cover header: document title + first metadata line from the brief.
    lines = [line for line in reader_markdown.splitlines() if line.strip()]
    title = ""
    meta = ""
    for line in lines:
        heading = _HEADING_RE.match(line)
        if heading and not title:
            title = heading.group(2)
            continue
        if title and line.startswith("**"):
            meta = line.strip("*").strip()
            break

    # Paginate: split blocks across pages at ~4200 chars per page body,
    # keeping tables and images unsplit.
    pages: list[str] = []
    current: list[str] = []
    length = 0
    for block in blocks:
        if length + len(block) > 4200 and current:
            pages.append("".join(current))
            current, length = [], 0
        current.append(block)
        length += len(block)
    if current:
        pages.append("".join(current))

    page_html: list[str] = []
    total = max(len(pages), 1)
    for number, body in enumerate(pages, start=1):
        cover = ""
        if number == 1:
            cover = (
                '<div class="mast"><div class="doc">'
                + _escape(page_footer_left.split("·")[0].strip())
                + "</div><h1>"
                + _inline(title or "Brief")
                + "</h1><div class=\"meta\">"
                + _inline(meta)
                + "</div></div>"
            )
        page_html.append(
            '<article class="page"><div class="page-body">'
            + cover
            + body
            + '</div><footer class="rf"><div>'
            + _escape(page_footer_left)
            + "</div><div>"
            + f"{number} / {total}"
            + "</div></footer></article>"
        )

    document = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>" + _escape(title or "BriefLoop Report") + "</title>"
        "<style>" + _CSS + "</style></head><body>"
        '<div class="tb"><button class="btn" onclick="window.print()">'
        "打印 / 存为 PDF</button></div>"
        + "".join(page_html)
        + "</body></html>\n"
    )
    return document.encode("utf-8")


__all__ = [
    "REPORT_OUTPUT_RELATIVE_PATH",
    "render_report_html",
]
