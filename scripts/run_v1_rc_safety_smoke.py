#!/usr/bin/env python3
"""Run hermetic public-safe v1.0 RC safety scenarios.

The runner creates fresh temporary workspaces and exercises production CLI or
transaction entry points.  Role-authored workflow artifacts are synthetic
proposals; runtime control files are written only by deterministic production
code.  Results are ephemeral process output, not a persisted evidence ledger.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.check_product_baseline import public_overclaim_findings
from multi_agent_brief.cli.main import main as cli_main
from multi_agent_brief.contracts.agent_artifact_intake import (
    evaluate_agent_artifact_intake,
)
from multi_agent_brief.orchestrator.recovery_state import evaluate_recovery_state
from multi_agent_brief.orchestrator.runtime_state import (
    RUNTIME_STATE_FILES,
    RuntimeStateError,
    build_completion_projection,
    check_runtime_state,
    complete_finalize_transaction,
    complete_stage_transaction,
    freeze_claim_ledger_transaction,
    initialize_runtime_state,
    supersede_stage_artifact_transaction,
)
from multi_agent_brief.outputs.finalize import finalize_reader_outputs
from multi_agent_brief.quality_gates.state import check_quality_gates
from multi_agent_brief.workbuddy.diagnose import build_workbuddy_diagnosis


RUNNER_BOUNDARY = (
    "hermetic_public_safe_deterministic_rc_scenarios; "
    "not_pilot_evidence_not_release_authority_not_product_truth"
)
REQUIRED_SCENARIO_IDS = (
    "RC-SMOKE-01",
    "RC-SMOKE-02",
    "RC-SMOKE-03",
    "RC-SMOKE-04",
    "RC-SMOKE-05",
    "RC-SMOKE-06",
    "RC-SMOKE-07",
    "RC-SMOKE-08",
)

_INTERMEDIATE = Path("output/intermediate")


def _require(condition: bool, message: str) -> None:
    """Fail a safety scenario even when Python assertions are optimized out."""

    if not condition:
        raise AssertionError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return payload


def _event_records(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / RUNTIME_STATE_FILES["event_log"]
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _control_bytes(workspace: Path) -> dict[str, bytes | None]:
    return {
        key: path.read_bytes() if path.exists() else None
        for key, relative in RUNTIME_STATE_FILES.items()
        if (path := workspace / relative)
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _new_workspace(parent: Path, name: str) -> Path:
    workspace = parent / name
    source_path = workspace / "input/sources/source-001.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# ExampleCo public announcement\n\n"
        "ExampleCo opened a demonstration facility in June 2026.\n",
        encoding="utf-8",
    )
    (workspace / "config.yaml").write_text(
        "project:\n"
        "  name: ExampleCo\n"
        "  language: en-US\n"
        "  audience: management\n"
        "report:\n"
        '  date: "2026-06-18"\n'
        "  max_source_age_days: 30\n"
        "  fail_on_stale_source: false\n"
        "input:\n"
        "  path: input\n"
        "output:\n"
        "  path: output\n"
        "  formats:\n"
        "    - markdown\n"
        "  named_outputs: false\n",
        encoding="utf-8",
    )
    (workspace / "user.md").write_text(
        "# Brief direction\n\nPrepare a public-safe ExampleCo update.\n",
        encoding="utf-8",
    )
    (workspace / "sources.yaml").write_text(
        "source_strategy:\n"
        "  enabled_providers:\n"
        "    - web_search\n"
        "web_search:\n"
        "  enabled: true\n"
        "  mode: runtime_tool\n",
        encoding="utf-8",
    )
    (workspace / "source_candidates.yaml").write_text(
        "schema_version: mabw.source_candidates.v1\n"
        "artifact_type: source_plan_only\n"
        "evidence_status: not_evidence\n"
        "recommended_sources:\n"
        "  - name: ExampleCo public announcement\n"
        "    url: https://example.com/facility\n",
        encoding="utf-8",
    )
    return workspace


def _run_cli(argv: list[str]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = cli_main(argv)
    if rc != 0:
        raise AssertionError(
            f"CLI failed ({rc}): {' '.join(argv)}\n"
            f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}"
        )
    return {"argv": argv[:2], "exit_code": rc}


def _stage_complete_cli(workspace: Path, stage_id: str, *, repo_root: Path) -> None:
    _run_cli(
        [
            "state",
            "stage-complete",
            "--workspace",
            str(workspace),
            "--repo-workdir",
            str(repo_root),
            "--stage",
            stage_id,
            "--reason",
            f"{stage_id} completed by RC safety scenario",
            "--json",
        ]
    )


def _write_input_classification(workspace: Path) -> None:
    source_path = (workspace / "input/sources/source-001.md").resolve()
    _write_json(
        workspace / "output/input_classification.json",
        {
            "evidence": [{"path": str(source_path), "name": source_path.name}],
            "feedback": [],
            "instruction": [],
            "context": [],
            "skipped": [],
        },
    )


def _valid_candidates() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "CAND-001",
            "claim": "ExampleCo opened a demo facility.",
            "source_id": "SRC-001",
        }
    ]


def _valid_screened() -> list[dict[str, Any]]:
    return [{"candidate_id": "CAND-001", "screening_status": "selected"}]


def _valid_claim_drafts() -> dict[str, Any]:
    return {
        "schema_version": "mabw.claim_drafts.v1",
        "drafts": [
            {
                "statement": "ExampleCo opened a demo facility.",
                "source_id": "SRC-001",
                "evidence_text": "ExampleCo opened a demonstration facility in June 2026.",
                "source_url": "https://example.com/facility",
                "source_title": "ExampleCo public announcement",
                "source_category": "news_media",
                "published_at": "2026-06-01",
                "claim_type": "fact",
                "confidence": "high",
            }
        ],
    }


def _valid_audit_report(*, recovery_reviewed: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "audit_status": "pass",
        "audit_score": 100,
        "passed": True,
        "recommendation": "approve",
        "findings": [],
    }
    if recovery_reviewed:
        payload["supersede_reviewed"] = True
    return payload


def _initialize_and_advance_to_claim_ledger(
    workspace: Path,
    *,
    repo_root: Path,
    normalized_intake: bool = False,
    use_cli: bool = False,
    runtime_launched: bool = False,
) -> None:
    if use_cli:
        if not runtime_launched:
            _run_cli(
                [
                    "state",
                    "init",
                    "--workspace",
                    str(workspace),
                    "--repo-workdir",
                    str(repo_root),
                ]
            )
        complete = lambda stage_id: _stage_complete_cli(
            workspace, stage_id, repo_root=repo_root
        )
    else:
        if runtime_launched:
            raise AssertionError("runtime_launched requires the CLI lifecycle path.")
        initialize_runtime_state(workspace=workspace, repo_workdir=repo_root)

        def complete(stage_id: str) -> None:
            complete_stage_transaction(
                workspace=workspace,
                repo_workdir=repo_root,
                stage_id=stage_id,
                reason=f"{stage_id} completed by RC safety scenario",
            )

    if not runtime_launched:
        complete("doctor")
    complete("source-discovery")
    _write_input_classification(workspace)
    complete("input-governance")
    if normalized_intake:
        candidate_payload: Any = {
            "metadata": {"producer": "public-safe-rc-scout"},
            "claims": [
                {
                    "candidate_id": "CAND-001",
                    "claim_statement": "ExampleCo opened a demo facility.",
                    "source_excerpt": "ExampleCo opened a demonstration facility in June.",
                    "source_url": "https://example.com/facility",
                    "source_category": "industry_news",
                    "published_at": "2026-06-01",
                    "topic": "demo facility",
                    "claim_type": "fact",
                    "confidence": 0.91,
                }
            ],
        }
        screened_payload: Any = {
            "selected_candidates": [candidate_payload["claims"][0]],
            "excluded_candidates": [],
            "screening_policy": {"total_candidates": 1, "max_items": 8},
        }
    else:
        candidate_payload = _valid_candidates()
        screened_payload = _valid_screened()
    _write_json(workspace / _INTERMEDIATE / "candidate_claims.json", candidate_payload)
    _write_json(workspace / _INTERMEDIATE / "screened_candidates.json", screened_payload)
    complete("scout")


def _freeze_claims(
    workspace: Path,
    *,
    repo_root: Path,
    use_cli: bool = False,
) -> dict[str, Any]:
    _write_json(workspace / _INTERMEDIATE / "claim_drafts.json", _valid_claim_drafts())
    if use_cli:
        _run_cli(
            [
                "state",
                "freeze-claim-ledger",
                "--workspace",
                str(workspace),
                "--repo-workdir",
                str(repo_root),
                "--json",
            ]
        )
        manifest = _read_json(workspace / RUNTIME_STATE_FILES["runtime_manifest"])
        return dict(manifest["claim_ledger_freeze"])
    state = freeze_claim_ledger_transaction(workspace=workspace, repo_workdir=repo_root)
    return dict(state["claim_ledger_freeze"])


def _advance_to_auditor_complete(
    workspace: Path,
    *,
    repo_root: Path,
    use_cli: bool = False,
    runtime_launched: bool = False,
) -> dict[str, Any]:
    _initialize_and_advance_to_claim_ledger(
        workspace,
        repo_root=repo_root,
        use_cli=use_cli,
        runtime_launched=runtime_launched,
    )
    freeze = _freeze_claims(workspace, repo_root=repo_root, use_cli=use_cli)
    if use_cli:
        _stage_complete_cli(workspace, "claim-ledger", repo_root=repo_root)
    else:
        complete_stage_transaction(
            workspace=workspace,
            repo_workdir=repo_root,
            stage_id="claim-ledger",
            reason="claim-ledger completed by RC safety scenario",
        )
    audited = workspace / _INTERMEDIATE / "audited_brief.md"
    audited.write_text(
        "## Executive Summary\n\nExampleCo opened a demo facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    for stage_id in ("analyst", "editor"):
        if use_cli:
            _stage_complete_cli(workspace, stage_id, repo_root=repo_root)
        else:
            complete_stage_transaction(
                workspace=workspace,
                repo_workdir=repo_root,
                stage_id=stage_id,
                reason=f"{stage_id} completed by RC safety scenario",
            )
    _write_json(workspace / _INTERMEDIATE / "audit_report.json", _valid_audit_report())
    if use_cli:
        _run_cli(
            [
                "gates",
                "check",
                "--workspace",
                str(workspace),
                "--repo-workdir",
                str(repo_root),
                "--stage",
                "auditor",
                "--report-date",
                "2026-06-18",
                "--json",
            ]
        )
        _stage_complete_cli(workspace, "auditor", repo_root=repo_root)
        state = check_runtime_state(workspace=workspace, repo_workdir=repo_root)
    else:
        check_quality_gates(
            workspace=workspace,
            repo_workdir=repo_root,
            stage_id="auditor",
            report_date="2026-06-18",
        )
        state = complete_stage_transaction(
            workspace=workspace,
            repo_workdir=repo_root,
            stage_id="auditor",
            reason="auditor completed by RC safety scenario",
        )
    return {"freeze": freeze, "state": state}


def _finalize_workspace(
    workspace: Path,
    *,
    repo_root: Path,
    use_cli: bool = False,
) -> dict[str, Any]:
    if use_cli:
        _run_cli(["finalize", "--config", str(workspace / "config.yaml")])
        _run_cli(
            [
                "gates",
                "check",
                "--workspace",
                str(workspace),
                "--repo-workdir",
                str(repo_root),
                "--stage",
                "finalize",
                "--report-date",
                "2026-06-18",
                "--json",
            ]
        )
        _run_cli(
            [
                "state",
                "finalize-complete",
                "--workspace",
                str(workspace),
                "--repo-workdir",
                str(repo_root),
                "--reason",
                "finalize completed by RC safety scenario",
                "--json",
            ]
        )
        completed = check_runtime_state(workspace=workspace, repo_workdir=repo_root)
    else:
        finalize_reader_outputs(
            output_dir=workspace / "output",
            project_name="ExampleCo",
            output_formats=["markdown"],
            output_named_outputs=False,
            workspace_dir=workspace,
        )
        check_quality_gates(
            workspace=workspace,
            repo_workdir=repo_root,
            stage_id="finalize",
            report_date="2026-06-18",
        )
        completed = complete_finalize_transaction(
            workspace=workspace,
            repo_workdir=repo_root,
            reason="finalize completed by RC safety scenario",
        )
    return completed


def _scenario_01(parent: Path, repo_root: Path) -> dict[str, Any]:
    workspace = _new_workspace(parent, "rc-smoke-01")
    _run_cli(
        [
            "run",
            "--workspace",
            str(workspace),
            "--repo-workdir",
            str(repo_root),
        ]
    )
    handoff = workspace / _INTERMEDIATE / "agent_handoff.json"
    _require(handoff.exists(), "Standard run launcher did not write agent_handoff.json.")
    _advance_to_auditor_complete(
        workspace,
        repo_root=repo_root,
        use_cli=True,
        runtime_launched=True,
    )
    _finalize_workspace(workspace, repo_root=repo_root, use_cli=True)
    projection = build_completion_projection(workspace=workspace, repo_workdir=repo_root)
    report = _read_json(workspace / _INTERMEDIATE / "finalize_report.json")
    delivery = workspace / "output/delivery/brief.md"
    events = _event_records(workspace)
    finalize_events = [
        event
        for event in events
        if event.get("event_type") == "decision_recorded"
        and event.get("stage_id") == "finalize"
        and event.get("decision") == "finalize"
    ]
    _require(report["status"] == "pass", "Clean finalize report did not pass.")
    _require(
        report["delivery_promotion"] == "promoted",
        "Clean finalize did not promote delivery.",
    )
    _require(delivery.exists(), "Promoted delivery Markdown is missing.")
    _require(
        report["delivery_artifact_sha256"]["output/delivery/brief.md"]
        == _sha256(delivery),
        "Finalize report delivery hash does not bind promoted bytes.",
    )
    _require(
        projection["delivery_truth"]["valid"] is True,
        "Completion projection does not report valid delivery truth.",
    )
    _require(
        projection["event_truth"]["finalize_event_present"] is True,
        "Completion projection does not bind a finalize event.",
    )
    _require(len(finalize_events) == 1, "Expected exactly one finalize decision event.")
    return {
        "transaction_path": "cli",
        "handoff_sha256": _sha256(handoff),
        "delivery_sha256": _sha256(delivery),
        "finalize_event_id": finalize_events[0]["event_id"],
    }


def _scenario_02(parent: Path, _repo_root: Path) -> dict[str, Any]:
    workspace = parent / "rc-smoke-02"
    audited = workspace / _INTERMEDIATE / "audited_brief.md"
    audited.parent.mkdir(parents=True, exist_ok=True)
    audited.write_text("# Brief\n\nFirst reader-safe delivery.\n", encoding="utf-8")
    finalize_reader_outputs(
        output_dir=workspace / "output",
        project_name="ExampleCo",
        output_formats=["markdown"],
        output_named_outputs=False,
        workspace_dir=workspace,
    )
    protected = [workspace / "output/brief.md", workspace / "output/delivery/brief.md"]
    before = {path: path.read_bytes() for path in protected}
    history = workspace / "output/delivery-history"
    history_before = sorted(path.name for path in history.iterdir())
    audited.write_text(
        "# Brief\n\nSecond run leaks raw internal identity [CL-0001].\n",
        encoding="utf-8",
    )
    try:
        finalize_reader_outputs(
            output_dir=workspace / "output",
            project_name="ExampleCo",
            output_formats=["markdown"],
            output_named_outputs=False,
            workspace_dir=workspace,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Reader-clean failure unexpectedly finalized.")
    report = _read_json(workspace / _INTERMEDIATE / "finalize_report.json")
    _require(report["status"] == "fail", "Reader-clean failure report did not fail.")
    _require(
        report["delivery_promotion"] == "skipped_reader_clean_failed",
        "Reader-clean failure was not recorded as a skipped promotion.",
    )
    _require(
        all(path.read_bytes() == data for path, data in before.items()),
        "Reader-clean failure changed an existing delivery artifact.",
    )
    _require(
        sorted(path.name for path in history.iterdir()) == history_before,
        "Reader-clean failure created a delivery snapshot.",
    )
    return {
        "failure_status": report["status"],
        "delivery_promotion": report["delivery_promotion"],
        "protected_delivery_count": len(protected),
    }


def _scenario_03(parent: Path, repo_root: Path) -> dict[str, Any]:
    workspace = _new_workspace(parent, "rc-smoke-03")
    _advance_to_auditor_complete(workspace, repo_root=repo_root)
    audited = workspace / _INTERMEDIATE / "audited_brief.md"
    audited.write_text(
        "## Executive Summary\n\nDirect frozen edit. [src:CL-0001]\n",
        encoding="utf-8",
    )
    try:
        check_runtime_state(workspace=workspace, repo_workdir=repo_root)
    except RuntimeStateError:
        pass
    else:
        raise AssertionError("Direct frozen edit did not fail integrity check.")
    workflow = _read_json(workspace / RUNTIME_STATE_FILES["workflow_state"])
    integrity = workflow["run_integrity"]
    contaminations = [event for event in _event_records(workspace) if event.get("event_type") == "run_integrity_contaminated"]
    _require(
        integrity["status"] == "contaminated",
        "Direct frozen edit did not contaminate run integrity.",
    )
    _require(
        integrity["reference_eligible"] is False,
        "Contaminated run remained reference eligible.",
    )
    _require(
        integrity["clean_single_shot"] is False,
        "Contaminated run remained clean single shot.",
    )
    _require(len(contaminations) == 1, "Expected exactly one contamination event.")
    _require(
        contaminations[0]["metadata"]["reason_code"]
        == "frozen_artifact_changed",
        "Contamination event does not identify the frozen artifact edit.",
    )
    return {
        "run_integrity": integrity["status"],
        "reference_eligible": integrity["reference_eligible"],
        "contamination_event_id": contaminations[0]["event_id"],
    }


def _scenario_04(parent: Path, repo_root: Path) -> dict[str, Any]:
    workspace = _new_workspace(parent, "rc-smoke-04")
    _advance_to_auditor_complete(workspace, repo_root=repo_root)
    audited = workspace / _INTERMEDIATE / "audited_brief.md"
    audited.write_text(
        "## Executive Summary\n\nExampleCo opened its demo facility. [src:CL-0001]\n",
        encoding="utf-8",
    )
    try:
        check_runtime_state(workspace=workspace, repo_workdir=repo_root)
    except RuntimeStateError:
        pass
    else:
        raise AssertionError("Direct frozen edit did not fail integrity check.")
    superseded = supersede_stage_artifact_transaction(
        workspace=workspace,
        repo_workdir=repo_root,
        stage_id="editor",
        artifact="output/intermediate/audited_brief.md",
        reason="human approved supersede in RC safety scenario",
    )
    _write_json(
        workspace / _INTERMEDIATE / "audit_report.json",
        _valid_audit_report(recovery_reviewed=True),
    )
    check_quality_gates(
        workspace=workspace,
        repo_workdir=repo_root,
        stage_id="auditor",
        report_date="2026-06-18",
    )
    complete_stage_transaction(
        workspace=workspace,
        repo_workdir=repo_root,
        stage_id="auditor",
        reason="auditor reran after approved supersede",
    )
    completed = _finalize_workspace(workspace, repo_root=repo_root)
    recovery = evaluate_recovery_state(workspace=workspace, repo_workdir=repo_root)
    finalize_events = [
        event
        for event in _event_records(workspace)
        if event.get("event_type") == "decision_recorded"
        and event.get("stage_id") == "finalize"
        and event.get("decision") == "finalize"
    ]
    _require(
        completed["workflow_state"]["run_integrity"]["reference_eligible"]
        is False,
        "Recovered contaminated run became reference eligible.",
    )
    _require(
        recovery["status"] == "completed_non_reference",
        "Recovered run did not reach completed_non_reference.",
    )
    _require(
        recovery["reference_eligible"] is False,
        "Recovery projection made contaminated run reference eligible.",
    )
    _require(
        len(finalize_events) == 1,
        "Recovered run did not record exactly one finalize event.",
    )
    return {
        "supersede_transaction_id": superseded["transaction"]["transaction_id"],
        "recovery_status": recovery["status"],
        "reference_eligible": recovery["reference_eligible"],
        "finalize_event_id": finalize_events[0]["event_id"],
    }


def _scenario_05(parent: Path, repo_root: Path) -> dict[str, Any]:
    workspace = _new_workspace(parent, "rc-smoke-05")
    _initialize_and_advance_to_claim_ledger(
        workspace, repo_root=repo_root, normalized_intake=True
    )
    drafts = {
        "schema_version": "mabw.claim_drafts.v1",
        "claim_drafts": [
            {
                "claim_statement": "ExampleCo opened a demo facility.",
                "source_id": "SRC-001",
                "source_excerpt": "ExampleCo opened a demonstration facility in June.",
                "source_title": "ExampleCo public announcement",
                "source_category": "industry_news",
                "claim_type": "fact",
                "confidence": 0.91,
            }
        ],
    }
    paths = {
        "candidate_claims": workspace / _INTERMEDIATE / "candidate_claims.json",
        "screened_candidates": workspace / _INTERMEDIATE / "screened_candidates.json",
        "claim_drafts": workspace / _INTERMEDIATE / "claim_drafts.json",
    }
    _write_json(paths["claim_drafts"], drafts)
    raw_before = {key: path.read_bytes() for key, path in paths.items()}
    frozen = freeze_claim_ledger_transaction(workspace=workspace, repo_workdir=repo_root)
    records = frozen["artifact_registry"]["artifacts"]
    for artifact_id in paths:
        _require(
            records[artifact_id]["status"] == "valid",
            f"Normalized {artifact_id} is not registry-valid.",
        )
        projection = records[artifact_id]["intake_projection"]
        _require(
            projection["artifact_id"] == artifact_id,
            f"Intake projection identity mismatch for {artifact_id}.",
        )
        _require(
            projection["fatal_finding_count"] == 0,
            f"Normalized {artifact_id} retained fatal findings.",
        )
        _require(
            projection["normalization_count"] > 0,
            f"Recoverable {artifact_id} drift was not normalized.",
        )
        _require(
            projection["raw_sha256"]
            == hashlib.sha256(raw_before[artifact_id]).hexdigest(),
            f"Intake projection raw hash mismatch for {artifact_id}.",
        )
        _require(
            paths[artifact_id].read_bytes() == raw_before[artifact_id],
            f"Intake normalization rewrote raw {artifact_id} bytes.",
        )
    _require(
        (workspace / _INTERMEDIATE / "claim_ledger.json").exists(),
        "Recoverable intake did not produce a frozen Claim Ledger.",
    )
    return {
        "normalized_artifact_ids": sorted(paths),
        "claim_count": frozen["claim_ledger_freeze"]["claim_count"],
        "raw_artifacts_unchanged": True,
    }


def _fatal_freeze_case(
    parent: Path,
    *,
    repo_root: Path,
    name: str,
    draft: dict[str, Any],
    expected_path_fragment: str,
) -> dict[str, Any]:
    workspace = _new_workspace(parent, name)
    _initialize_and_advance_to_claim_ledger(workspace, repo_root=repo_root)
    draft_path = workspace / _INTERMEDIATE / "claim_drafts.json"
    _write_json(
        draft_path,
        {"schema_version": "mabw.claim_drafts.v1", "drafts": [draft]},
    )
    intake = evaluate_agent_artifact_intake(draft_path, artifact_id="claim_drafts")
    fatal_findings = [
        dict(finding)
        for finding in intake.findings
        if finding.get("severity") == "fatal"
    ]
    finding_paths = [str(finding.get("path") or "") for finding in fatal_findings]
    _require(
        any(expected_path_fragment in path for path in finding_paths),
        f"Fatal intake finding did not identify {expected_path_fragment}: {finding_paths}",
    )
    before = _control_bytes(workspace)
    ledger_path = workspace / _INTERMEDIATE / "claim_ledger.json"
    _require(not ledger_path.exists(), "Claim Ledger existed before fatal freeze attempt.")
    try:
        freeze_claim_ledger_transaction(workspace=workspace, repo_workdir=repo_root)
    except RuntimeStateError as exc:
        error_code = exc.error_code
    else:
        raise AssertionError("Fatal claim draft unexpectedly froze.")
    _require(
        _control_bytes(workspace) == before,
        "Fatal freeze attempt changed runtime control bytes.",
    )
    _require(not ledger_path.exists(), "Fatal freeze attempt wrote a Claim Ledger.")
    _require(
        error_code == "E_CLAIM_DRAFT_CONTRACT_INVALID",
        f"Fatal freeze returned imprecise error code: {error_code}",
    )
    return {
        "findings": [
            {
                "code": finding.get("code"),
                "path": finding.get("path"),
                "validation_result": finding.get("validation_result"),
            }
            for finding in fatal_findings
        ],
        "error_code": error_code,
    }


def _scenario_06(parent: Path, repo_root: Path) -> dict[str, Any]:
    base = _valid_claim_drafts()["drafts"][0]
    invalid_source = dict(base)
    invalid_source["source_url"] = "South China Morning Post"
    source_case = _fatal_freeze_case(
        parent,
        repo_root=repo_root,
        name="rc-smoke-06-source",
        draft=invalid_source,
        expected_path_fragment="source_url",
    )
    preassigned = dict(base)
    preassigned["claim_id"] = "CL-9999"
    id_case = _fatal_freeze_case(
        parent,
        repo_root=repo_root,
        name="rc-smoke-06-id",
        draft=preassigned,
        expected_path_fragment="claim_id",
    )
    return {
        "fatal_source_identity": source_case,
        "prefreeze_claim_id": id_case,
        "ledger_writes": 0,
    }


def _assert_workbuddy_parity(workspace: Path, *, repo_root: Path) -> dict[str, Any]:
    projection = build_completion_projection(workspace=workspace, repo_workdir=repo_root)
    diagnosis = build_workbuddy_diagnosis(workspace=workspace)
    _require(
        diagnosis["completion_projection"] == projection,
        "WorkBuddy diagnosis diverges from canonical completion projection.",
    )
    run_card = diagnosis["run_card"]
    _require(
        run_card["current_stage"] == projection["workflow"]["current_stage"],
        "WorkBuddy Run Card changed current-stage truth.",
    )
    _require(
        run_card["run_integrity"] == projection["run_integrity"]["status"],
        "WorkBuddy Run Card changed run-integrity truth.",
    )
    _require(
        run_card["delivery_valid"] is projection["delivery_truth"].get("valid"),
        "WorkBuddy Run Card changed delivery validity truth.",
    )
    return {
        "current_stage": run_card["current_stage"],
        "delivery_valid": run_card["delivery_valid"],
        "next_allowed_action": run_card["next_allowed_action"],
    }


def _scenario_07(parent: Path, repo_root: Path) -> dict[str, Any]:
    fresh = _new_workspace(parent, "rc-smoke-07-fresh")
    initialize_runtime_state(workspace=fresh, repo_workdir=repo_root)
    fresh_parity = _assert_workbuddy_parity(fresh, repo_root=repo_root)
    complete = _new_workspace(parent, "rc-smoke-07-complete")
    _advance_to_auditor_complete(complete, repo_root=repo_root)
    _finalize_workspace(complete, repo_root=repo_root)
    complete_parity = _assert_workbuddy_parity(complete, repo_root=repo_root)
    _require(
        fresh_parity["delivery_valid"] is False,
        "Fresh workspace incorrectly reports valid delivery.",
    )
    _require(
        complete_parity["delivery_valid"] is True,
        "Finalized workspace does not report valid delivery.",
    )
    return {"fresh": fresh_parity, "complete": complete_parity}


def _scenario_08(_parent: Path, repo_root: Path) -> dict[str, Any]:
    surfaces = {
        "README.md": ("experimental workbuddy / codebuddy", "experimental source-clone codebuddy"),
        "README.zh-CN.md": (
            "实验性 workbuddy",
            "实验性的 source-clone workbuddy",
            "实验性的 source-clone codebuddy",
        ),
        "docs/support-matrix.md": ("workbuddy skill source bundle", "experimental; source-clone-only"),
        "docs/workbuddy.md": ("experimental local skill adapter", "source-clone-only"),
        "docs/workbuddy.zh-CN.md": ("实验性的本地 skill adapter", "source-clone-only"),
    }
    checked: list[str] = []
    forbidden_claims: list[str] = []
    for relative, required in surfaces.items():
        raw_text = (repo_root / relative).read_text(encoding="utf-8")
        text = raw_text.casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in text]
        _require(
            not missing,
            f"{relative} missing product-posture wording: {missing}",
        )
        forbidden_claims.extend(public_overclaim_findings(relative, raw_text))
        checked.append(relative)
    _require(
        not forbidden_claims,
        f"Public surfaces contain forbidden product-posture claim(s): {forbidden_claims}",
    )
    return {
        "public_surfaces": checked,
        "posture": "experimental_source_clone",
        "forbidden_claim_findings": forbidden_claims,
    }


Scenario = Callable[[Path, Path], dict[str, Any]]
SCENARIOS: dict[str, Scenario] = {
    "RC-SMOKE-01": _scenario_01,
    "RC-SMOKE-02": _scenario_02,
    "RC-SMOKE-03": _scenario_03,
    "RC-SMOKE-04": _scenario_04,
    "RC-SMOKE-05": _scenario_05,
    "RC-SMOKE-06": _scenario_06,
    "RC-SMOKE-07": _scenario_07,
    "RC-SMOKE-08": _scenario_08,
}


def run_v1_rc_safety_smoke(
    *,
    repo_root: str | Path = REPO_ROOT,
    scenario_ids: Iterable[str] | None = None,
    work_root: str | Path | None = None,
) -> dict[str, Any]:
    """Execute selected scenarios; the readiness gate calls it with all IDs."""

    root = Path(repo_root).expanduser().resolve()
    selected = list(scenario_ids) if scenario_ids is not None else list(REQUIRED_SCENARIO_IDS)
    if len(selected) != len(set(selected)):
        raise ValueError("Scenario selection contains duplicate IDs.")
    unknown = sorted(set(selected).difference(REQUIRED_SCENARIO_IDS))
    if unknown:
        raise ValueError(f"Unknown RC safety scenario IDs: {unknown}")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if work_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="briefloop-v1-rc-safety-")
        parent = Path(temporary.name)
    else:
        parent = Path(work_root).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    try:
        for scenario_id in selected:
            try:
                evidence = SCENARIOS[scenario_id](parent, root)
                results.append(
                    {"scenario_id": scenario_id, "ok": True, "evidence": evidence}
                )
            except Exception as exc:  # scenario failures must remain visible as gate data
                results.append(
                    {
                        "scenario_id": scenario_id,
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
    finally:
        if temporary is not None:
            temporary.cleanup()

    executed = [result["scenario_id"] for result in results]
    required_complete = executed == list(REQUIRED_SCENARIO_IDS)
    return {
        "ok": bool(results) and all(result["ok"] for result in results),
        "required_complete": required_complete,
        "required_scenario_ids": list(REQUIRED_SCENARIO_IDS),
        "executed_scenario_ids": executed,
        "boundary": RUNNER_BOUNDARY,
        "scenarios": results,
    }


def _format_human(payload: Mapping[str, Any]) -> str:
    lines = []
    for result in payload.get("scenarios", []):
        status = "pass" if result.get("ok") else "FAIL"
        lines.append(f"[{status}] {result.get('scenario_id')}")
        if not result.get("ok"):
            lines.append(f"  {result.get('error_type')}: {result.get('error')}")
    lines.append(
        "v1.0 RC safety scenarios: "
        + ("pass" if payload.get("ok") and payload.get("required_complete") else "not_satisfied")
    )
    lines.append(str(payload.get("boundary", "")))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run hermetic v1.0 RC safety scenarios.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=REQUIRED_SCENARIO_IDS,
        help="Run only the selected stable scenario ID (repeatable).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)
    payload = run_v1_rc_safety_smoke(scenario_ids=args.scenario)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_format_human(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
