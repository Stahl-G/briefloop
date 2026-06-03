"""Public-safe content audit.

Ensures no real personal identity, employer, client, or sensitive internal
context leaks into public-facing files.

Rules are imported from scripts/public_safe_scan.py (single source of truth).
This test file itself must NOT contain real personal names or real company names.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Import the shared scanner module
sys.path.insert(0, str(REPO / "scripts"))
from public_safe_scan import (  # noqa: E402
    PUBLIC_DIRS,
    PUBLIC_TARGETS,
    SENSITIVE_PATTERNS,
    ScanResult,
    collect_files,
    scan_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hits_for_category(result: ScanResult, category_substr: str) -> list:
    """Filter hits by pattern description substring."""
    return [h for h in result.hits if category_substr in h.pattern_desc]


# ---------------------------------------------------------------------------
# Tests — generic rules via shared scanner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [str(p.relative_to(REPO)) for p in PUBLIC_TARGETS if p.exists()],
    ids=[p.name for p in PUBLIC_TARGETS if p.exists()],
)
def test_public_file_no_sensitive_content(target: str) -> None:
    """Each public-facing file must not contain real personal/company sentinels."""
    path = REPO / target
    hits = scan_file(path)
    if hits:
        msg_lines = [f"Sensitive content found in {target}:"]
        for h in hits:
            msg_lines.append(f"  {h.file}:{h.line} [{h.pattern_desc}] matched '{h.match}'")
        pytest.fail("\n".join(msg_lines))


def test_no_real_email_in_public_files() -> None:
    """No real email addresses in public-facing files (test@example.com is OK)."""
    for f in collect_files():
        hits = scan_file(f)
        email_hits = _hits_for_category(ScanResult(hits=hits), "email")
        if email_hits:
            msg_lines = [f"Real email found in {f.relative_to(REPO)}:"]
            for h in email_hits:
                msg_lines.append(f"  {h.file}:{h.line}: {h.match}")
            pytest.fail("\n".join(msg_lines))


def test_no_hardcoded_credentials() -> None:
    """No hardcoded API keys, tokens, or passwords in public files."""
    for f in collect_files():
        hits = scan_file(f)
        cred_hits = _hits_for_category(ScanResult(hits=hits), "credential")
        if cred_hits:
            msg_lines = [f"Hardcoded credential in {f.relative_to(REPO)}:"]
            for h in cred_hits:
                msg_lines.append(f"  {h.file}:{h.line}: {h.pattern_desc}")
            pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# Tests — scanner script runs and passes
# ---------------------------------------------------------------------------


def test_scanner_script_passes() -> None:
    """The standalone scanner script should exit 0 on a clean repo."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "public_safe_scan.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, f"Scanner failed:\n{result.stdout}\n{result.stderr}"


def test_scanner_json_output() -> None:
    """The scanner --json flag should produce valid JSON with ok=true."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "public_safe_scan.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, f"Scanner failed:\n{result.stdout}"
    import json
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["hit_count"] == 0


# ---------------------------------------------------------------------------
# Tests — specific code files use only generic labels
# ---------------------------------------------------------------------------


def test_mapper_profiles_are_generic() -> None:
    """Source profile mapper should use generic company/industry names."""
    mapper_path = REPO / "src" / "multi_agent_brief" / "onboarding" / "mapper.py"
    if not mapper_path.exists():
        pytest.skip("mapper.py not found")

    # The shared scanner catches real company names, personal names, handles
    hits = scan_file(mapper_path)
    real_hits = [
        h for h in hits
        if "company" in h.pattern_desc or "personal" in h.pattern_desc or "handle" in h.pattern_desc
    ]
    if real_hits:
        msg_lines = ["Real identifier in mapper.py:"]
        for h in real_hits:
            msg_lines.append(f"  line {h.line}: [{h.pattern_desc}] '{h.match}'")
        pytest.fail("\n".join(msg_lines))


def test_init_wizard_roles_are_generic() -> None:
    """Init wizard role prompts should use generic labels, not real employer context."""
    wizard_path = REPO / "src" / "multi_agent_brief" / "cli" / "init_wizard.py"
    if not wizard_path.exists():
        pytest.skip("init_wizard.py not found")

    hits = scan_file(wizard_path)
    real_hits = [
        h for h in hits
        if "company" in h.pattern_desc or "personal" in h.pattern_desc or "handle" in h.pattern_desc
    ]
    if real_hits:
        msg_lines = [f"Real identifier in init_wizard.py:"]
        for h in real_hits:
            msg_lines.append(f"  line {h.line}: [{h.pattern_desc}] '{h.match}'")
        pytest.fail("\n".join(msg_lines))
