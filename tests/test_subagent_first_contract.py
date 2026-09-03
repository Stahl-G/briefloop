from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_first_screen_uses_briefloop_as_writer_command():
    readme_paths = ["README.md", "README.zh-CN.md"]
    for path in readme_paths:
        text = _read(path)
        first_screen = "\n".join(text.splitlines()[:32])
        assert "/briefloop" in first_screen
        assert "/mabw" not in first_screen
        assert "multi-agent-brief" not in first_screen
        assert "MABW" not in first_screen
        assert "BriefLoop" in first_screen
        assert "/generate-brief" not in first_screen

    readme_en = _read("README_en.md")
    assert "English README has moved to [README.md](README.md)." in readme_en
