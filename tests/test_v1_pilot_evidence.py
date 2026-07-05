from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_v1_pilot_evidence.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT.parent.parent),
    )


def _satisfied_doc(artifact_path: str = "evidence.log", extra_fields: str = "") -> str:
    boundary_statement = (
        "- Boundary statement: traceability, not semantic proof; not output-quality proof; "
        "not delivery approval; not release authority.\n"
    )
    return f"""# v1 Pilot Evidence

Status: satisfied

## Required Evidence Types

- external fresh-clone smoke
- WorkBuddy Skill first-user smoke
- pilot user checklist
- recurring weekly-loop dogfood

## Required Record Fields

- what succeeded
- where the user got confused
- what failed
- what was fixed
- what remains known limitation

## Boundaries

- traceability, not semantic proof
- measurement infrastructure, not a benchmark claim
- not output-quality proof
- not delivery approval
- not release authority
- not legal, compliance, investment, disclosure, or publication approval

## Recorded Evidence

### Evidence Record: fresh clone smoke

- Evidence type: external fresh-clone smoke
- Date: 2026-07-05
- Runner: release operator
- Environment: fresh source clone
- Artifact or log path: {artifact_path}
- What succeeded: setup completed
- Where the user got confused: none observed
- What failed: none observed
- What was fixed: not fixed in this release
- What remains known limitation: not a semantic proof
""" + boundary_statement + extra_fields


def test_repo_v1_pilot_evidence_doc_passes_as_advisory() -> None:
    result = _run("--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "not_satisfied"
    assert payload["satisfied"] is False
    assert any(check["status"] == "warn" for check in payload["checks"])


def test_require_satisfied_fails_until_evidence_is_recorded() -> None:
    result = _run("--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "not_satisfied"
    assert any(check["id"] == "v1_pilot_evidence.require_satisfied" for check in payload["checks"])


def test_satisfied_evidence_record_passes_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    (tmp_path / "evidence.log").write_text("fresh clone smoke passed\n", encoding="utf-8")
    doc.write_text(_satisfied_doc(), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "satisfied"
    assert payload["satisfied"] is True
    assert payload["evidence_record_count"] == 1
    assert payload["valid_evidence_record_count"] == 1


def test_missing_boundary_phrase_fails(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(_satisfied_doc().replace("not delivery approval", "delivery approval"), encoding="utf-8")

    result = _run("--path", str(doc), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    failed_ids = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
    assert "v1_pilot_evidence.boundaries" in failed_ids


def test_satisfied_status_without_record_fails(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(
        _satisfied_doc().replace("### Evidence Record: fresh clone smoke", "### Evidence Template"),
        encoding="utf-8",
    )

    result = _run("--path", str(doc), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "satisfied"
    failed_ids = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
    assert "v1_pilot_evidence.recorded_evidence" in failed_ids


def test_empty_evidence_record_fails_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(
        _satisfied_doc().replace(
            "### Evidence Record: fresh clone smoke\n\n"
            "- Evidence type: external fresh-clone smoke\n"
            "- Date: 2026-07-05\n"
            "- Runner: release operator\n"
            "- Environment: fresh source clone\n"
            "- Artifact or log path: evidence.log\n"
            "- What succeeded: setup completed\n"
            "- Where the user got confused: none observed\n"
            "- What failed: none observed\n"
            "- What was fixed: not fixed in this release\n"
            "- What remains known limitation: not a semantic proof\n"
            "- Boundary statement: traceability, not semantic proof; not output-quality proof; "
            "not delivery approval; not release authority.\n",
            "### Evidence Record: empty record\n\nNo fields here.\n",
        ),
        encoding="utf-8",
    )

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["satisfied"] is False
    assert payload["evidence_record_count"] == 1
    assert payload["valid_evidence_record_count"] == 0
    failed_ids = {check["id"] for check in payload["checks"] if check["status"] == "fail"}
    assert "v1_pilot_evidence.recorded_evidence" in failed_ids


def test_missing_artifact_path_fails_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(_satisfied_doc(artifact_path="docs/does-not-exist.log"), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["satisfied"] is False
    assert payload["evidence_record_count"] == 1
    assert payload["valid_evidence_record_count"] == 0
    recorded = next(check for check in payload["checks"] if check["id"] == "v1_pilot_evidence.recorded_evidence")
    assert "does not exist" in recorded["detail"]


def test_invalid_evidence_date_fails_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    (tmp_path / "evidence.log").write_text("fresh clone smoke passed\n", encoding="utf-8")
    doc.write_text(_satisfied_doc().replace("- Date: 2026-07-05", "- Date: someday"), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["satisfied"] is False
    recorded = next(check for check in payload["checks"] if check["id"] == "v1_pilot_evidence.recorded_evidence")
    assert "Date must use YYYY-MM-DD" in recorded["detail"]


def test_future_evidence_date_fails_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    future = (date.today() + timedelta(days=1)).isoformat()
    (tmp_path / "evidence.log").write_text("fresh clone smoke passed\n", encoding="utf-8")
    doc.write_text(_satisfied_doc().replace("- Date: 2026-07-05", f"- Date: {future}"), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["satisfied"] is False
    recorded = next(check for check in payload["checks"] if check["id"] == "v1_pilot_evidence.recorded_evidence")
    assert "Date must not be in the future" in recorded["detail"]


def test_https_artifact_reference_passes_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(_satisfied_doc(artifact_path="https://briefloop.ai/evidence/v1.log"), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["satisfied"] is True


def test_external_artifact_reference_requires_note(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(_satisfied_doc(artifact_path="external: partner-run-log"), encoding="utf-8")

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    recorded = next(check for check in payload["checks"] if check["id"] == "v1_pilot_evidence.recorded_evidence")
    assert "External verification note" in recorded["detail"]


def test_external_artifact_reference_with_note_passes_required_mode(tmp_path: Path) -> None:
    doc = tmp_path / "v1-pilot-evidence.md"
    doc.write_text(
        _satisfied_doc(
            artifact_path="external: partner-run-log",
            extra_fields="- External verification note: stored in approved release evidence archive\n",
        ),
        encoding="utf-8",
    )

    result = _run("--path", str(doc), "--require-satisfied", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["satisfied"] is True
