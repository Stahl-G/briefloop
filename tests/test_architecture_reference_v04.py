"""Tests for the Architecture Reference v0.4 source/render contract."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_architecture_reference_v04.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_architecture_reference_v04_test",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_architecture_reference_source_render_check_runs_clean() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "source/render check passed" in result.stdout


def test_architecture_reference_checker_rejects_heading_drift(tmp_path) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    broken_html.write_text(
        html_text.replace("<h2>Abstract</h2>", "<h2>Changed Abstract</h2>", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert "en: Markdown/HTML heading sequence differs" in errors


@pytest.mark.parametrize("pair_index", [0, 1])
def test_architecture_reference_checker_rejects_visible_body_drift(
    tmp_path,
    pair_index: int,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    marker = "At v0.11.12, BriefLoop" if pair.language == "en" else "v0.11.12 已形成"
    broken_html.write_text(
        html_text.replace(marker, f"changed-{marker}", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert f"{pair.language}: Markdown/HTML visible body atoms differ" in errors


def test_architecture_reference_checker_preserves_format_control_body_drift(
    tmp_path,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    marker = "At v0.11.12, BriefLoop"
    assert marker in html_text
    broken_html.write_text(
        html_text.replace(marker, "At v0.11.12,\u200b BriefLoop", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert "en: Markdown/HTML visible body atoms differ" in errors


@pytest.mark.parametrize(
    ("pair_index", "old", "new"),
    [
        (0, "v0.11.12 已形成", "v0,11.12 已形成"),
        (1, "more than 2,767 deterministic", "more than 2.767 deterministic"),
    ],
)
def test_architecture_reference_checker_rejects_material_punctuation_drift(
    tmp_path,
    pair_index: int,
    old: str,
    new: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    assert old in html_text
    broken_html.write_text(html_text.replace(old, new, 1), encoding="utf-8")
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert f"{pair.language}: Markdown/HTML visible body atoms differ" in errors


def test_architecture_reference_checker_preserves_literal_underscores(
    tmp_path,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    assert "artifact_registry.json" in html_text
    broken_html.write_text(
        html_text.replace("artifact_registry.json", "artifact registry.json", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert "en: Markdown/HTML visible body atoms differ" in errors


def test_markdown_visible_atoms_distinguish_emphasis_from_literal_underscore() -> None:
    module = _load_module()

    markdown_atoms = module._markdown_visible_atoms(
        "_emphasis_ and `artifact_registry.json`"
    )
    rendered_atoms = module.VISIBLE_ATOM_RE.findall(
        "emphasis and artifact_registry.json"
    )

    assert markdown_atoms == rendered_atoms


def test_architecture_reference_checker_rejects_asset_drift(tmp_path) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[0]
    broken_svg = tmp_path / pair.svg.name
    broken_svg.write_bytes(pair.svg.read_bytes() + b"\n")
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=pair.html,
        svg=broken_svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert "zh-CN: embedded SVG differs from source SVG" in errors


def test_architecture_reference_checker_accepts_svg_crlf_checkout(tmp_path) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    crlf_svg = tmp_path / pair.svg.name
    canonical_svg = module._canonical_text_asset_bytes(pair.svg.read_bytes())
    crlf_svg.write_bytes(canonical_svg.replace(b"\n", b"\r\n"))
    crlf_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=pair.html,
        svg=crlf_svg,
    )

    errors = module.check_report_pair(crlf_pair)

    assert "en: embedded SVG differs from source SVG" not in errors


@pytest.mark.parametrize(
    ("pair_index", "candidate_text"),
    [(0, "问题候选是当前控制单元。"), (1, "Issue Candidate is a current control unit.")],
)
def test_architecture_reference_checker_rejects_current_issue_candidate(
    tmp_path,
    pair_index: int,
    candidate_text: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_markdown = tmp_path / pair.markdown.name
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    broken_markdown.write_text(
        markdown_text.replace("\n---\n", f"\n{candidate_text}\n\n---\n", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=pair.html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any("outside an explicit future/not-shipped section" in error for error in errors)


@pytest.mark.parametrize(
    ("pair_index", "old", "new"),
    [
        (
            0,
            "问题候选系统尚未交付。",
            "问题候选系统当前已经交付。",
        ),
        (
            1,
            "The Issue Candidate system has not shipped.",
            "The Issue Candidate system is currently shipped.",
        ),
    ],
)
def test_architecture_reference_checker_rejects_coordinated_shipped_claim(
    tmp_path,
    pair_index: int,
    old: str,
    new: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_markdown = tmp_path / pair.markdown.name
    broken_html = tmp_path / pair.html.name
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    html_text = pair.html.read_text(encoding="utf-8")
    assert old in markdown_text
    assert old in html_text
    broken_markdown.write_text(
        markdown_text.replace(old, new, 1),
        encoding="utf-8",
    )
    broken_html.write_text(html_text.replace(old, new, 1), encoding="utf-8")
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any(
        "canonical future/not-shipped Issue Candidate line inventory differs" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("markdown_claim", "html_claim"),
    [
        (
            "Issue-Candidate system is currently shipped.",
            "Issue-Candidate system is currently shipped.",
        ),
        (
            "Issue -Candidate system is currently shipped.",
            "Issue -Candidate system is currently shipped.",
        ),
        (
            "Issue- Candidate system is currently shipped.",
            "Issue- Candidate system is currently shipped.",
        ),
        (
            "Issue - Candidate system is currently shipped.",
            "Issue - Candidate system is currently shipped.",
        ),
        (
            "Issue – Candidate system is currently shipped.",
            "Issue – Candidate system is currently shipped.",
        ),
        (
            "Issue\nCandidate system is currently shipped.",
            "Issue Candidate system is currently shipped.",
        ),
        (
            "Issue _Candidate_ system is currently shipped.",
            "Issue <em>Candidate</em> system is currently shipped.",
        ),
        (
            "Issue **Candidate** system is currently shipped.",
            "Issue <strong>Candidate</strong> system is currently shipped.",
        ),
        (
            "Issue `Candidate` system is currently shipped.",
            "Issue <code>Candidate</code> system is currently shipped.",
        ),
        (
            "Issue [Candidate](https://example.com/issue-candidate) system is "
            "currently shipped.",
            'Issue <a href="https://example.com/issue-candidate">Candidate</a> '
            "system is currently shipped.",
        ),
        (
            "Issue \u200bCandidate system is currently shipped.",
            "Issue \u200bCandidate system is currently shipped.",
        ),
        (
            "Issue \u2060Candidate system is currently shipped.",
            "Issue \u2060Candidate system is currently shipped.",
        ),
        (
            "Issue \u00adCandidate system is currently shipped.",
            "Issue \u00adCandidate system is currently shipped.",
        ),
        (
            "Issue\u200bCandidate system is currently shipped.",
            "Issue\u200bCandidate system is currently shipped.",
        ),
        (
            "Issue\u2060Candidate system is currently shipped.",
            "Issue\u2060Candidate system is currently shipped.",
        ),
        (
            "Issue\u00adCandidate system is currently shipped.",
            "Issue\u00adCandidate system is currently shipped.",
        ),
        (
            "IssueCandidate system is currently shipped.",
            "IssueCandidate system is currently shipped.",
        ),
    ],
)
def test_architecture_reference_checker_rejects_disguised_issue_candidate_claim(
    tmp_path,
    markdown_claim: str,
    html_claim: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_markdown = tmp_path / pair.markdown.name
    broken_html = tmp_path / pair.html.name
    markdown_marker = (
        "The Issue Candidate system has not shipped. This historical report "
        "retains only the product boundary: if implemented later, it must follow "
        "the existing deterministic-control, frozen-artifact, single-writer, and "
        "human-adjudication principles, and it must not grant agents self-approval "
        "or release authority."
    )
    html_marker = "grant agents self-approval or release authority.</p>"
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    html_text = pair.html.read_text(encoding="utf-8")
    assert markdown_marker in markdown_text
    assert html_marker in html_text
    broken_markdown.write_text(
        markdown_text.replace(
            markdown_marker,
            f"{markdown_marker}\n\n{markdown_claim}",
            1,
        ),
        encoding="utf-8",
    )
    broken_html.write_text(
        html_text.replace(
            html_marker,
            f"{html_marker}\n      <p>{html_claim}</p>",
            1,
        ),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any(
        "canonical future/not-shipped Issue Candidate line inventory differs" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("pair_index", "candidate_text"),
    [
        (1, "Issue\n_Candidate_ is a current control unit."),
        (0, "问题\n**候选**系统是当前控制单元。"),
    ],
)
def test_architecture_reference_checker_preserves_disguised_candidate_provenance(
    tmp_path,
    pair_index: int,
    candidate_text: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_markdown = tmp_path / pair.markdown.name
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    broken_markdown.write_text(
        markdown_text.replace(
            "\n---\n",
            f"\n{candidate_text}\n\n---\n",
            1,
        ),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=pair.html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any(
        "Issue Candidate appears outside an explicit future/not-shipped section"
        in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("markdown_claim", "html_claim"),
    [
        (
            "问题**候选**系统当前已经交付。",
            "问题<strong>候选</strong>系统当前已经交付。",
        ),
        (
            "问题_候选_系统当前已经交付。",
            "问题<em>候选</em>系统当前已经交付。",
        ),
        (
            "问题`候选`系统当前已经交付。",
            "问题<code>候选</code>系统当前已经交付。",
        ),
        (
            "问题\n候选系统当前已经交付。",
            "问题候选系统当前已经交付。",
        ),
        (
            "问题[候选](https://example.com/issue-candidate)系统当前已经交付。",
            '问题<a href="https://example.com/issue-candidate">候选</a>'
            "系统当前已经交付。",
        ),
        (
            "问题-候选系统当前已经交付。",
            "问题-候选系统当前已经交付。",
        ),
        (
            "问题 – 候选系统当前已经交付。",
            "问题 – 候选系统当前已经交付。",
        ),
        (
            "问题\u200b候选系统当前已经交付。",
            "问题\u200b候选系统当前已经交付。",
        ),
        (
            "问题\u2060候选系统当前已经交付。",
            "问题\u2060候选系统当前已经交付。",
        ),
        (
            "问题\u00ad候选系统当前已经交付。",
            "问题\u00ad候选系统当前已经交付。",
        ),
    ],
)
def test_architecture_reference_checker_rejects_disguised_chinese_candidate_claim(
    tmp_path,
    markdown_claim: str,
    html_claim: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[0]
    broken_markdown = tmp_path / pair.markdown.name
    broken_html = tmp_path / pair.html.name
    markdown_marker = (
        "问题候选系统尚未交付。本历史报告只保留产品边界：未来若实现，该机制必须遵守"
        "现有的确定性控制面、冻结工件、单写者和人工裁决原则，且不得赋予智能体自我批准"
        "或发布权威。"
    )
    html_marker = f"{markdown_marker}</p>"
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    html_text = pair.html.read_text(encoding="utf-8")
    assert markdown_marker in markdown_text
    assert html_marker in html_text
    broken_markdown.write_text(
        markdown_text.replace(
            markdown_marker,
            f"{markdown_marker}\n\n{markdown_claim}",
            1,
        ),
        encoding="utf-8",
    )
    broken_html.write_text(
        html_text.replace(
            html_marker,
            f"{html_marker}\n      <p>{html_claim}</p>",
            1,
        ),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any(
        "canonical future/not-shipped Issue Candidate line inventory differs" in error
        for error in errors
    )


def test_architecture_reference_checker_preserves_css_string_whitespace(
    tmp_path,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_html = tmp_path / pair.html.name
    html_text = pair.html.read_text(encoding="utf-8")
    assert '"Segoe UI"' in html_text
    broken_html.write_text(
        html_text.replace('"Segoe UI"', '"SegoeUI"', 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=pair.markdown,
        html=broken_html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert "en: embedded CSS differs from source CSS" in errors


@pytest.mark.parametrize("post_snapshot_name", ["codebuddy", "GMAIL"])
def test_architecture_reference_checker_rejects_post_snapshot_names_case_insensitively(
    tmp_path,
    post_snapshot_name: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[1]
    broken_markdown = tmp_path / pair.markdown.name
    markdown_text = pair.markdown.read_text(encoding="utf-8")
    broken_markdown.write_text(
        markdown_text.replace("\n---\n", f"\n{post_snapshot_name}\n\n---\n", 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=pair.html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any("post-v0.11.12 surface" in error for error in errors)


@pytest.mark.parametrize(
    ("pair_index", "old", "new", "expected_error"),
    [
        (
            1,
            "**Code snapshot:** v0.11.12",
            "**Code snapshot:** v0.12.0",
            "immutable tag name/SHA declaration differs",
        ),
        (
            1,
            "source-clone WorkBuddy Skill bundle",
            "hosted WorkBuddy delegation",
            "source-clone WorkBuddy boundary is missing",
        ),
        (
            0,
            "source-clone WorkBuddy Skill bundle",
            "托管式 WorkBuddy 委派",
            "source-clone WorkBuddy boundary is missing",
        ),
        (
            1,
            "`FeedbackIssue` records",
            "generic feedback records",
            "shipped FeedbackIssue boundary is missing",
        ),
        (
            0,
            "`FeedbackIssue`、修复任务",
            "通用反馈记录、修复任务",
            "shipped FeedbackIssue boundary is missing",
        ),
    ],
)
def test_architecture_reference_checker_locks_snapshot_identity_and_workbuddy_scope(
    tmp_path,
    pair_index: int,
    old: str,
    new: str,
    expected_error: str,
) -> None:
    module = _load_module()
    pair = module.REPORT_PAIRS[pair_index]
    broken_markdown = tmp_path / pair.markdown.name
    broken_markdown.write_text(
        pair.markdown.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    broken_pair = module.ReportPair(
        language=pair.language,
        markdown=broken_markdown,
        html=pair.html,
        svg=pair.svg,
    )

    errors = module.check_report_pair(broken_pair)

    assert any(expected_error in error for error in errors)


def test_architecture_reference_checker_locks_snapshot_and_cli_boundaries() -> None:
    module = _load_module()

    assert module._check_supporting_notes() == []
    for pair in module.REPORT_PAIRS:
        markdown_text = pair.markdown.read_text(encoding="utf-8")
        html_text = pair.html.read_text(encoding="utf-8")
        assert module.TAG_SHA in markdown_text
        assert module.TAG_SHA in html_text
        assert "CodeBuddy" not in markdown_text
        assert "CodeBuddy" not in html_text
        assert "Gmail" not in markdown_text
        assert "Gmail" not in html_text
    supporting_notes = (
        ROOT / "docs" / "tech-report-v0.4.0" / "v09-implementation-status.md",
        ROOT
        / "docs"
        / "tech-report-v0.4.0"
        / "response-to-reviewers-v0.4.0.md",
    )
    for note in supporting_notes:
        note_text = note.read_text(encoding="utf-8")
        assert "Finding Candidate" not in note_text
        assert "Issue Candidate" in note_text
