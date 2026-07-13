#!/usr/bin/env python3
"""Check the v0.4 architecture-reference source/render contract.

The Markdown manuscripts remain the editable source. The self-contained HTML
editions may be rendered with different supported Pandoc versions, so this
guard checks semantic parity rather than byte identity: headings, fenced code,
visible body semantic atoms, non-fragment links, embedded CSS/SVG assets, and
valid local fragments.
"""

from __future__ import annotations

import base64
import html
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAG_SHA = "65b384c06bccffbb183a76db1260def02853b951"
TAG_NAME = "v0.11.12"
CSS_PATH = ROOT / "docs" / "assets" / "briefloop-tech-report.css"
VISIBLE_ATOM_RE = re.compile(
    r"[\u3400-\u9fff]|[^\W_]+|_|[^\w\s*~`|\\]",
    re.UNICODE,
)
INLINE_CODE_RE = re.compile(r"(?P<ticks>`+)(?P<code>.*?)(?P=ticks)")
UNDERSCORE_EMPHASIS_RE = re.compile(
    r"(?<!\w)(?P<marker>_{1,3})(?=\S)(?P<body>.*?\S)(?P=marker)(?!\w)"
)
ISSUE_CANDIDATE_HYPHENS = frozenset("-\u2010\u2011\u2012\u2013\u2014\u2015")


@dataclass(frozen=True)
class ReportPair:
    language: str
    markdown: Path
    html: Path
    svg: Path


REPORT_PAIRS = (
    ReportPair(
        language="zh-CN",
        markdown=ROOT / "docs" / "briefloop-architecture-reference-v0.4.0.md",
        html=ROOT / "docs" / "briefloop-architecture-reference-v0.4.0.html",
        svg=ROOT / "docs" / "assets" / "briefloop-architecture-v0.4.0.svg",
    ),
    ReportPair(
        language="en",
        markdown=ROOT / "docs" / "briefloop-architecture-reference-v0.4.0.en.md",
        html=ROOT / "docs" / "briefloop-architecture-reference-v0.4.0.en.html",
        svg=ROOT / "docs" / "assets" / "briefloop-architecture-v0.4.0.en.svg",
    ),
)


class _ReportHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.heading_tag: str | None = None
        self.heading_parts: list[str] = []
        self.headings: list[tuple[str, str]] = []
        self.in_pre = False
        self.pre_parts: list[str] = []
        self.code_blocks: list[str] = []
        self.in_style = False
        self.style_parts: list[str] = []
        self.footer_depth = 0
        self.body_parts: list[str] = []
        self.links: list[str] = []
        self.ids: list[str] = []
        self.fragments: list[str] = []
        self.embedded_svg_payloads: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key: value for key, value in attrs}
        identifier = values.get("id")
        if identifier:
            self.ids.append(identifier)
        href = values.get("href")
        if href and href.startswith("#"):
            self.fragments.append(href[1:])
        if tag == "main" and identifier == "report-main":
            self.in_main = True
        if self.in_main and tag == "footer":
            self.footer_depth += 1
        if self.in_main and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_tag = tag
            self.heading_parts = []
        if self.in_main and tag == "pre":
            self.in_pre = True
            self.pre_parts = []
        if self.in_main and tag == "a" and href and not href.startswith("#"):
            self.links.append(href)
        if tag == "style":
            self.in_style = True
        source = values.get("src")
        prefix = "data:image/svg+xml;base64,"
        if tag == "img" and source and source.startswith(prefix):
            self.embedded_svg_payloads.append(source[len(prefix) :])

    def handle_endtag(self, tag: str) -> None:
        if self.heading_tag == tag:
            self.headings.append(
                (tag, _normalize_space("".join(self.heading_parts)))
            )
            self.heading_tag = None
            self.heading_parts = []
        if tag == "pre" and self.in_pre:
            self.code_blocks.append("".join(self.pre_parts).strip("\n"))
            self.in_pre = False
            self.pre_parts = []
        if tag == "style":
            self.in_style = False
        if tag == "footer" and self.footer_depth:
            self.footer_depth -= 1
        if tag == "main" and self.in_main:
            self.in_main = False

    def handle_data(self, data: str) -> None:
        if self.heading_tag:
            self.heading_parts.append(data)
        if self.in_pre:
            self.pre_parts.append(data)
        if self.in_style:
            self.style_parts.append(data)
        if self.in_main and not self.footer_depth:
            self.body_parts.append(data)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_css(value: str) -> str:
    """Ignore render indentation without erasing CSS string contents."""

    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _canonical_text_asset_bytes(value: bytes) -> bytes:
    """Normalize checkout-only line endings without hiding content drift."""

    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _strip_markdown_inline(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[*_~]+", "", value)
    return _normalize_space(html.unescape(value))


def _markdown_structure(text: str) -> tuple[
    list[tuple[str, str]],
    list[str],
    list[str],
]:
    headings: list[tuple[str, str]] = []
    code_blocks: list[str] = []
    code_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            if in_fence:
                code_blocks.append("\n".join(code_lines))
                code_lines = []
                in_fence = False
            else:
                in_fence = True
            continue
        if in_fence:
            code_lines.append(line)
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line)
        if match:
            headings.append(
                (f"h{len(match.group(1))}", _strip_markdown_inline(match.group(2)))
            )
    links = [
        match.group(1)
        for match in re.finditer(
            r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)",
            text,
        )
        if not match.group(1).startswith("#")
    ]
    return headings, code_blocks, links


