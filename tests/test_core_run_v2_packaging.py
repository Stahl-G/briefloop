from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import zipfile


ROOT = Path(__file__).parents[1]


def test_non_editable_wheel_runs_complete_dormant_core_spine(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "build-root"
    build_root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", build_root / "README.md")
    shutil.copytree(ROOT / "src", build_root / "src")
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=build_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel_path = next(wheel_dir.glob("briefloop-*.whl"))
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(installed)

    script = textwrap.dedent(
        r'''
        from contextlib import redirect_stdout
        from copy import deepcopy
        import hashlib
        from importlib import resources
        import io
        import json
        from pathlib import Path
        import shutil
        import sys

        import multi_agent_brief
        from multi_agent_brief.cli.init_wizard import create_demo_workspace
        from multi_agent_brief.cli.main import main
        from multi_agent_brief.contracts.v2 import (
            ArtifactSubmitRequest,
            AuditPromotionRequest,
            ClaimFreezeRequest,
            CoreRunInitializeRequest,
            GateCheckRequest,
            IntegrityCheckRequest,
            InvocationStartRequest,
            OwnedArtifactSubmitRequest,
            SourceCommitRequest,
            StageCompleteRequest,
        )
        from multi_agent_brief.control_store import SQLiteControlStore
        from multi_agent_brief.core_run_v2.checkout import build_checkout_revision
        from multi_agent_brief.core_run_v2.publication import CheckoutPublicationEngine
        from multi_agent_brief.core_run_v2.policy import REQUIRED_AUDITOR_GATES

        workspace = Path(sys.argv[1])
        installed = Path(sys.argv[2]).resolve()
        assert Path(multi_agent_brief.__file__).resolve().is_relative_to(installed)
        migration_0004 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0004.sql")
        assert migration_0004.is_file()
        assert "PRAGMA user_version=4;" in migration_0004.read_text(encoding="utf-8")
        migration_0005 = resources.files(
            "multi_agent_brief.control_store"
        ).joinpath("migrations", "0005.sql")
        assert migration_0005.is_file()
        assert "PRAGMA user_version=5;" in migration_0005.read_text(encoding="utf-8")
        assert callable(build_checkout_revision)
        assert CheckoutPublicationEngine.__module__.endswith(".publication")
        create_demo_workspace(workspace)
        run_id = "RUN-WHEEL-CORE-V2-001"
        workspace_id = "WS-WHEEL-CORE-V2-001"
        now = "2026-07-15T12:00:00Z"
        counter = 0

        def record(model, **values):
            return model.model_validate(
                {"schema_version": model.schema_id, **values},
                strict=True,
            )

        def write_json(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            return path

        def request_path(payload, *, scope="cli", name="request"):
            global counter
            counter += 1
            return write_json(
                workspace
                / "scratch"
                / f"{scope}-{counter:03d}-{name}"
                / "submit_request.json",
                payload,
            ).relative_to(workspace).as_posix()

        def call(group, action, request, *, target=None, expected="committed"):
            target = workspace if target is None else target
            stream = io.StringIO()
            with redirect_stdout(stream):
                return_code = main([
                    group,
                    action,
                    "--workspace",
                    str(target),
                    "--request",
                    request,
                    "--json",
                ])
            lines = stream.getvalue().splitlines()
            assert len(lines) == 1, lines
            result = json.loads(lines[0])
            assert result["status"] == expected, result
            assert return_code == (0 if expected in {"committed", "replayed"} else 1)
            return result

        def revision(target=None):
            target = workspace if target is None else target
            with SQLiteControlStore.open(target / "briefloop.db") as store:
                return store.current_revision

        def snapshot(target=None):
            target = workspace if target is None else target
            with SQLiteControlStore.open(target / "briefloop.db") as store:
                return store.load_snapshot(run_id)

        def stage(stage_id, target=None):
            return next(
                item for item in snapshot(target).stage_states
                if item.stage_id == stage_id
            )

        def core(action, model, *, scope="cli", target=None, expected="committed"):
            target = workspace if target is None else target
            path = target / "scratch" / scope / "submit_request.json"
            write_json(path, model.model_dump(mode="json", exclude_unset=False))
            return call(
                "core-v2",
                action,
                path.relative_to(target).as_posix(),
                target=target,
                expected=expected,
            )

        def start(request_id, stage_id, role_id):
            result = core(
                "invocation-start",
                record(
                    InvocationStartRequest,
                    request_id=request_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    role_id=role_id,
                    runtime="operator",
                    expected_store_revision=revision(),
                ),
                scope="cli",
            )
            return result["primary_record_id"]

        def complete(stage_id, artifacts, gate_ids=None, *, target=None, expected="committed"):
            target = workspace if target is None else target
            current = stage(stage_id, target)
            return core(
                "stage-complete",
                record(
                    StageCompleteRequest,
                    request_id=f"REQ-WHEEL-COMPLETE-{stage_id.upper()}",
                    run_id=run_id,
                    stage_id=stage_id,
                    reason=f"{stage_id} accepted output is complete",
                    expected_stage_revision=current.revision,
                    expected_store_revision=revision(target),
                    expected_artifact_revisions=[
                        {"artifact_id": artifact_id, "revision": artifact_revision}
                        for artifact_id, artifact_revision in artifacts
                    ],
                    expected_gate_evaluation_ids=gate_ids or [],
                ),
                target=target,
                expected=expected,
            )

        initialize = deepcopy(CoreRunInitializeRequest.minimal_example)
        initialize.update(
            request_id="REQ-WHEEL-INIT",
            run_id=run_id,
            workspace_id=workspace_id,
            role_topology="default",
            input_governance_required=False,
        )
        call(
            "core-v2",
            "initialize",
            request_path(initialize, name="initialize"),
        )
        core(
            "doctor-check",
            record(
                IntegrityCheckRequest,
                request_id="REQ-WHEEL-DOCTOR",
                run_id=run_id,
                expected_store_revision=revision(),
            ),
        )

        planner = start(
            "REQ-WHEEL-INVOKE-PLANNER",
            "source-discovery",
            "source-planner",
        )
        candidates = workspace / "scratch" / planner / "source_candidates.yaml"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text("sources:\n  - SRC-WHEEL-001\n", encoding="utf-8")
        core(
            "artifact-submit",
            record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-WHEEL-ARTIFACT-SOURCES",
                run_id=run_id,
                artifact_id="source_candidates",
                invocation_id=planner,
                producer_tool_id=None,
                input_path=candidates.relative_to(workspace).as_posix(),
                expected_store_revision=revision(),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            ),
            scope=planner,
        )

        provider = start(
            "REQ-WHEEL-INVOKE-PROVIDER",
            "source-discovery",
            "source-provider",
        )
        source_dir = workspace / "scratch" / provider
        source_dir.mkdir(parents=True, exist_ok=True)
        content = b"ExampleCo opened a public pilot facility on 2026-07-14.\n"
        content_path = source_dir / "source_content.txt"
        content_path.write_bytes(content)
        source_proposal = write_json(
            source_dir / "source_proposal.json",
            {
                "schema_version": "briefloop.source_proposal.v2",
                "proposal_id": "PROP-WHEEL-SOURCE",
                "run_id": run_id,
                "source_id": "SRC-WHEEL-001",
                "origin_type": "uploaded_file",
                "acquisition_method": "manual_upload",
                "material_kind": "uploaded_file",
                "provider": None,
                "locator": {
                    "kind": "file",
                    "path": content_path.relative_to(workspace).as_posix(),
                },
                "title": "Synthetic packaged source",
                "publisher": "Example regulator",
                "published_at": "2026-07-14",
                "retrieved_at": now,
                "source_category": "regulator",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "filing",
                "raw_underlying_evidence_type": None,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "text/plain",
                "raw_payload_sha256": None,
                "raw_payload_media_type": None,
            },
        )
        source_request = record(
            SourceCommitRequest,
            request_id="REQ-WHEEL-SOURCE",
            run_id=run_id,
            invocation_id=provider,
            proposal_path=source_proposal.relative_to(workspace).as_posix(),
            content_path=content_path.relative_to(workspace).as_posix(),
            raw_payload_path=None,
            expected_store_revision=revision(),
        )
        source_request_path = write_json(
            source_dir / "submit_request.json",
            source_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "source",
            source_request_path.relative_to(workspace).as_posix(),
        )
        accepted_source = snapshot().sources[0]
        complete(
            "source-discovery",
            [
                ("source_candidates", 1),
                (
                    accepted_source.content_artifact_id,
                    accepted_source.content_artifact_revision,
                ),
            ],
        )
        complete("input-governance", [])

        scout = start("REQ-WHEEL-INVOKE-SCOUT", "scout", "scout")
        scout_dir = workspace / "scratch" / scout
        candidate_path = write_json(
            scout_dir / "candidate_claims.json",
            {
                "schema_version": "briefloop.candidate_claims_proposal.v2",
                "proposal_id": "PROP-WHEEL-CANDIDATE",
                "run_id": run_id,
                "created_at": now,
                "candidates": [
                    {
                        "candidate_id": "CAND-WHEEL-001",
                        "source_id": "SRC-WHEEL-001",
                        "statement": "ExampleCo opened a public pilot facility.",
                        "evidence_text": (
                            "ExampleCo opened a public pilot facility on 2026-07-14."
                        ),
                        "topic": "operations",
                        "claim_type": "fact",
                        "confidence": "high",
                    }
                ],
            },
        )
        candidate_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-CANDIDATE",
            run_id=run_id,
            artifact_id="candidate_claims",
            invocation_id=scout,
            input_path=candidate_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        candidate_request_path = write_json(
            scout_dir / "submit_request.json",
            candidate_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "candidate",
            candidate_request_path.relative_to(workspace).as_posix(),
        )

        screening = start("REQ-WHEEL-INVOKE-SCREEN", "scout", "scout")
        screening_dir = workspace / "scratch" / screening
        screened_path = write_json(
            screening_dir / "screened_candidates.json",
            {
                "schema_version": "briefloop.screened_candidates_proposal.v2",
                "proposal_id": "PROP-WHEEL-SCREENED",
                "run_id": run_id,
                "candidate_claims_proposal_id": "PROP-WHEEL-CANDIDATE",
                "created_at": now,
                "decisions": [
                    {
                        "candidate_id": "CAND-WHEEL-001",
                        "decision": "selected",
                        "reason_code": "public_evidence_in_scope",
                        "explanation": "Public evidence is in scope.",
                        "priority": "high",
                    }
                ],
            },
        )
        screened_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-SCREENED",
            run_id=run_id,
            artifact_id="screened_candidates",
            invocation_id=screening,
            input_path=screened_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        screened_request_path = write_json(
            screening_dir / "submit_request.json",
            screened_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "screened",
            screened_request_path.relative_to(workspace).as_posix(),
        )
        complete(
            "scout",
            [("candidate_claims", 1), ("screened_candidates", 1)],
        )

        claim_role = start(
            "REQ-WHEEL-INVOKE-CLAIMS",
            "claim-ledger",
            "claim-ledger",
        )
        claim_dir = workspace / "scratch" / claim_role
        claim_path = write_json(
            claim_dir / "claim_drafts.json",
            {
                "schema_version": "briefloop.claim_drafts_proposal.v2",
                "proposal_id": "PROP-WHEEL-CLAIMS",
                "run_id": run_id,
                "screened_candidates_proposal_id": "PROP-WHEEL-SCREENED",
                "created_at": now,
                "drafts": [
                    {
                        "draft_id": "DRAFT-WHEEL-001",
                        "statement": "ExampleCo opened a public pilot facility.",
                        "evidence_text": (
                            "ExampleCo opened a public pilot facility on 2026-07-14."
                        ),
                        "source_ids": ["SRC-WHEEL-001"],
                        "claim_type": "fact",
                    }
                ],
            },
        )
        claim_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-CLAIM-DRAFTS",
            run_id=run_id,
            artifact_id="claim_drafts",
            invocation_id=claim_role,
            input_path=claim_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        claim_request_path = write_json(
            claim_dir / "submit_request.json",
            claim_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "claim-drafts",
            claim_request_path.relative_to(workspace).as_posix(),
        )
        core(
            "claim-freeze",
            record(
                ClaimFreezeRequest,
                request_id="REQ-WHEEL-FREEZE",
                run_id=run_id,
                claim_drafts_proposal_id="PROP-WHEEL-CLAIMS",
                expected_claim_drafts_artifact={
                    "artifact_id": "claim_drafts",
                    "revision": 1,
                },
                expected_store_revision=revision(),
                expected_ledger_revision=0,
            ),
        )
        complete(
            "claim-ledger",
            [("claim_drafts", 1), ("claim_ledger", 1)],
        )

        analyst = start("REQ-WHEEL-INVOKE-ANALYST", "analyst", "analyst")
        analyst_path = workspace / "scratch" / analyst / "analyst_draft_snapshot.md"
        analyst_path.parent.mkdir(parents=True, exist_ok=True)
        analyst_path.write_text(
            "# ExampleCo brief\n\nExampleCo opened a pilot. [src:CL-0001]\n",
            encoding="utf-8",
        )
        core(
            "artifact-submit",
            record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-WHEEL-ANALYST-ARTIFACT",
                run_id=run_id,
                artifact_id="analyst_draft_snapshot",
                invocation_id=analyst,
                producer_tool_id="analyst-snapshot-v2",
                input_path=analyst_path.relative_to(workspace).as_posix(),
                expected_store_revision=revision(),
                expected_artifact_revision=0,
                expected_parent_artifact=None,
            ),
            scope=analyst,
        )
        complete("analyst", [("analyst_draft_snapshot", 1)])

        editor = start("REQ-WHEEL-INVOKE-EDITOR", "editor", "editor")
        brief_path = workspace / "scratch" / editor / "audited_brief.md"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(
            "# ExampleCo brief\n\n## Executive Summary\n\n"
            "ExampleCo opened a public pilot facility on 2026-07-14. "
            "[src:CL-0001]\n",
            encoding="utf-8",
        )
        core(
            "artifact-submit",
            record(
                OwnedArtifactSubmitRequest,
                request_id="REQ-WHEEL-EDITOR-ARTIFACT",
                run_id=run_id,
                artifact_id="audited_brief",
                invocation_id=editor,
                producer_tool_id=None,
                input_path=brief_path.relative_to(workspace).as_posix(),
                expected_store_revision=revision(),
                expected_artifact_revision=0,
                expected_parent_artifact={
                    "artifact_id": "analyst_draft_snapshot",
                    "revision": 1,
                },
            ),
            scope=editor,
        )
        complete(
            "editor",
            [("analyst_draft_snapshot", 1), ("audited_brief", 1)],
        )

        auditor = start("REQ-WHEEL-INVOKE-AUDITOR", "auditor", "auditor")
        audit_dir = workspace / "scratch" / auditor
        audit_path = write_json(
            audit_dir / "audit_proposal.json",
            {
                "schema_version": "briefloop.audit_proposal.v2",
                "proposal_id": "PROP-WHEEL-AUDIT",
                "run_id": run_id,
                "artifact_id": "audited_brief",
                "artifact_revision": 1,
                "decision": "pass",
                "created_at": now,
                "findings": [],
            },
        )
        audit_request = record(
            ArtifactSubmitRequest,
            request_id="REQ-WHEEL-AUDIT",
            run_id=run_id,
            artifact_id="audit_proposal",
            invocation_id=auditor,
            input_path=audit_path.relative_to(workspace).as_posix(),
            expected_store_revision=revision(),
            expected_artifact_revision=0,
        )
        audit_request_path = write_json(
            audit_dir / "submit_request.json",
            audit_request.model_dump(mode="json", exclude_unset=False),
        )
        call(
            "intake-v2",
            "audit",
            audit_request_path.relative_to(workspace).as_posix(),
        )
        core(
            "audit-promote",
            record(
                AuditPromotionRequest,
                request_id="REQ-WHEEL-AUDIT-PROMOTE",
                run_id=run_id,
                audit_proposal_id="PROP-WHEEL-AUDIT",
                expected_target_artifact={
                    "artifact_id": "audited_brief",
                    "revision": 1,
                },
                expected_audit_report_revision=0,
                expected_store_revision=revision(),
            ),
        )
        core(
            "gate-check",
            record(
                GateCheckRequest,
                request_id="REQ-WHEEL-GATE",
                run_id=run_id,
                stage_id="auditor",
                expected_store_revision=revision(),
                expected_report_artifact_revision=0,
                expected_input_artifacts=[
                    {"artifact_id": "claim_ledger", "revision": 1},
                    {"artifact_id": "audited_brief", "revision": 1},
                    {"artifact_id": "analyst_draft_snapshot", "revision": 1},
                    {"artifact_id": "screened_candidates", "revision": 1},
                    {"artifact_id": "candidate_claims", "revision": 1},
                ],
            ),
        )
        before_completion = snapshot()
        gate_ids = [
            item.evaluation_id
            for item in before_completion.gate_evaluations
            if item.gate_id in REQUIRED_AUDITOR_GATES
        ]
        auditor_artifacts = [
            ("claim_ledger", 1),
            ("audited_brief", 1),
            ("audit_report", 1),
            ("auditor_quality_gate_report", 1),
            ("analyst_draft_snapshot", 1),
        ]
        contaminated_workspace = workspace.with_name("wheel-core-contaminated")
        shutil.copytree(workspace, contaminated_workspace)
        complete("auditor", auditor_artifacts, gate_ids)

        clean = snapshot()
        clean_states = {item.stage_id: item.status for item in clean.stage_states}
        assert clean_states["auditor"] == "complete"
        assert clean_states["finalize"] == "ready"
        assert not clean.approvals
        assert not clean.deliveries
        assert not any(
            item.stage_id == "finalize" and item.transition_kind == "complete"
            for item in clean.stage_transitions
        )

        legacy = workspace / "output" / "intermediate" / "runtime_manifest.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy_bytes = b'{"runtime":"fake-legacy"}'
        legacy.write_bytes(legacy_bytes)
        legacy_revision = revision()
        legacy_states = {item.stage_id: item.status for item in snapshot().stage_states}
        assert legacy_revision == clean.store_revision
        assert legacy_states == clean_states
        assert legacy.read_bytes() == legacy_bytes
        control_paths = [
            "workflow_state.json",
            "artifact_registry.json",
            "event_log.jsonl",
            "finalize_report.json",
        ]
        assert not any(
            (workspace / "output" / "intermediate" / name).exists()
            for name in control_paths
        )

        contaminated_before = snapshot(contaminated_workspace)
        protected = next(
            item for item in contaminated_before.artifacts
            if item.artifact_id == "audited_brief"
        )
        (contaminated_workspace / protected.path).write_text(
            "MUTATED OUTSIDE CONTROLSTORE\n",
            encoding="utf-8",
        )
        blocked = complete(
            "auditor",
            auditor_artifacts,
            gate_ids,
            target=contaminated_workspace,
            expected="blocked",
        )
        assert blocked["error_code"] == "frozen_artifact_contaminated"
        contaminated = snapshot(contaminated_workspace)
        assert contaminated.store_revision == contaminated_before.store_revision + 1
        assert next(
            item for item in contaminated.stage_states if item.stage_id == "auditor"
        ).status == "ready"
        assert contaminated.run_integrity_records[-1].status == "contaminated"

        print(json.dumps({
            "auditor": clean_states["auditor"],
            "finalize": clean_states["finalize"],
            "claim_count": len(clean.claims),
            "gate_count": len(clean.gate_evaluations),
            "legacy_file_zero_truth": legacy_revision == clean.store_revision,
            "contamination_blocked": blocked["status"] == "blocked",
            "receipt_count": len(clean.transactions),
        }, sort_keys=True))
        '''
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(installed)
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "wheel-core-workspace"),
            str(installed),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout == (
        '{"auditor": "complete", "claim_count": 1, '
        '"contamination_blocked": true, "finalize": "ready", '
        '"gate_count": 6, "legacy_file_zero_truth": true, '
        '"receipt_count": 28}\n'
    )
