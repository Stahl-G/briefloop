from __future__ import annotations

import re


REDACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "api_key_hint": re.compile(r"(?i)\b(api[_-]?key|secret|token|webhook)\b\s*[:=]"),
    "absolute_path": re.compile(r"(/Users/[^ \n]+|/Volumes/[^ \n]+)"),
    "private_ip": re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"),
}


def scan_redaction_risks(text: str) -> list[dict]:
    findings: list[dict] = []
    for risk_type, pattern in REDACTION_PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(
                {
                    "type": risk_type,
                    "text": match.group(0)[:120],
                    "start": match.start(),
                    "severity": "high" if risk_type in {"api_key_hint", "absolute_path"} else "medium",
                    "recommendation": "Remove or replace before publishing.",
                }
            )
    return findings