def _visible_atoms(
    value: str,
    *,
    ignore_format_controls: bool = False,
) -> list[str]:
    value = html.unescape(value)
    if ignore_format_controls:
        value = "".join(
            character
            for character in value
            if unicodedata.category(character) != "Cf"
        )
    return VISIBLE_ATOM_RE.findall(value)


def _markdown_visible_atom_records(
    text: str,
    *,
    ignore_format_controls: bool = False,
) -> list[tuple[str, int]]:
    """Project Markdown reader text without depending on a Pandoc version.

    Markdown-only delimiters are excluded, while reader-visible punctuation is
    retained so numeric and other punctuation drift cannot collapse to the same
    comparison value.
    """

    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "", text)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    visible_lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if re.match(r"^\s*```", line):
            continue
        if re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", line):
            continue
        if re.match(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", line):
            continue
        visible_lines.append(
            (
                line_number,
                re.sub(r"^\s*(?:#{1,6}|>|[-+*]|\d+[.)])\s+", "", line),
            )
        )
    records: list[tuple[str, int]] = []
    for line_number, line in visible_lines:
        position = 0
        for match in INLINE_CODE_RE.finditer(line):
            plain_text = UNDERSCORE_EMPHASIS_RE.sub(
                r"\g<body>", line[position : match.start()]
            )
            records.extend(
                (atom, line_number)
                for atom in _visible_atoms(
                    plain_text,
                    ignore_format_controls=ignore_format_controls,
                )
            )
            records.extend(
                (atom, line_number)
                for atom in _visible_atoms(
                    match.group("code"),
                    ignore_format_controls=ignore_format_controls,
                )
            )
            position = match.end()
        plain_text = UNDERSCORE_EMPHASIS_RE.sub(
            r"\g<body>", line[position:]
        )
        records.extend(
            (atom, line_number)
            for atom in _visible_atoms(
                plain_text,
                ignore_format_controls=ignore_format_controls,
            )
        )
    return records


def _markdown_visible_atoms(text: str) -> list[str]:
    return [atom for atom, _line_number in _markdown_visible_atom_records(text)]


def _issue_candidate_boundary_lines(language: str) -> tuple[str, ...]:
    if language == "en":
        return (
            "- An end-to-end Issue Candidate system.",
            "→ Issue Candidate system",
            "A future Issue Candidate system should follow this path:",
            "## Appendix H: Issue Candidate Boundary (Not Shipped)",
            "The Issue Candidate system has not shipped. This historical report "
            "retains only the product boundary: if implemented later, it must "
            "follow the existing deterministic-control, frozen-artifact, "
            "single-writer, and human-adjudication principles, and it must not "
            "grant agents self-approval or release authority.",
        )
    return (
        "- 端到端问题候选系统；",
        "→ 问题候选系统",
        "未来问题候选系统应遵循以下路径：",
        "## 附录 H：问题候选边界（未交付）",
        "问题候选系统尚未交付。本历史报告只保留产品边界：未来若实现，该机制必须遵守"
        "现有的确定性控制面、冻结工件、单写者和人工裁决原则，且不得赋予智能体自我批准"
        "或发布权威。",
    )


def _issue_candidate_occurrences(
    text: str,
    language: str,
) -> list[tuple[int, int]]:
    """Locate reader-visible Issue Candidate terms in either manuscript.

    The shared visible-atom projection removes Markdown presentation syntax,
    so soft line wraps, emphasis, inline code, and links cannot hide an
    additional current-capability claim from the canonical line inventory. The
    returned source-line span preserves provenance for section checks.
    """

    records = _markdown_visible_atom_records(
        text,
        ignore_format_controls=True,
    )
    if language == "zh-CN":
        # These separator atoms remain material to source/render parity, but
        # cannot split the protected Chinese identity here. This keeps the
        # identity guard independent from Markdown emphasis and the same
        # ASCII/Unicode hyphen family accepted by the English identity.
        records = [
            record
            for record in records
            if record[0] != "_" and record[0] not in ISSUE_CANDIDATE_HYPHENS
        ]
    folded_atoms = [atom.casefold() for atom, _line_number in records]
    occurrences: list[tuple[int, int]] = []
    if language == "zh-CN":
        chinese_term = ("问", "题", "候", "选")
        for index in range(len(folded_atoms) - len(chinese_term) + 1):
            if tuple(
                folded_atoms[index : index + len(chinese_term)]
            ) == chinese_term:
                end_index = index + len(chinese_term) - 1
                occurrences.append((records[index][1], records[end_index][1]))
        return occurrences

    if language != "en":
        raise ValueError(f"Unsupported report language: {language}")

    for index, atom in enumerate(folded_atoms):
        if atom in {"issuecandidate", "issuecandidates"}:
            occurrences.append((records[index][1], records[index][1]))
            continue
        if atom != "issue":
            continue
        candidate_index = index + 1
        while (
            candidate_index < len(folded_atoms)
            and folded_atoms[candidate_index] in ISSUE_CANDIDATE_HYPHENS
        ):
            candidate_index += 1
        if candidate_index < len(folded_atoms) and folded_atoms[candidate_index] in {
            "candidate",
            "candidates",
        }:
            occurrences.append((records[index][1], records[candidate_index][1]))
    return occurrences


def _issue_candidate_context_errors(text: str, language: str) -> list[str]:
    if language == "en":
        candidate_pattern = re.compile(
            r"\bissue(?:\s+|\s*[-\u2010-\u2015]+\s*)candidates?\b",
            re.IGNORECASE,
        )
        allowed_headings = {
            "8.4 Not Shipped",
            "11.3 Support-Sufficiency Direction",
            "11.4 From Failure to Improvement",
            "Appendix H: Issue Candidate Boundary (Not Shipped)",
        }
    else:
        candidate_pattern = re.compile("问题候选")
        allowed_headings = {
            "8.4 尚未交付",
            "11.3 支持充分性方向",
            "11.4 从失败到改进",
            "附录 H：问题候选边界（未交付）",
        }

    errors: list[str] = []
    current_heading = ""
    heading_by_line: dict[int, str] = {}
    candidate_lines: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if heading:
            current_heading = _strip_markdown_inline(heading.group(1))
        heading_by_line[line_number] = current_heading
        if candidate_pattern.search(line):
            candidate_lines.append(line.strip())
    expected_lines = _issue_candidate_boundary_lines(language)
    inventory_matches = tuple(candidate_lines) == expected_lines
    occurrences = _issue_candidate_occurrences(text, language)
    for start_line, _end_line in occurrences:
        if heading_by_line.get(start_line, "") not in allowed_headings:
            errors.append(
                f"{language}: Issue Candidate appears outside an explicit "
                f"future/not-shipped section at Markdown line {start_line}"
            )
    inventory_matches = inventory_matches and (
        len(occurrences) == len(expected_lines)
    )
    if not inventory_matches:
        errors.append(
            f"{language}: canonical future/not-shipped Issue Candidate line "
            "inventory differs"
        )
    return errors


def _snapshot_boundary_errors(
    surface_name: str,
    text: str,
    *,
    language: str,
    baseline: bool = False,
) -> list[str]:
    errors: list[str] = []
    if baseline:
        declaration = f"**Immutable tag**: `{TAG_NAME}` (`{TAG_SHA}`)"
        workbuddy_declaration = (
            "v0.11.12 operator runtime, source-clone WorkBuddy Skill bundle, "
            "semantic adjudicate"
        )
    elif language == "en":
        declaration = f"**Code snapshot:** {TAG_NAME} (tag `{TAG_SHA}`)"
        workbuddy_declaration = (
            "| v0.11.12 | Runtime and operator surfaces | Operator runtime, "
            "human semantic adjudication, and the source-clone WorkBuddy "
            "Skill bundle |"
        )
        feedback_issue_declaration = (
            "Its control units are not code diffs but material claims, evidence "
            "spans, support records, `FeedbackIssue` records, repair tasks, and "
            "delivery decisions."
        )
    else:
        declaration = f"**代码快照**：{TAG_NAME}（tag `{TAG_SHA}`）"
        workbuddy_declaration = (
            "| v0.11.12 | 运行时与操作面 | operator 运行时、人工语义裁决和 "
            "source-clone WorkBuddy Skill bundle |"
        )
        feedback_issue_declaration = (
            "控制单元不再是代码差异，而是重要声明、证据片段、支持记录、"
            "`FeedbackIssue`、修复任务和交付决定。"
        )
    if declaration not in text:
        errors.append(f"{surface_name}: immutable tag name/SHA declaration differs")
    if workbuddy_declaration not in text:
        errors.append(f"{surface_name}: source-clone WorkBuddy boundary is missing")
    if not baseline and feedback_issue_declaration not in text:
        errors.append(f"{surface_name}: shipped FeedbackIssue boundary is missing")
    folded = text.casefold()
    for post_snapshot_name in ("codebuddy", "gmail"):
        if post_snapshot_name in folded:
            errors.append(
                f"{surface_name}: post-v0.11.12 surface "
                f"{post_snapshot_name} appears in the historical snapshot"
            )
    return errors


def check_report_pair(pair: ReportPair, css_path: Path = CSS_PATH) -> list[str]:
    errors: list[str] = []
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    html_text = pair.html.read_text(encoding="utf-8")
    markdown_headings, markdown_code, markdown_links = _markdown_structure(
        markdown_text
    )
    parser = _ReportHtmlParser()
    parser.feed(html_text)

    if markdown_headings != parser.headings:
        errors.append(f"{pair.language}: Markdown/HTML heading sequence differs")
    if markdown_code != parser.code_blocks:
        errors.append(f"{pair.language}: Markdown/HTML fenced-code sequence differs")
    if _markdown_visible_atoms(markdown_text) != VISIBLE_ATOM_RE.findall(
        html.unescape("\n".join(parser.body_parts))
    ):
        errors.append(f"{pair.language}: Markdown/HTML visible body atoms differ")
    if Counter(markdown_links) != Counter(parser.links):
        errors.append(f"{pair.language}: Markdown/HTML non-fragment links differ")

    duplicate_ids = sorted(
        identifier
        for identifier, count in Counter(parser.ids).items()
        if count > 1
    )
    if duplicate_ids:
        errors.append(f"{pair.language}: duplicate HTML ids: {duplicate_ids}")
    missing_fragments = sorted(set(parser.fragments) - set(parser.ids))
    if missing_fragments:
        errors.append(
            f"{pair.language}: unresolved HTML fragments: {missing_fragments}"
        )

    css_text = css_path.read_text(encoding="utf-8")
    if _normalize_css("".join(parser.style_parts)) != _normalize_css(css_text):
        errors.append(f"{pair.language}: embedded CSS differs from source CSS")

    if len(parser.embedded_svg_payloads) != 1:
        errors.append(
            f"{pair.language}: expected one embedded SVG, found "
            f"{len(parser.embedded_svg_payloads)}"
        )
    else:
        try:
            embedded_svg = base64.b64decode(
                parser.embedded_svg_payloads[0], validate=True
            )
        except ValueError:
            errors.append(f"{pair.language}: embedded SVG is not valid base64")
        else:
            if _canonical_text_asset_bytes(
                embedded_svg
            ) != _canonical_text_asset_bytes(pair.svg.read_bytes()):
                errors.append(f"{pair.language}: embedded SVG differs from source SVG")

    errors.extend(
        _snapshot_boundary_errors(
            pair.markdown.name,
            markdown_text,
            language=pair.language,
        )
    )
    folded_html = html_text.casefold()
    for post_snapshot_name in ("codebuddy", "gmail"):
        if post_snapshot_name in folded_html:
            errors.append(
                f"{pair.html.name}: post-v0.11.12 surface "
                f"{post_snapshot_name} appears in the historical snapshot"
            )
    errors.extend(_issue_candidate_context_errors(markdown_text, pair.language))

    if pair.language == "en":
        if "post-snapshot" not in markdown_text or "post-snapshot" not in html_text:
            errors.append("en: post-snapshot Pilot tracking boundary is missing")
    else:
        if "快照后" not in markdown_text or "快照后" not in html_text:
            errors.append("zh-CN: 快照后的 Pilot 跟踪边界缺失")
    return errors


def _check_supporting_notes() -> list[str]:
    errors: list[str] = []
    baseline = (
        ROOT / "docs" / "tech-report-v0.4.0" / "implementation-baseline-v0.11.md"
    ).read_text(encoding="utf-8")
    errors.extend(
        _snapshot_boundary_errors(
            "implementation baseline",
            baseline,
            language="en",
            baseline=True,
        )
    )

    status = (
        ROOT / "docs" / "tech-report-v0.4.0" / "v09-implementation-status.md"
    ).read_text(encoding="utf-8")
    reviewer_response = (
        ROOT
        / "docs"
        / "tech-report-v0.4.0"
        / "response-to-reviewers-v0.4.0.md"
    ).read_text(encoding="utf-8")
    for note_name, note_text in (
        ("v0.9 implementation status", status),
        ("reviewer response", reviewer_response),
    ):
        if "Finding Candidate" in note_text:
            errors.append(
                f"{note_name}: use the canonical Issue Candidate term"
            )
    command_block = next(
        (
            block
            for block in re.findall(r"```(?:bash)?\n(.*?)```", status, re.DOTALL)
            if "semantic-support adjudicate" in block
        ),
        "",
    )
    required_flags = ("--workspace", "--proposal-id", "--decision", "--reason")
    missing_flags = [flag for flag in required_flags if flag not in command_block]
    if missing_flags:
        errors.append(
            "semantic-support adjudicate example is missing required flags: "
            + ", ".join(missing_flags)
        )
    return errors


def check() -> list[str]:
    errors: list[str] = []
    for pair in REPORT_PAIRS:
        errors.extend(check_report_pair(pair))
    errors.extend(_check_supporting_notes())
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Architecture Reference v0.4.0 check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Architecture Reference v0.4.0 source/render check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
