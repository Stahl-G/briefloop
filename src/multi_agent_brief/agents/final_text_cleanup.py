"""Reusable cleanup utilities for removing process residue from brief text.

Used by the deterministic EditorAgent and can be referenced by the Claude Code
editor subagent for consistent cleanup behavior.
"""
from __future__ import annotations

import re

# Patterns that match internal process residue — must be removed from final text.
_RESIDUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\[SRC:\]"),
    re.compile(r"\[SOURCE:\]"),
    re.compile(r"\[src:\s*\]"),  # empty [src:] with optional whitespace
    re.compile(r"Thought for\b[^\n]*", re.IGNORECASE),
    re.compile(r"Bash\([^\n]*\)"),
    re.compile(r"Agent completed\b[^\n]*", re.IGNORECASE),
    re.compile(r"audit in background\b[^\n]*", re.IGNORECASE),
]

# Valid claim citation pattern: [src:CLAIM_ID] where CLAIM_ID is 6+ uppercase/digit/underscore.
_VALID_SRC_REF = re.compile(r"\[src:[A-Z0-9_]{6,}\]")


def clean_process_residue(text: str) -> str:
    """Remove process residue while preserving valid [src:CLAIM_ID] citations.

    Args:
        text: The markdown text to clean.

    Returns:
        Cleaned text with residue removed and excessive blank lines collapsed.
    """
    for pattern in _RESIDUE_PATTERNS:
        text = pattern.sub("", text)
    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def validate_citations_intact(original: str, cleaned: str) -> bool:
    """Verify that all valid [src:CLAIM_ID] citations survive cleanup.

    Args:
        original: The original text before cleanup.
        cleaned: The text after cleanup.

    Returns:
        True if every valid citation in original is also in cleaned.
    """
    original_refs = set(_VALID_SRC_REF.findall(original))
    cleaned_refs = set(_VALID_SRC_REF.findall(cleaned))
    return original_refs.issubset(cleaned_refs)
