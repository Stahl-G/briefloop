#!/usr/bin/env python3
"""Fetch GitHub pull-request reviewer comments for the current branch.

This is a maintainer convenience helper used by local git hooks. It is
intentionally best-effort: if `gh` is unavailable, unauthenticated, or the
current branch has no PR, it exits successfully after printing a short message.
Fetched data is written under `.git/briefloop-review-comments/`, not into the
working tree.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, help="Pull request number. Defaults to the current branch PR.")
    parser.add_argument("--repo", help="GitHub repository in OWNER/NAME form. Defaults to gh repo view.")
    parser.add_argument("--output-dir", type=Path, help="Output directory. Defaults to .git/briefloop-review-comments.")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error status output.")
    args = parser.parse_args(argv)

    if shutil.which("gh") is None:
        return _skip("gh CLI not found", quiet=args.quiet)

    root = _repo_root()
    if root is None:
        return _skip("not inside a git repository", quiet=args.quiet)

    repo = args.repo or _gh_json_value(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    if not repo:
        return _skip("could not resolve GitHub repository", quiet=args.quiet)

    pr_number = args.pr or _current_pr_number()
    if pr_number is None:
        return _skip("current branch has no GitHub PR", quiet=args.quiet)

    output_dir = args.output_dir or _default_output_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)

    pr = _gh_json(["pr", "view", str(pr_number), "--json", "number,title,url,headRefName,baseRefName,isDraft,mergeStateStatus,reviewDecision"])
    if not isinstance(pr, dict):
        return _skip(f"could not load PR #{pr_number}", quiet=args.quiet)

    inline_comments = _gh_paginated_json(["api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate"])
    reviews = _gh_paginated_json(["api", f"repos/{repo}/pulls/{pr_number}/reviews", "--paginate"])
    issue_comments = _gh_paginated_json(["api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate"])

    payload = {
        "schema_version": "briefloop.github_review_comments.v1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "pr": pr,
        "inline_comments": inline_comments,
        "reviews": reviews,
        "issue_comments": issue_comments,
    }
    json_path = output_dir / f"pr-{pr_number}-review-comments.json"
    md_path = output_dir / f"pr-{pr_number}-review-comments.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    if not args.quiet:
        print(f"[github-review-comments] wrote {md_path}")
        print(f"[github-review-comments] wrote {json_path}")
    return 0


def render_markdown(payload: dict[str, Any]) -> str:
    pr = payload.get("pr") if isinstance(payload.get("pr"), dict) else {}
    lines = [
        f"# GitHub Review Comments for PR #{pr.get('number', 'unknown')}",
        "",
        f"- Title: {pr.get('title', '')}",
        f"- URL: {pr.get('url', '')}",
        f"- Head: `{pr.get('headRefName', '')}`",
        f"- Base: `{pr.get('baseRefName', '')}`",
        f"- Draft: `{pr.get('isDraft', '')}`",
        f"- Merge state: `{pr.get('mergeStateStatus', '')}`",
        f"- Review decision: `{pr.get('reviewDecision', '')}`",
        f"- Fetched at: `{payload.get('fetched_at', '')}`",
        "",
        "## Inline Review Comments",
        "",
    ]
    inline_comments = _list(payload.get("inline_comments"))
    if not inline_comments:
        lines.append("_None._")
    for item in inline_comments:
        user = _login(item)
        path = item.get("path") or ""
        line = item.get("line") or item.get("original_line") or ""
        commit_id = item.get("commit_id") or ""
        url = item.get("html_url") or ""
        body = _body(item)
        lines.extend(
            [
                f"### {path}:{line} by {user}",
                "",
                f"- Commit: `{commit_id}`",
                f"- URL: {url}",
                "",
                body,
                "",
            ]
        )

    lines.extend(["## Review Bodies", ""])
    reviews = [item for item in _list(payload.get("reviews")) if _body(item).strip()]
    if not reviews:
        lines.append("_None._")
    for item in reviews:
        lines.extend(
            [
                f"### {_login(item)} - {item.get('state', '')}",
                "",
                f"- Submitted: `{item.get('submitted_at', '')}`",
                f"- Commit: `{item.get('commit_id', '')}`",
                f"- URL: {item.get('html_url', '')}",
                "",
                _body(item),
                "",
            ]
        )

    lines.extend(["## PR Conversation Comments", ""])
    issue_comments = _list(payload.get("issue_comments"))
    if not issue_comments:
        lines.append("_None._")
    for item in issue_comments:
        lines.extend(
            [
                f"### {_login(item)}",
                "",
                f"- Created: `{item.get('created_at', '')}`",
                f"- URL: {item.get('html_url', '')}",
                "",
                _body(item),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _repo_root() -> Path | None:
    result = _run(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _default_output_dir(root: Path) -> Path:
    git_dir = _run(["git", "rev-parse", "--git-dir"], cwd=root).stdout.strip()
    git_path = Path(git_dir)
    if not git_path.is_absolute():
        git_path = root / git_path
    return git_path / "briefloop-review-comments"


def _current_pr_number() -> int | None:
    value = _gh_json_value(["pr", "view", "--json", "number", "--jq", ".number"])
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _gh_json(args: list[str]) -> Any:
    result = _run(["gh", *args])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _gh_paginated_json(args: list[str]) -> list[dict[str, Any]]:
    result = _run(["gh", *args])
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = _parse_concatenated_json_arrays(result.stdout)
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _parse_concatenated_json_arrays(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    idx = 0
    items: list[Any] = []
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        value, idx = decoder.raw_decode(text, idx)
        if isinstance(value, list):
            items.extend(value)
    return items


def _gh_json_value(args: list[str]) -> str | None:
    result = _run(["gh", *args])
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _skip(reason: str, *, quiet: bool) -> int:
    if not quiet:
        print(f"[github-review-comments] skipped: {reason}", file=sys.stderr)
    return 0


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _login(item: dict[str, Any]) -> str:
    user = item.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        return user["login"]
    return "unknown"


def _body(item: dict[str, Any]) -> str:
    value = item.get("body")
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
