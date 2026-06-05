"""Run Manifest — tracks what ran, what it produced, and why it failed."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _file_hash(path: Path) -> str:
    """SHA-256 hex digest of a file, or empty string if missing."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _config_hash(config_path: Path) -> str:
    """SHA-256 hex digest of the config file."""
    return _file_hash(config_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RunManifest:
    """Snapshot of a single pipeline run."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=_utc_now)
    config_path: str = ""
    config_hash: str = ""
    workspace: str = ""

    # Pipeline configuration
    enabled_providers: list[str] = field(default_factory=list)
    output_formats: list[str] = field(default_factory=list)
    language: str = ""
    report_date: str = ""

    # Counts
    source_count: int = 0
    claim_count: int = 0
    candidate_count: int = 0

    # Audit
    audit_status: str = ""  # pass / warning / fail / not_run
    audit_score: int | None = None
    audit_finding_count: int = 0
    semantic_status: str = ""  # not_configured / not_run / pass / warning / fail / error

    # Artifacts: name → {path, hash}
    artifacts: dict[str, dict[str, str]] = field(default_factory=dict)

    # Pipeline stages: agent_name → {status, error?}
    stages: dict[str, dict[str, str]] = field(default_factory=dict)

    # Errors encountered during pipeline
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def export_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunManifest:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def build_manifest(
    *,
    config_path: str | Path = "",
    workspace: str | Path = "",
    enabled_providers: list[str] | None = None,
    output_formats: list[str] | None = None,
    language: str = "",
    report_date: str = "",
    source_count: int = 0,
    claim_count: int = 0,
    candidate_count: int = 0,
    audit_status: str = "",
    audit_score: int | None = None,
    audit_finding_count: int = 0,
    semantic_status: str = "",
    artifact_paths: dict[str, str | Path] | None = None,
    stage_outputs: list[dict[str, Any]] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> RunManifest:
    """Build a RunManifest from pipeline state.

    Args:
        config_path: path to config.yaml (for hash computation).
        workspace: workspace root directory.
        enabled_providers: list of enabled source providers.
        output_formats: configured output formats.
        language: brief language.
        report_date: reporting date.
        source_count: number of collected sources.
        claim_count: number of claims in ledger.
        candidate_count: number of screener candidates.
        audit_status: final audit status (pass/warning/fail).
        audit_score: audit score (0-100).
        audit_finding_count: number of audit findings.
        artifact_paths: mapping of artifact name → file path.
        stage_outputs: list of AgentOutput dicts from pipeline.
        errors: list of error dicts.
    """
    cp = Path(config_path) if config_path else Path()
    manifest = RunManifest(
        config_path=str(config_path),
        config_hash=_config_hash(cp) if config_path else "",
        workspace=str(workspace),
        enabled_providers=enabled_providers or [],
        output_formats=output_formats or [],
        language=language,
        report_date=report_date,
        source_count=source_count,
        claim_count=claim_count,
        candidate_count=candidate_count,
        audit_status=audit_status or "not_run",
        audit_score=audit_score,
        audit_finding_count=audit_finding_count,
        semantic_status=semantic_status or "not_run",
    )

    # Artifact hashes
    if artifact_paths:
        for name, path_str in artifact_paths.items():
            p = Path(path_str)
            manifest.artifacts[name] = {
                "path": str(p),
                "hash": _file_hash(p),
            }

    # Stage statuses — detect failures from artifacts or summary
    if stage_outputs:
        for output in stage_outputs:
            if isinstance(output, dict):
                agent_name = output.get("agent_name", "unknown")
                artifacts = output.get("artifacts", {})
                summary = output.get("summary", "")

                # Determine status from artifacts or summary
                if isinstance(artifacts, dict) and artifacts.get("status") == "failed":
                    stage_status = "failed"
                elif "FAILED" in summary.upper() or "ERROR" in summary.upper():
                    stage_status = "failed"
                else:
                    stage_status = "ok"

                stage_entry: dict[str, str] = {
                    "status": stage_status,
                    "summary": summary,
                }

                # Propagate error details from artifacts
                if stage_status == "failed":
                    if isinstance(artifacts, dict):
                        if artifacts.get("error_type"):
                            stage_entry["error_type"] = artifacts["error_type"]
                        if artifacts.get("error"):
                            stage_entry["error"] = str(artifacts["error"])[:500]
                    manifest.errors.append({
                        "stage": agent_name,
                        "error_type": stage_entry.get("error_type", "unknown"),
                        "error": stage_entry.get("error", summary),
                    })

                # Propagate collection_errors from source-collection
                if agent_name == "source-collection" and isinstance(artifacts, dict):
                    coll_errors = artifacts.get("collection_errors", [])
                    if isinstance(coll_errors, list):
                        for ce in coll_errors:
                            manifest.errors.append({
                                "stage": agent_name,
                                "error_type": ce.get("error_type", "collection_error"),
                                "error": ce.get("message", str(ce)),
                            })

                manifest.stages[agent_name] = stage_entry

    if errors:
        manifest.errors.extend(errors)

    return manifest


def save_manifest(manifest: RunManifest, output_dir: str | Path) -> Path:
    """Save manifest to output/intermediate/run_manifest.json."""
    intermediate = Path(output_dir) / "intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    path = intermediate / "run_manifest.json"
    manifest.export_json(path)
    return path
