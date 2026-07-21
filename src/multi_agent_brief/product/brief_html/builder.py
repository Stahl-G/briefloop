"""Store/LAJ → three-page brief data contract (read-only projections only).

Page 1 (quality) is projected from one verified SQLite ControlStore snapshot;
the live Store-fed quality projection payload is embedded verbatim.  Page 2
(semantic review) renders the hash-bound LAJ reader view when present.  Page 3
(improvement) is an honest unavailable surface: no Store-native Improvement
Ledger home exists and nothing is fabricated.  No legacy JSON fold-in is read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from multi_agent_brief.control_store import ControlStoreError, SQLiteControlStore
from multi_agent_brief.core_run_v2.errors import CoreRunError
from multi_agent_brief.core_run_v2.next_action import classify_core_run_next_action
from multi_agent_brief.core_run_v2.policy import core_role_topology_policy
from multi_agent_brief.core_run_v2.terminal import classify_terminal_legality
from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier
from multi_agent_brief.product.review_session.contracts import FindingDimensionId
from multi_agent_brief.runtime_host_v2.errors import RuntimeHostError
from multi_agent_brief.runtime_host_v2.projections import (
    build_store_quality_projection,
)
from multi_agent_brief.semantic_evaluator.reader import (
    LAJ_READER_FILENAMES,
    LajReaderView,
    bind_laj_reader_view_to_report,
    build_empty_laj_reader_view,
    load_laj_reader_view,
)

BRIEF_PAGES_DATA_SCHEMA = "briefloop.brief_pages.data.v1"
BRIEF_PAGES_BOUNDARY = (
    "Read-only projection. No Gate, approval, delivery, repair, or runtime "
    "authority. LAJ surfaces are Experimental advisory; no finding is neutral "
    "and LAJ utility is NOT MEASURED."
)
LAJ_EXPERIMENTAL_BANNER = (
    "Experimental AI assessment. Advisory only. Not a Gate, delivery decision, "
    "or proof of correctness. Utility NOT MEASURED."
)
IMPROVEMENT_CONSUMPTION_NOTE = (
    "Next run reads only human-approved, deterministically produced "
    "improvement_memory_snapshot."
)
IMPROVEMENT_PLANNED_NOTE = "Disposition/guidance transactions planned (MU-1/MU-2)."
_READER_JSON = LAJ_READER_FILENAMES[1]
_SKIP_DISCOVERY_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


class BriefPagesError(ValueError):
    """Raised when the three-page data contract cannot be built."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_snapshot(workspace: Path):
    try:
        with SQLiteControlStore.open(workspace / "briefloop.db") as store:
            head = store.load_workspace_run_head()
            if head is None:
                raise BriefPagesError("control_store_integrity_invalid")
            return CoreRunDomainVerifier().verify(store, head.current_run_id)
    except BriefPagesError:
        raise
    except (ControlStoreError, CoreRunError, OSError, RuntimeError, ValueError) as exc:
        raise BriefPagesError("control_store_integrity_invalid") from exc


def _row(label: str, value: Any, tone: str = "neutral") -> dict[str, Any]:
    return {"label": label, "value": value, "tone": tone}


def _quality_groups(verified) -> dict[str, list[dict[str, Any]]]:
    snapshot = verified.snapshot
    binding = verified.binding
    source_plan = verified.source_plan
    terminal = classify_terminal_legality(snapshot)
    topology = core_role_topology_policy(binding.role_topology)

    gates = [
        _row(
            item.gate_id,
            item.status,
            "pass"
            if item.status == "pass"
            else ("block" if item.blocking and item.status == "fail" else "attention"),
        )
        for item in sorted(
            snapshot.gate_evaluations, key=lambda entry: (entry.gate_id, entry.evaluation_id)
        )
    ]
    if not gates:
        gates = [
            _row(gate_id, "not_evaluated", "unavailable")
            for gate_id in sorted(binding.gate_strictness)
        ]

    claim_types: dict[str, int] = {}
    for claim in snapshot.claims:
        claim_types[claim.claim_type] = claim_types.get(claim.claim_type, 0) + 1
    providers = sorted(
        {item.provider for item in snapshot.sources if item.provider is not None}
    )

    finalizations = sorted(snapshot.finalizations, key=lambda item: item.finalized_at)
    report_artifacts = [
        item
        for item in snapshot.artifacts
        if "report" in item.artifact_id or "brief" in item.artifact_id
    ]

    return {
        "control": [
            _row("run_id", snapshot.run.run_id),
            _row("runtime", snapshot.run.runtime),
            _row("store_revision", snapshot.store_revision),
            _row("transactions", len(snapshot.transactions)),
            _row("contract_fingerprint", binding.contract_fingerprint),
            _row("role_topology", topology.topology),
        ],
        "source": [
            _row("accepted_sources", len(snapshot.sources)),
            _row("providers", providers or "none"),
            _row("web_search_mode", source_plan.web_search_mode),
            _row("source_routes", len(source_plan.routes)),
            _row("sources_config_sha256", source_plan.sources_config_sha256),
        ],
        "gates": gates,
        "claims": [
            _row("claims", len(snapshot.claims)),
            _row("claim_freezes", len(snapshot.claim_freezes)),
            _row("claim_types", claim_types or "none"),
        ],
        "reader_clean": (
            [
                _row("finalizations", len(finalizations)),
                _row("last_finalized_at", finalizations[-1].finalized_at),
                _row(
                    "report_artifacts",
                    [
                        {
                            "artifact_id": item.artifact_id,
                            "revision": item.current_revision,
                            "status": item.status,
                        }
                        for item in report_artifacts
                    ]
                    or "none",
                ),
            ]
            if finalizations
            else [_row("finalizations", "not_available", "unavailable")]
        ),
        "closeout": [
            _row(
                "terminal_state",
                terminal.terminal_state,
                "pass" if terminal.terminal_state == "delivered" else "neutral",
            ),
            _row("package_ready_records", len(snapshot.package_ready_records)),
            _row("deliveries", len(snapshot.deliveries)),
        ],
    }


