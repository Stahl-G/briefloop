"""CI smoke test: run the official reference workflow and verify artifact contract.

Usage:
    python scripts/ci/smoke_reference_workflow.py [workspace_path]

If workspace_path is omitted, defaults to examples/reference_workflow_demo.

Exit codes:
    0 — all artifacts present and contract satisfied
    1 — one or more checks failed

This script runs without API keys.  It exercises the same path that
``multi-agent-brief prepare`` uses internally, then validates every
artifact defined in the v0.5.0 reference workflow contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# ── Expected artifacts ────────────────────────────────────────────────────────

READER_BRIEF = "brief.md"
INTERMEDIATE_DIR = "intermediate"
EXPECTED_INTERMEDIATE = [
    "audited_brief.md",
    "claim_ledger.json",
    "audit_report.json",
    "source_map.md",
    "run_manifest.json",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

class _Check:
    """Lightweight check result collector."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  FAIL: {msg}")

    def ok(self, msg: str) -> None:
        print(f"  OK:   {msg}")


def _run_pipeline(workspace: Path) -> None:
    """Run BriefPipeline through the same path as CLI prepare."""
    from multi_agent_brief.core.config import build_run_settings, load_config
    from multi_agent_brief.core.pipeline import BriefPipeline
    from multi_agent_brief.core.schemas import PipelineContext
    from multi_agent_brief.sources.registry import load_sources_config

    config_path = workspace / "config.yaml"
    config = load_config(str(config_path))

    # Load sources.yaml (conservative / manual-only)
    sources_path = workspace / "sources.yaml"
    source_config = None
    if sources_path.exists():
        source_config = load_sources_config(sources_path)

    settings = build_run_settings(
        config=config,
        input_dir=None,   # use config input.path
        output_dir=None,  # use config output.path
        name=None,
        language=None,
        audience=None,
    )
    context = PipelineContext(**settings)

    if source_config is not None:
        context.metadata["source_config"] = source_config
    context.metadata["_config_dir"] = str(workspace)

    outputs = BriefPipeline().run(context)

    # Generate run_manifest.json (mirrors CLI prepare behavior)
    from multi_agent_brief.core.manifest import build_manifest, save_manifest

    try:
        formatter_output = next(
            (o for o in outputs if o.agent_name == "formatter"), None
        )
        artifact_paths = formatter_output.artifacts if formatter_output else {}
        stage_dicts = [o.to_dict() for o in outputs]
        audit_report = context.report_state.audit_report
        manifest = build_manifest(
            config_path=str(config_path),
            workspace=str(workspace),
            enabled_providers=source_config.enabled_providers if source_config else [],
            output_formats=context.output_formats,
            language=context.language,
            report_date=context.report_date,
            source_count=len(context.sources),
            claim_count=len(context.metadata.get("_ledger", [])),
            candidate_count=len(context.candidates),
            audit_status=audit_report.audit_status if audit_report else "not_run",
            audit_score=audit_report.audit_score if audit_report else None,
            audit_finding_count=len(audit_report.findings) if audit_report else 0,
            semantic_status=(audit_report.metadata.get("semantic_status", "") if audit_report else ""),
            artifact_paths=artifact_paths,
            stage_outputs=stage_dicts,
        )
        save_manifest(manifest, context.output_dir)
    except Exception as exc:
        print(f"  WARN: could not generate run_manifest.json: {exc}")


def _verify_artifacts(workspace: Path, checks: _Check) -> None:
    """Verify every artifact in the reference workflow contract."""
    output = workspace / "output"
    intermediate = output / INTERMEDIATE_DIR

    # 1. Reader-facing brief
    brief_path = output / READER_BRIEF
    if brief_path.exists():
        checks.ok(f"{READER_BRIEF} exists")
        content = brief_path.read_text(encoding="utf-8")
        if "[src:" in content:
            checks.fail(f"{READER_BRIEF} contains [src: markers (must be reader-clean)")
        else:
            checks.ok(f"{READER_BRIEF} has no [src: markers")
    else:
        checks.fail(f"{READER_BRIEF} missing")

    # 2. Intermediate artifacts
    for name in EXPECTED_INTERMEDIATE:
        path = intermediate / name
        if path.exists():
            checks.ok(f"intermediate/{name} exists")
        else:
            checks.fail(f"intermediate/{name} missing")
            continue

        # Content checks for specific files
        if name == "audited_brief.md":
            content = path.read_text(encoding="utf-8")
            if "[src:" in content:
                checks.ok("audited_brief.md retains [src: citations")
            else:
                checks.fail("audited_brief.md has no [src: citations (should retain them)")

        if name == "claim_ledger.json":
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(ledger, list) and len(ledger) > 0:
                    checks.ok(f"claim_ledger.json has {len(ledger)} entries")
                else:
                    checks.fail("claim_ledger.json is empty or not a list")
            except json.JSONDecodeError:
                checks.fail("claim_ledger.json is not valid JSON")

        if name == "run_manifest.json":
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                artifacts = manifest.get("artifacts", {})
                if artifacts:
                    hashes_ok = all(
                        isinstance(v, dict) and "hash" in v
                        for v in artifacts.values()
                    )
                    if hashes_ok:
                        checks.ok(f"run_manifest.json has {len(artifacts)} artifacts with hashes")
                    else:
                        checks.fail("run_manifest.json artifacts missing hash fields")
                else:
                    checks.fail("run_manifest.json has no artifacts")
            except json.JSONDecodeError:
                checks.fail("run_manifest.json is not valid JSON")


def main() -> int:
    # Resolve workspace
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1])
    else:
        workspace = Path("examples/reference_workflow_demo")

    workspace = workspace.resolve()
    print(f"Workspace: {workspace}")

    if not workspace.exists():
        print(f"ERROR: workspace not found: {workspace}")
        return 1

    # Step 1: Run pipeline
    print("\n--- Running pipeline ---")
    try:
        _run_pipeline(workspace)
    except Exception as exc:
        print(f"Pipeline FAILED: {exc}")
        return 1

    # Step 2: Verify artifacts
    print("\n--- Verifying artifacts ---")
    checks = _Check()
    _verify_artifacts(workspace, checks)

    # Step 3: Report
    print()
    if checks.errors:
        print(f"SMOKE FAILED — {len(checks.errors)} check(s) failed")
        for err in checks.errors:
            print(f"  - {err}")
        return 1

    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
