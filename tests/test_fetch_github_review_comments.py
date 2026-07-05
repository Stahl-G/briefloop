from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_github_review_comments.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_github_review_comments", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_markdown_includes_inline_reviews_and_conversation_comments() -> None:
    module = _module()
    markdown = module.render_markdown(
        {
            "fetched_at": "2026-07-05T00:00:00+00:00",
            "pr": {
                "number": 123,
                "title": "demo",
                "url": "https://github.com/Stahl-G/briefloop/pull/123",
                "headRefName": "codex/demo",
                "baseRefName": "main",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "REVIEW_REQUIRED",
            },
            "inline_comments": [
                {
                    "path": "src/demo.py",
                    "line": 7,
                    "commit_id": "abc123",
                    "html_url": "https://example.test/inline",
                    "user": {"login": "reviewer"},
                    "body": "Fix this edge case.",
                }
            ],
            "reviews": [
                {
                    "state": "COMMENTED",
                    "submitted_at": "2026-07-05T00:01:00Z",
                    "commit_id": "abc123",
                    "html_url": "https://example.test/review",
                    "user": {"login": "reviewer"},
                    "body": "Overall review body.",
                }
            ],
            "issue_comments": [
                {
                    "created_at": "2026-07-05T00:02:00Z",
                    "html_url": "https://example.test/comment",
                    "user": {"login": "maintainer"},
                    "body": "Conversation note.",
                }
            ],
        }
    )

    assert "GitHub Review Comments for PR #123" in markdown
    assert "src/demo.py:7 by reviewer" in markdown
    assert "Fix this edge case." in markdown
    assert "Overall review body." in markdown
    assert "Conversation note." in markdown


def test_parse_concatenated_json_arrays_from_paginated_gh_output() -> None:
    module = _module()
    parsed = module._parse_concatenated_json_arrays('[{"id": 1}]\n[{"id": 2}]')

    assert parsed == [{"id": 1}, {"id": 2}]


def test_pr_view_args_honor_explicit_repo() -> None:
    module = _module()

    assert module._pr_view_args(437, repo="Stahl-G/briefloop", explicit_repo=True)[-2:] == [
        "--repo",
        "Stahl-G/briefloop",
    ]
    assert "--repo" not in module._pr_view_args(437, repo="Stahl-G/briefloop", explicit_repo=False)
