"""Reusable cleanup utilities for removing process residue from draft text.

Used by final quality and finalize helpers, and available to external editor subagents for consistent cleanup behavior.
"""
from __future__ import annotations

import re

from multi_agent_brief.core.citations import VALID_SRC_REF_PATTERN

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

# Final Clean patterns — detect issues but do NOT auto-remove (require explicit gate).
# Each entry: (pattern, finding_type, severity, description_template)
FINAL_CLEAN_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    # Template variables and unresolved placeholders
    (re.compile(r"\{\{[^}]+\}\}"), "template_variable_residue", "high",
     "Template variable {{{{...}}}} found"),
    (re.compile(r"\$\{[^}]+\}"), "template_variable_residue", "high",
     "Template variable ${{...}} found"),
    (re.compile(r"<TODO>", re.IGNORECASE), "template_variable_residue", "high",
     "Unresolved <TODO> placeholder found"),
    (re.compile(r"<PLACEHOLDER>", re.IGNORECASE), "template_variable_residue", "high",
     "Unresolved <PLACEHOLDER> found"),

    # Internal file paths (absolute or relative paths)
    (re.compile(r"(?:^|\s)(?:/[a-zA-Z0-9_./-]{10,}|(?:\.\./){2,}[a-zA-Z0-9_./-]+)"), "internal_path_leak", "high",
     "Internal file path exposed in text"),

    # Model/AI process phrases
    (re.compile(r"\bas an AI\b", re.IGNORECASE), "model_phrase_residue", "medium",
     "Model phrase 'as an AI' found"),
    (re.compile(r"\bagent should\b", re.IGNORECASE), "model_phrase_residue", "medium",
     "Model phrase 'agent should' found"),
    (re.compile(r"\bnext run should\b", re.IGNORECASE), "model_phrase_residue", "medium",
     "Model phrase 'next run should' found"),
    (re.compile(r"\bI am an AI\b", re.IGNORECASE), "model_phrase_residue", "medium",
     "Model phrase 'I am an AI' found"),

    # User feedback leakage (feedback presented as market fact)
    (re.compile(r"(?:用户反馈|feedback suggests|user reported|customer feedback)"), "feedback_as_fact", "high",
     "User feedback presented as market fact"),

    # Editorial comments in report body
    (re.compile(r"^(?:TODO:|FIXME:|NOTE:|HACK:|XXX:)", re.MULTILINE | re.IGNORECASE),
     "editorial_comment_as_conclusion", "medium",
     "Editorial comment found in report body"),

    # Investment/trading recommendation wording
    (re.compile(r"(?:强烈推荐|强烈买入|强烈卖出|strong buy|strong sell|目标价|target price)", re.IGNORECASE),
     "investment_recommendation", "high",
     "Investment recommendation language found"),
]

# Valid claim citation pattern: [src:<claim_id>] with stable alnum, underscore, or hyphen IDs.
_VALID_SRC_REF = VALID_SRC_REF_PATTERN


def clean_process_residue(text: str) -> str:
    """Remove process residue while preserving valid [src:<claim_id>] citations.

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
    """Verify that all valid [src:<claim_id>] citations survive cleanup.

    Args:
        original: The original text before cleanup.
        cleaned: The text after cleanup.

    Returns:
        True if every valid citation in original is also in cleaned.
    """
    original_refs = set(_VALID_SRC_REF.findall(original))
    cleaned_refs = set(_VALID_SRC_REF.findall(cleaned))
    return original_refs.issubset(cleaned_refs)


def strip_claim_citations(text: str) -> str:
    """Remove all [src:<claim_id>] citations from text.

    Human readers cannot parse these internal references.
    Call this before writing the final reader-facing brief.

    Args:
        text: Markdown text with [src:<claim_id>] citations.

    Returns:
        Text with all [src:...] markers removed.
    """
    text = _VALID_SRC_REF.sub("", text)
    # Also strip any remaining [src:...] patterns (malformed, empty, etc.)
    text = re.compile(r"\[src:[^\]]*\]").sub("", text)
    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_final_clean_issues(text: str) -> list[dict]:
    """Detect Final Clean issues without auto-removing them.

    Returns a list of finding dicts with keys:
    - finding_type: str
    - severity: str ("high" or "medium")
    - description: str
    - line_number: int | None
    - evidence: str
    """
    findings: list[dict] = []
    for pattern, finding_type, severity, desc_template in FINAL_CLEAN_PATTERNS:
        for match in pattern.finditer(text):
            # Calculate line number
            line_number = text[:match.start()].count("\n") + 1
            # Extract evidence (the matched line)
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            evidence = text[start:end].strip()

            findings.append({
                "finding_type": finding_type,
                "severity": severity,
                "description": desc_template,
                "line_number": line_number,
                "evidence": evidence[:200],  # truncate long evidence
            })
    return findings


def detect_invalid_citations(text: str, valid_ids: set[str]) -> list[dict]:
    """Detect invalid or empty citation markers in text.

    Args:
        text: Markdown text to check.
        valid_ids: Set of valid claim IDs from the ledger.

    Returns:
        List of finding dicts for invalid citations.
    """
    findings: list[dict] = []

    # Find all [src:...] patterns
    for match in re.finditer(r"\[src:([^\]]*)\]", text):
        ref_id = match.group(1).strip()

        # Empty citation
        if not ref_id:
            line_number = text[:match.start()].count("\n") + 1
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            findings.append({
                "finding_type": "empty_source_marker",
                "severity": "medium",
                "description": "Empty source marker [src:] found",
                "line_number": line_number,
                "evidence": text[start:end].strip()[:200],
            })
            continue

        # Invalid claim ID (not in ledger)
        if ref_id not in valid_ids:
            line_number = text[:match.start()].count("\n") + 1
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            findings.append({
                "finding_type": "invalid_claim_id",
                "severity": "high",
                "description": f"Invalid claim ID [src:{ref_id}] not found in ledger",
                "line_number": line_number,
                "evidence": text[start:end].strip()[:200],
            })

    return findings