def _quality_page(workspace: Path, verified) -> dict[str, Any]:
    projection = build_store_quality_projection(workspace)
    action = classify_core_run_next_action(verified)
    return {
        "status": "available" if projection.get("ok") else "unavailable",
        "reason_code": None if projection.get("ok") else projection.get("reason_code"),
        "boundary": "projection_only_not_gate_or_delivery_authority",
        "projection": projection,
        "groups": _quality_groups(verified),
        "actions": [action.model_dump(mode="json", exclude_unset=False)],
    }


def _discover_laj_view(workspace: Path) -> Path | None:
    candidates: list[Path] = []
    for path in workspace.rglob(_READER_JSON):
        if any(part in _SKIP_DISCOVERY_DIRS for part in path.parts):
            continue
        if path.is_file() and not path.is_symlink():
            candidates.append(path)
    return sorted(candidates)[0] if candidates else None


def _semantic_page(workspace: Path, laj_view_path: str | Path | None) -> dict[str, Any]:
    source = (
        Path(laj_view_path).expanduser()
        if laj_view_path is not None
        else _discover_laj_view(workspace)
    )
    view: LajReaderView
    if source is None or not source.is_file():
        view = build_empty_laj_reader_view(
            status="not_available", reason_code="laj_not_run"
        )
    else:
        try:
            view = load_laj_reader_view(source)
            brief = workspace / "output" / "brief.md"
            if brief.is_file() and not brief.is_symlink():
                view = bind_laj_reader_view_to_report(
                    view, expected_report_sha256=_sha256_file(brief)
                )
        except Exception:
            view = build_empty_laj_reader_view(
                status="invalid", reason_code="laj_reader_view_invalid"
            )

    dimension_ids = list(get_args(FindingDimensionId))
    findings = [
        finding.model_dump(mode="json", exclude_unset=False) for finding in view.findings
    ]
    dimensions = [
        {
            "dimension_id": dimension,
            "state": (
                "finding_reported"
                if any(item["dimension_id"] == dimension for item in findings)
                else "not_assessed_in_view"
            ),
        }
        for dimension in dimension_ids
    ]
    return {
        "status": "not_run" if view.reason_codes == ["laj_not_run"] else view.status,
        "banner": LAJ_EXPERIMENTAL_BANNER,
        "boundary": view.boundary,
        "coverage": {
            "assessed_unit_count": view.assessed_unit_count,
            "finding_count": view.finding_count,
            "withheld_finding_count": view.withheld_finding_count,
            "abstention_count": view.abstention_count,
        },
        "dimensions": dimensions,
        "findings": findings,
        "handoff_note": (
            "Handoff units are evidence needs, not defects; they never trigger Gates."
        ),
        "reason_codes": view.reason_codes,
        "disclaimer": view.disclaimer,
    }


def _improvement_page() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason_code": "pf_review_2_not_shipped",
        "recorded": [],
        "consumption_note": IMPROVEMENT_CONSUMPTION_NOTE,
        "planned_note": IMPROVEMENT_PLANNED_NOTE,
    }


def build_brief_pages_data(
    workspace: str | Path,
    *,
    laj_view_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the full three-page data contract from Store/LAJ sources only."""

    root = Path(workspace).expanduser().resolve()
    verified = _verified_snapshot(root)
    return {
        "schema_version": BRIEF_PAGES_DATA_SCHEMA,
        "generated_at": generated_at or _utc_now(),
        "boundary": BRIEF_PAGES_BOUNDARY,
        "workspace": {
            "run_id": verified.snapshot.run.run_id,
            "runtime": verified.snapshot.run.runtime,
            "store_revision": verified.snapshot.store_revision,
            "authority": "sqlite_control_store",
        },
        "quality": _quality_page(root, verified),
        "semantic": _semantic_page(root, laj_view_path),
        "improvement": _improvement_page(),
    }


__all__ = [
    "BRIEF_PAGES_BOUNDARY",
    "BRIEF_PAGES_DATA_SCHEMA",
    "BriefPagesError",
    "IMPROVEMENT_CONSUMPTION_NOTE",
    "IMPROVEMENT_PLANNED_NOTE",
    "LAJ_EXPERIMENTAL_BANNER",
    "build_brief_pages_data",
]
