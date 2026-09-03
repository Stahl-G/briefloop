from __future__ import annotations

import json
from pathlib import Path

from multi_agent_brief.outputs.finalize import (
    interpret_finalize_audit_binding,
    require_finalize_audit_binding_pass,
)
from tests.helpers import sha256_file as _sha256_file


def _write_single_claim_ledger(
    path: Path,
    *,
    claim_id: str = "CL-001",
    source_url: str = "https://example.com/exampleco-demo",
) -> None:
    claims = [
        {
            "claim_id": claim_id,
            "statement": "ExampleCo opened a public demo facility in June 2026.",
            "source_id": "SRC-001",
            "evidence_text": "ExampleCo opened a public demo facility in June 2026.",
            "source_url": source_url,
            "source_type": "web_search",
            "metadata": {
                "source_title": "ExampleCo Opens Demo Facility",
                "publisher": "Example News",
                "published_at": "2026-06-01",
                "source_category": "news_media",
            },
        }
    ]
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")


def _passing_audit_payload(**overrides) -> dict:
    payload = {
        "audit_status": "pass",
        "audit_score": 100,
        "passed": True,
        "recommendation": "approve",
        "summary": "CL-001 is ready for delivery.",
        "findings": [],
    }
    payload.update(overrides)
    return payload


def test_finalize_audit_binding_interpreter_rejects_pass_status_with_stale_hash(tmp_path: Path):
    ws = tmp_path
    output_dir = ws / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    ledger = intermediate / "claim_ledger.json"
    audited = intermediate / "audited_brief.md"
    audit_report = intermediate / "audit_report.json"
    _write_single_claim_ledger(ledger)
    audited.write_text("# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n", encoding="utf-8")
    audit_report.write_text(json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": "0" * 64,
            "audited_brief_sha256": _sha256_file(audited),
            "audit_report_sha256": _sha256_file(audit_report),
        }
    }

    verdict = interpret_finalize_audit_binding(workspace=ws, finalize_report=report)

    assert verdict.kind == "degraded"
    assert require_finalize_audit_binding_pass(verdict) == [
        "finalize_report.json audit_binding.claim_ledger_sha256 does not match current artifact bytes."
    ]


def test_finalize_audit_binding_interpreter_rejects_pass_status_with_findings(tmp_path: Path):
    ws = tmp_path
    output_dir = ws / "output"
    intermediate = output_dir / "intermediate"
    intermediate.mkdir(parents=True)
    ledger = intermediate / "claim_ledger.json"
    audited = intermediate / "audited_brief.md"
    audit_report = intermediate / "audit_report.json"
    _write_single_claim_ledger(ledger)
    audited.write_text("# Brief\n\nExampleCo opened a public demo facility. [src:CL-001]\n", encoding="utf-8")
    audit_report.write_text(json.dumps(_passing_audit_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "audit_binding": {
            "status": "pass",
            "claim_ledger_sha256": _sha256_file(ledger),
            "audited_brief_sha256": _sha256_file(audited),
            "audit_report_sha256": _sha256_file(audit_report),
            "findings": [{"kind": "audit_binding_mismatch"}],
        }
    }

    verdict = interpret_finalize_audit_binding(workspace=ws, finalize_report=report)

    assert verdict.kind == "degraded"
    assert require_finalize_audit_binding_pass(verdict) == [
        "finalize_report.json audit_binding.findings must be empty when audit_binding.status is pass."
    ]
