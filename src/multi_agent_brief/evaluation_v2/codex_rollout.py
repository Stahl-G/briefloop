"""Codex rollout adapter: audit-report measurement plus the gate oracle.

``build_codex_rollout`` -- the callable that materialises
``case.source_pack`` into a scratch workspace, drives the auditor role
named by ``case.rollout`` through the packaged Codex kit, and reads back
the recorded audit report -- is defined at the bottom of this module on
the file-level integration path (see its comment block for why the
Store-bound paths are not used).  The CLI seam
(``multi_agent_brief.cli.eval_commands._build_rollout``) resolves it
lazily, so ``eval run`` still fails closed on the empty skeleton corpus
until real cases exist.

Why measurement reads the agent's audit report, not the gates (measured,
not assumed): the deterministic gate evaluator's inputs are pure artifacts
-- the brief markdown, the claim ledger, the workspace config.  It never
reads the agent's ``audit_report.json``, and the auditor role writes ONLY
``audit_report.json``.  Gate findings therefore cannot measure the auditor:
scoring auditor cases from gate findings is bit-identical whether the
auditor was excellent, terrible, or never ran.  The two parsers below split
the two jobs accordingly:

* ``parse_reported_audit(payload)`` is the MEASUREMENT parser.  It reads the
  agent-written ``output/intermediate/audit_report.json`` (contract:
  ``multi_agent_brief/contracts/schemas/audit_report.py``; observed in two
  real codex rollouts) and returns the compliant findings plus the count of
  noncompliant ones.  It must NOT crash on an unknown ``finding_type`` or a
  missing anchor: in the 120-run measurement loop a bad finding is
  recorded (noncompliant), never fatal.  The rollout envelope injects the
  harness-owned reporting contract (``corpus_data/envelope-auditor-reporting.md``)
  so every evaluated variant sees the identical vocabulary constraint.
* ``parse_gate_findings_for_oracle(payload)`` is the CORPUS-CONSTRUCTION
  oracle.  The generator uses it, once per constructed case, to verify each
  seeded defect is gate-detectable before the case enters the corpus.
  Loud errors on unknown vocabulary or positionless findings are correct
  here: that is corpus-side validation of staged artifacts, a bad staging
  must stop construction, and it never runs in the measurement loop.

Audit-report finding shape (from the contract and the two real rollouts):
each finding carries ``finding_id``, ``severity`` (low|medium|high),
``finding_type`` (a FREE string -- observed values include
``unsupported_fact_missing_citation`` and ``target_scope_mismatch``, which
are NOT the canonical vocabulary), ``description``; optionally
``recommendation``, ``related_claim_id``, ``line_number`` (int|null),
``evidence``.  Anchors appear as ``related_claim_id`` or ``line_number``
when present; positions otherwise live only in prose, which is unusable.

Measurement field mapping (``parse_reported_audit``):

* compliant  =  ``finding_type`` within ``FINDING_TYPES`` AND an anchor
  present AND a severity that maps onto the evaluation's two blocking
  levels.  Compliant findings become ``ReportedFinding`` records:
  ``locator`` = ``related_claim_id`` when present, else
  ``"audited_brief#L<line_number>"`` (the auditor's input brief artifact);
  ``blocking_level`` maps severity ``high`` -> ``blocking`` and
  ``medium``/``low`` -> ``warning`` (the scorer never reads this level; it
  is recorded so the raw outcome stays auditable).
* everything else -- unknown type, no anchor, unmappable severity, or a
  non-object entry -- increments ``noncompliant_finding_count`` and is
  otherwise dropped from matching: it can never satisfy the double match,
  and dropping it from ``findings`` (while counting it) is exactly the
  "recorded, not fatal" behavior the measurement loop needs.

``blocked`` is an argument to ``outcome_from_findings``, not derived
there.  The recommended derivation, on the same payload the findings were
parsed from, is ``blocked = payload.get("audit_status") == "fail"``.

Detection is level-agnostic: a seeded defect counts as found when
``finding_type`` AND ``locator`` both match a reported finding, whatever
the reported level -- the disagreement is preserved in ``findings`` and
measured by ``block_agreement`` in the scorer, never by recall.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from multi_agent_brief.evaluation_v2.contracts import (
    FINDING_TYPES,
    GENERATION_DEFECT_TYPES,
    EvaluationCase,
    ReportedFinding,
    RolloutOutcome,
)
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_SCHEMA

#: Measurement-side severity mapping: the agent's audit report carries a
#: three-level severity; the evaluation contract records two blocking
#: levels.  ``high`` is the only severity the envelope contract ties to
#: delivery-blocking defects; ``medium`` and ``low`` are advisory.
_SEVERITY_TO_BLOCKING: dict[str, str] = {
    "high": "blocking",
    "medium": "warning",
    "low": "warning",
}

#: Oracle vocabulary: the corpus-construction oracle accepts every type the
#: deterministic gates can legitimately raise on a staged case -- the four
#: detection types plus the six generation-quality types (the demo
#: reference workspace, for one, records a ``final_*`` warning alongside
#: its staged defects).  This union is NOT the measurement vocabulary; the
#: measurement loop accepts only ``FINDING_TYPES``.
_ORACLE_TYPES: frozenset[str] = FINDING_TYPES | GENERATION_DEFECT_TYPES

#: The only blocking levels finding producers emit; anything else --
#: including the ``"none"`` the gate validator tolerates -- is rejected.
_BLOCKING_LEVELS: tuple[str, ...] = ("blocking", "warning")

#: Anchor preference for derived oracle locators: a source is finer than a
#: claim, a claim is finer than the artifact the finding was raised on.
_LOCATOR_ANCHOR_KEYS: tuple[str, ...] = ("source_id", "claim_id", "artifact_id")

#: The auditor's input brief artifact: the anchor ``line_number`` refers to
#: when the agent's finding carries no ``related_claim_id``.
_AUDITED_BRIEF_ANCHOR: str = "audited_brief"


def _text_or_none(value: Any) -> str | None:
    """Return stripped non-blank text, else ``None``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _positive_int_or_none(value: Any) -> int | None:
    """Return the value when it is a positive integer, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


# ---------------------------------------------------------------------------
# Measurement: the agent's own audit report
# ---------------------------------------------------------------------------


def parse_reported_audit(
    payload: Mapping[str, Any],
) -> tuple[list[ReportedFinding], int]:
    """Parse an agent-written ``audit_report.json`` payload for measurement.

    Returns ``(compliant_findings, noncompliant_finding_count)``:
    vocabulary-legal, anchored findings as ``ReportedFinding`` records in
    reported order, plus the count of reported findings that are outside
    the detection vocabulary, carry no anchor, or carry no mappable
    severity.  A missing ``findings`` key counts as no findings.  Only
    payload-level structural failures raise (payload not an object,
    ``findings`` not a list); every finding-level defect is recorded, not
    fatal.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("audit report payload must be a JSON object")

    findings = payload.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        raise ValueError("audit report payload must carry a 'findings' list")

    compliant: list[ReportedFinding] = []
    noncompliant = 0
    for finding in findings:
        record = _compliant_audit_finding(finding)
        if record is None:
            noncompliant += 1
        else:
            compliant.append(record)
    return compliant, noncompliant


def _compliant_audit_finding(finding: Any) -> ReportedFinding | None:
    """Return the finding as a ``ReportedFinding``, or ``None`` when the
    finding cannot be recorded compliantly (counted by the caller)."""
    if not isinstance(finding, Mapping):
        return None

    finding_type = _text_or_none(finding.get("finding_type"))
    if finding_type is None or finding_type not in FINDING_TYPES:
        return None

    related_claim_id = _text_or_none(finding.get("related_claim_id"))
    line_number = _positive_int_or_none(finding.get("line_number"))
    if related_claim_id is not None:
        locator = related_claim_id
    elif line_number is not None:
        locator = f"{_AUDITED_BRIEF_ANCHOR}#L{line_number}"
    else:
        return None

    severity = _text_or_none(finding.get("severity"))
    if severity is None or severity not in _SEVERITY_TO_BLOCKING:
        return None

    return ReportedFinding(
        finding_type=finding_type,
        locator=locator,
        blocking_level=_SEVERITY_TO_BLOCKING[severity],
    )


def outcome_from_findings(
    case: EvaluationCase,
    findings: Iterable[ReportedFinding],
    *,
    blocked: bool,
    noncompliant_finding_count: int = 0,
) -> RolloutOutcome:
    """Fold parsed reported findings onto the case's ground truth.

    ``findings`` are the compliant ``ReportedFinding`` records from
    ``parse_reported_audit``; ``noncompliant_finding_count`` is the count it
    returned alongside them.  ``found_defect_ids`` lists the seeded defects
    whose ``(finding_type, locator)`` pair a finding matches
    (level-agnostic, in case order); ``flagged_claim_locators`` lists the
    clean-claim locators any finding's locator equals.

    ``blocked`` is a passthrough argument.  The recommended derivation,
    from the same audit-report payload the findings were parsed from, is
    ``blocked = payload.get("audit_status") == "fail"``.
    """
    reported = list(findings)

    reported_pairs = {(finding.finding_type, finding.locator) for finding in reported}
    reported_locators = {finding.locator for finding in reported}

    found_defect_ids = [
        defect.defect_id
        for defect in case.seeded_defects
        if (defect.finding_type, defect.locator) in reported_pairs
    ]
    flagged_claim_locators = [
        locator for locator in case.clean_claims if locator in reported_locators
    ]

    return RolloutOutcome(
        case_id=case.case_id,
        found_defect_ids=found_defect_ids,
        flagged_claim_locators=flagged_claim_locators,
        blocked=blocked,
        findings=reported,
        noncompliant_finding_count=noncompliant_finding_count,
    )


# ---------------------------------------------------------------------------
# Corpus construction: the deterministic gates as ground-truth oracle
# ---------------------------------------------------------------------------


def _derive_locator(
    finding: Mapping[str, Any], *, index: int, finding_type: str
) -> str:
    """Derive the evaluation locator from a gate-report finding record."""
    explicit = _text_or_none(finding.get("locator"))
    if explicit is not None:
        return explicit

    anchor = next(
        (
            anchor
            for anchor in (
                _text_or_none(finding.get(key)) for key in _LOCATOR_ANCHOR_KEYS
            )
            if anchor is not None
        ),
        None,
    )

    line_number = _positive_int_or_none(finding.get("line_number"))
    if line_number is not None and anchor is not None:
        return f"{anchor}#L{line_number}"

    if anchor is not None:
        return anchor

    raise ValueError(
        f"finding[{index}] ({finding_type}) carries no locator: record an "
        "explicit 'locator', or a position derivable from "
        "'source_id'/'claim_id'/'artifact_id' together with 'line_number'"
    )


def _normalize_finding(finding: Mapping[str, Any], *, index: int) -> tuple[str, str]:
    """Validate one raw gate-report finding into an oracle (type, locator) pair."""
    if not isinstance(finding, Mapping):
        raise ValueError(
            f"finding[{index}] must be an object, got {type(finding).__name__}"
        )

    finding_type = _text_or_none(finding.get("finding_type"))
    if finding_type is None:
        raise ValueError(
            f"finding[{index}] is missing required non-blank 'finding_type'"
        )
    if finding_type not in _ORACLE_TYPES:
        raise ValueError(
            f"finding[{index}] has unknown finding_type {finding_type!r}; "
            f"expected one of the oracle gate types: "
            f"{sorted(_ORACLE_TYPES)}"
        )

    blocking_level = finding.get("blocking_level")
    if blocking_level not in _BLOCKING_LEVELS:
        raise ValueError(
            f"finding[{index}] has unknown blocking_level {blocking_level!r}; "
            f"expected one of {list(_BLOCKING_LEVELS)}"
        )

    return (
        finding_type,
        _derive_locator(finding, index=index, finding_type=finding_type),
    )


def parse_gate_findings_for_oracle(
    payload: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """Corpus-construction oracle: parse a recorded gate-report payload.

    Accepts the payload exactly as the gate layer records it (top-level
    ``schema_version``, ``status``, ``gate_results``, ``findings``,
    ``metadata``, timestamps) and returns every finding as a
    ``(finding_type, locator)`` pair, in recorded order, so the generator
    can assert each seeded defect's pair is present -- the definition of
    "gate-detectable at construction time".  Plain pairs, not
    ``ReportedFinding`` records: the oracle legitimately observes
    generation-family gate findings (see ``_ORACLE_TYPES``), which the
    measurement-side ``ReportedFinding`` Literal deliberately cannot hold.

    Unlike ``parse_reported_audit``, this parser is deliberately loud:
    unknown vocabulary, missing positions, and payload-shape drift raise
    ``ValueError`` instead of being counted, because a corpus case whose
    staged artifacts do not produce matchable gate findings must stop
    construction, and this parser never runs in the measurement loop.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("quality-gate report payload must be a JSON object")

    schema_version = payload.get("schema_version")
    if schema_version != QUALITY_GATE_SCHEMA:
        raise ValueError(
            f"unexpected quality-gate report schema_version {schema_version!r}; "
            f"expected {QUALITY_GATE_SCHEMA!r}"
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("quality-gate report payload must carry a 'findings' list")

    return [
        _normalize_finding(finding, index=index)
        for index, finding in enumerate(findings)
    ]


# ---------------------------------------------------------------------------
# Real rollout: file-level adapter (validated end-to-end by the P2-T0 smokes)
# ---------------------------------------------------------------------------
#
# Why file-level: the Store-bound paths (`runtime install`, `runtime
# continue`) reject workspaces whose seeding used the extracted staging
# chain, and re-initialising with a real codex binding diverges from that
# chain at source-plan construction (observed: stage_artifact_binding_invalid).
# The file-level path -- staged workspace, kit files, envelope-driven
# `codex exec`, harvest from the envelope scratch directory -- is the one
# empirically validated by two real rollouts plus the constrained-envelope
# confirmation, so it is the integration path.

from datetime import timedelta  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
from importlib.resources import files as resource_files  # noqa: E402
from pathlib import Path  # noqa: E402

import yaml  # noqa: E402

from multi_agent_brief.evaluation_v2 import staging  # noqa: E402
from multi_agent_brief.runtime_assets import install_runtime_kit  # noqa: E402

#: Directory (under the workspace scratch tree) the envelope names as the
#: rollout's output location.  The real rollouts wrote there even when the
#: task text named a different path: the role contract's scratch convention
#: wins, so the adapter harvests exactly here.
ROLLOUT_SCRATCH_DIR = "scratch/eval-rollout"

#: Sentinel-substituted workspace config.  Shape mirrors what a demo
#: workspace init produces (proven against the seeding chain); every
#: content-coupled field is substituted per case: the company must agree
#: with the brief and ledger, claim-count minimums must match the seeded
#: ledger size, and report dates drive the freshness window.
_CONFIG_TEMPLATE = """project:
  name: "@@BRIEF_TITLE@@"
  company: "@@COMPANY@@"
  industry: "@@INDUSTRY@@"
  role: "strategy_office"
  audience: "management"
audience_profile:
  id: "management"
language:
  interface: "en-US"
  output: "en-US"
  source_handling: "preserve_original"
report:
  cadence: "weekly"
  date: "@@REPORT_DATE@@"
  max_source_age_days: @@MAX_AGE@@
  fail_on_stale_source: true
input:
  path: "input"
output:
  path: "output"
  formats:
    - "markdown"
  filename_template: "{project_name}_{report_date}"
  named_outputs: true
brief_quality:
  min_items: @@ITEMS@@
  min_zh_chars: 3000
  require_dates: true
  allow_quiet_week_exception: false
selector:
  enabled: true
  max_items: @@ITEMS@@
  require_fresh_source: true
  topic_diversity: true
retrieval:
  enabled: false
audit:
  fail_on_missing_source: true
  fail_on_stale_source: true
  redaction_scan: true
  require_claim_citations: true
safety:
  no_investment_advice: true
  no_legal_advice: true
  no_trading_signals: true
  require_human_review: true
controlstore_v2:
  schema_version: "briefloop.workspace_controlstore_bootstrap.v2"
  workspace_id: "WS-EVALV200000000000000000000000000001"
  run_id: "RUN-EVALV200000000000000000000000000001"
  runtime: "codex"
  role_topology: "single_session"
  input_governance_required: true
  gate_strictness:
    coverage_omission: true
    editor_new_fact: true
    final_abstract_quality: true
    material_fact: true
    freshness: true
    target_relevance: true
  run_direction:
    schema_version: "briefloop.run_direction.v2"
    subject_name: "@@COMPANY@@"
    industry_or_theme: "@@INDUSTRY@@"
    brief_title: "@@BRIEF_TITLE@@"
    task_objective: "Auditor detection evaluation on a seeded synthetic workspace."
    audience: "management"
    audience_profile: "management"
    output_language: "en-US"
    source_handling: "preserve_original"
    cadence: "weekly"
    focus_areas:
      - "Operations"
    excluded_topics: []
    forbidden_sources: []
    source_profile: "conservative"
    web_search_mode: "disabled"
    output_formats:
      - "markdown"
    report_date: "@@REPORT_DATE@@"
    report_window_start: "@@WINDOW_START@@"
    report_window_end: "@@REPORT_DATE@@"
    max_source_age_days: @@MAX_AGE@@
    selector_max_items: @@ITEMS@@
    target_terms:
      - "Operations"
"""

_USER_TEMPLATE = """# User Context

## Company

@@COMPANY@@

## Industry

@@INDUSTRY@@

## Objective

Produce the weekly brief for @@COMPANY@@ from the frozen claim ledger only.
"""

_COMMON_FRAME = """You are operating inside a BriefLoop workspace evaluation rollout.

Environment facts (honest scoping):
- This workspace was pre-seeded to your stage by deterministic tooling.
- The Store-backed preflight commands (`briefloop contract show`,
  `briefloop runtime invocation-validate`) are NOT available in this rollout;
  do not run them. Work from the files named below.
- Do not modify briefloop.db or anything outside the named outputs.
- Finish by writing the required output file, then stop.
"""


def _reporting_contract() -> str:
    """Read the harness-owned reporting contract packaged with the corpus."""
    resource = (
        resource_files("multi_agent_brief.evaluation_v2")
        .joinpath("corpus_data", "envelope-auditor-reporting.md")
        .read_text(encoding="utf-8")
    )
    return resource


def _render_config(case: EvaluationCase, pack: Mapping[str, Any]) -> str:
    config = pack.get("config") or {}
    project = config.get("project") or {}
    report = config.get("report") or {}
    company = str(project.get("name", "Synthetic Company"))
    industry = str(project.get("industry", "synthetic industry"))
    report_date = case.report_date
    max_age = int(report.get("max_source_age_days", 14))
    items = len(pack.get("claim_ledger") or [])
    from datetime import date

    window_start = (
        date.fromisoformat(report_date) - timedelta(days=max_age)
    ).isoformat()
    return (
        _CONFIG_TEMPLATE.replace("@@BRIEF_TITLE@@", f"{company} Weekly Brief")
        .replace("@@COMPANY@@", company)
        .replace("@@INDUSTRY@@", industry)
        .replace("@@REPORT_DATE@@", report_date)
        .replace("@@WINDOW_START@@", window_start)
        .replace("@@MAX_AGE@@", str(max_age))
        .replace("@@ITEMS@@", str(items))
    )


def _execution_authorization_for(
    workspace: Path, sources: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Build the init-time execution authorization for the case source pack.

    Mirrors the authorized-pack intake lane the runtime supports for multi-
    source runs (manifest-bound, atomic, one invocation): every case source
    becomes one manifest member with its content written under ``input/``.
    """
    from multi_agent_brief.contracts.v2 import ExecutionSourceManifest
    from multi_agent_brief.control_store.serialization import (
        canonical_json_bytes,
        sha256_hex,
    )

    members: list[dict[str, Any]] = []
    for source in sources:
        source_id = str(source["source_id"])
        input_path = f"input/{source_id.lower()}.txt"
        content = str(source.get("title", "synthetic source")).encode("utf-8")
        target = workspace / input_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        members.append(
            {
                "source_id": source_id,
                "input_path": input_path,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_media_type": "text/plain",
                "origin_type": "manual_evidence",
                "acquisition_method": "manual_evidence",
                "material_kind": "full_content",
                "provider": None,
                "locator": {"kind": "file", "path": input_path},
                "title": str(source.get("title", "Synthetic source")),
                "publisher": source.get("publisher"),
                "published_at": str(source["published_at"]),
                "retrieved_at": staging.NOW,
                "source_category": "other",
                "retrieval_source_type": "local_file",
                "underlying_evidence_type": "filing",
                "raw_underlying_evidence_type": None,
                "document_kind": None,
                "opened_at": None,
                "resolved_at": None,
            }
        )
    manifest = ExecutionSourceManifest.model_validate(
        {"schema_version": ExecutionSourceManifest.schema_id, "members": members},
        strict=True,
    )
    canonical = canonical_json_bytes(
        manifest.model_dump(mode="json", exclude_unset=False)
    )
    return {
        "schema_version": "briefloop.run_execution_authorization_input.v2",
        "completion_target": "finalized_local",
        "source_manifest": manifest.model_dump(mode="json", exclude_unset=False),
        "source_manifest_sha256": sha256_hex(canonical),
        "source_manifest_member_count": len(members),
        "repair_budget": 1,
    }


def materialize_case_workspace(case: EvaluationCase, workspace: Path) -> None:
    """Materialise ``case.source_pack`` into a seeded, editor-complete workspace.

    Writes the content-coupled workspace files (config, sources, user
    context), then drives the deterministic seeding chain from the extracted
    staging module with the case's own sources, claims, analyst snapshot and
    brief -- no canned payloads, no model calls.  After this the workspace
    sits exactly where the P2-T0 smoke workspaces sat: everything the
    auditor role reads is on disk under ``output/intermediate/``.
    """
    pack = yaml.safe_load(case.source_pack)
    if not isinstance(pack, Mapping):
        raise ValueError(f"case {case.case_id}: source_pack must be a YAML mapping")

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "config.yaml").write_text(
        _render_config(case, pack), encoding="utf-8"
    )
    (workspace / "sources.yaml").write_text(
        "source_strategy:\n"
        '  profile: "conservative"\n'
        "  enabled_providers:\n"
        '    - "manual"\n'
        "manual:\n"
        "  enabled: true\n"
        "  sources: []\n",
        encoding="utf-8",
    )
    user = _USER_TEMPLATE
    project = pack.get("config", {}).get("project", {})
    user = user.replace("@@COMPANY@@", str(project.get("name", "Synthetic Company")))
    user = user.replace("@@INDUSTRY@@", str(project.get("industry", "synthetic industry")))
    (workspace / "user.md").write_text(user, encoding="utf-8")

    sources = list(pack.get("sources") or [])
    claims = list(pack.get("claim_ledger") or [])

    authorization = _execution_authorization_for(workspace, sources)
    report_config = (pack.get("config") or {}).get("report") or {}
    max_age = int(report_config.get("max_source_age_days", 14))
    from datetime import date as _date

    window_start = (_date.fromisoformat(case.report_date) - timedelta(days=max_age)).isoformat()
    project_config = (pack.get("config") or {}).get("project") or {}
    company = str(project_config.get("name", "Synthetic Company"))
    direction_overrides = {
        "subject_name": company,
        "industry_or_theme": str(project_config.get("industry", "synthetic industry")),
        "brief_title": f"{company} Weekly Brief",
        "report_date": case.report_date,
        "report_window_start": window_start,
        "report_window_end": case.report_date,
        "max_source_age_days": max_age,
        "selector_max_items": len(claims),
    }
    service = staging._initialize(
        workspace,
        execution_authorization=authorization,
        input_governance_required=True,
        run_direction_overrides=direction_overrides,
    )
    doctor = service.doctor_check(
        staging._record(
            staging.IntegrityCheckRequest,
            request_id="REQ-DOCTOR-001",
            run_id=staging.RUN_ID,
            expected_store_revision=staging._store_revision(workspace),
        )
    )
    assert doctor.status == "committed", doctor.to_dict()

    pack_result = service.apply_authorized_source_pack()
    assert pack_result.status == "committed", pack_result.to_dict()
    from multi_agent_brief.core_run_v2.verifier import CoreRunDomainVerifier

    with staging.SQLiteControlStore.open(workspace / "briefloop.db") as store:
        snapshot = CoreRunDomainVerifier().verify(store, staging.RUN_ID).snapshot
    staging._complete_stage(
        service, workspace,
        stage_id="source-discovery",
        artifacts=[
            (
                item.source_manifest_artifact.artifact_id,
                item.source_manifest_artifact.revision,
            )
            for item in snapshot.run_execution_authorizations
        ]
        + [
            (item.content_artifact_id, item.content_artifact_revision)
            for item in snapshot.sources
        ],
    )
    staging._complete_stage(
        service, workspace,
        stage_id="input-governance",
        artifacts=[("input_classification", 1)],
    )

    scout = staging._start_invocation(
        service, workspace,
        request_id="REQ-INVOKE-SCOUT",
        stage_id="scout", role_id="scout",
    )
    staging._submit_proposal(
        workspace,
        lane="candidate",
        invocation_id=scout,
        request_id="REQ-CANDIDATE-001",
        artifact_id="candidate_claims",
        payload={
            "schema_version": "briefloop.candidate_claims_proposal.v2",
            "proposal_id": "PROP-CANDIDATE-001",
            "run_id": staging.RUN_ID,
            "created_at": staging.NOW,
            "candidates": [
                {
                    "candidate_id": f"CAND-{index:03d}",
                    "source_id": claim["source_id"],
                    "statement": claim["statement"],
                    "evidence_text": claim.get("evidence_text", claim["statement"]),
                    "topic": "operations",
                    "claim_type": claim.get("claim_type", "fact"),
                    "confidence": "high",
                }
                for index, claim in enumerate(claims, start=1)
            ],
        },
    )
    # Screening policy: selected+high requires current-window evidence, so a
    # selected candidate backed by a source outside the report window (the
    # stale-source defect material) must be selected at medium priority.
    def _screening_priority(source_id: str) -> str:
        for source in sources:
            if source["source_id"] == source_id:
                published = _date.fromisoformat(str(source["published_at"]))
                start = _date.fromisoformat(window_start)
                end = _date.fromisoformat(case.report_date)
                return "high" if start <= published <= end else "medium"
        return "medium"

    screening = staging._start_invocation(
        service, workspace,
        request_id="REQ-INVOKE-SCREEN",
        stage_id="scout", role_id="scout",
    )
    staging._submit_proposal(
        workspace,
        lane="screened",
        invocation_id=screening,
        request_id="REQ-SCREENED-001",
        artifact_id="screened_candidates",
        payload={
            "schema_version": "briefloop.screened_candidates_proposal.v2",
            "proposal_id": "PROP-SCREENED-001",
            "run_id": staging.RUN_ID,
            "candidate_claims_proposal_id": "PROP-CANDIDATE-001",
            "created_at": staging.NOW,
            "decisions": [
                {
                    "candidate_id": f"CAND-{index:03d}",
                    "decision": "selected",
                    "reason_code": "public_evidence_in_scope",
                    "explanation": "Public evidence is in scope.",
                    "priority": _screening_priority(claims[index - 1]["source_id"]),
                }
                for index in range(1, len(claims) + 1)
            ],
        },
    )
    staging._complete_stage(
        service, workspace,
        stage_id="scout",
        artifacts=[("candidate_claims", 1), ("screened_candidates", 1)],
    )

    claims_invocation = staging._start_invocation(
        service, workspace,
        request_id="REQ-INVOKE-CLAIMS",
        stage_id="claim-ledger", role_id="claim-ledger",
    )
    staging._submit_proposal(
        workspace,
        lane="claim-drafts",
        invocation_id=claims_invocation,
        request_id="REQ-CLAIM-DRAFTS-001",
        artifact_id="claim_drafts",
        payload={
            "schema_version": "briefloop.claim_drafts_proposal.v2",
            "proposal_id": "PROP-CLAIM-DRAFTS-001",
            "run_id": staging.RUN_ID,
            "screened_candidates_proposal_id": "PROP-SCREENED-001",
            "created_at": staging.NOW,
            "drafts": [
                {
                    "draft_id": f"DRAFT-{index:03d}",
                    "statement": claim["statement"],
                    "evidence_text": claim.get("evidence_text", claim["statement"]),
                    "source_ids": [claim["source_id"]],
                    "claim_type": claim.get("claim_type", "fact"),
                }
                for index, claim in enumerate(claims, start=1)
            ],
        },
    )
    frozen = staging.ClaimFreezeService(workspace, clock=staging.CLOCK).freeze(
        staging._record(
            staging.ClaimFreezeRequest,
            request_id="REQ-FREEZE-001",
            run_id=staging.RUN_ID,
            claim_drafts_proposal_id="PROP-CLAIM-DRAFTS-001",
            expected_claim_drafts_artifact={
                "artifact_id": "claim_drafts",
                "revision": 1,
            },
            expected_store_revision=staging._store_revision(workspace),
            expected_ledger_revision=0,
        )
    )
    assert frozen.status == "committed", frozen.to_dict()
    staging._complete_stage(
        service, workspace,
        stage_id="claim-ledger",
        artifacts=[("claim_drafts", 1), ("claim_ledger", 1)],
    )

    analyst = staging._start_invocation(
        service, workspace,
        request_id="REQ-INVOKE-ANALYST",
        stage_id="analyst", role_id="analyst",
    )
    analyst_path = workspace / "scratch" / analyst / "analyst_draft_snapshot.md"
    analyst_path.parent.mkdir(parents=True, exist_ok=True)
    analyst_path.write_text(str(pack.get("analyst_draft_snapshot", "")), encoding="utf-8")
    analyst_result = staging.ArtifactAcceptanceService(
        workspace, clock=staging.CLOCK
    ).submit_owned_artifact(
        staging._record(
            staging.OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-ANALYST",
            run_id=staging.RUN_ID,
            artifact_id="analyst_draft_snapshot",
            invocation_id=analyst,
            producer_tool_id="analyst-snapshot-v2",
            input_path=analyst_path.relative_to(workspace).as_posix(),
            expected_store_revision=staging._store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact=None,
        )
    )
    assert analyst_result.status == "committed", analyst_result.to_dict()
    staging._complete_stage(
        service, workspace,
        stage_id="analyst",
        artifacts=[("analyst_draft_snapshot", 1)],
    )

    editor = staging._start_invocation(
        service, workspace,
        request_id="REQ-INVOKE-EDITOR",
        stage_id="editor", role_id="editor",
    )
    brief_path = workspace / "scratch" / editor / "audited_brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(str(pack.get("audited_brief", "")), encoding="utf-8")
    editor_result = staging.ArtifactAcceptanceService(
        workspace, clock=staging.CLOCK
    ).submit_owned_artifact(
        staging._record(
            staging.OwnedArtifactSubmitRequest,
            request_id="REQ-ARTIFACT-EDITOR",
            run_id=staging.RUN_ID,
            artifact_id="audited_brief",
            invocation_id=editor,
            producer_tool_id=None,
            input_path=brief_path.relative_to(workspace).as_posix(),
            expected_store_revision=staging._store_revision(workspace),
            expected_artifact_revision=0,
            expected_parent_artifact={
                "artifact_id": "analyst_draft_snapshot",
                "revision": 1,
            },
        )
    )
    assert editor_result.status == "committed", editor_result.to_dict()
    staging._complete_stage(
        service, workspace,
        stage_id="editor",
        artifacts=[("analyst_draft_snapshot", 1), ("audited_brief", 1)],
    )


def _build_envelope(case: EvaluationCase) -> dict[str, Any]:
    return {
        "schema_version": "briefloop.role_task_envelope.v2",
        "run_id": "RUN-EVAL-ROLLOUT",
        "invocation_id": f"INV-EVAL-{case.case_id[:24]}",
        "store_revision": 0,
        "action_fingerprint": "0" * 64,
        "role_id": case.rollout.role,
        "stage_id": case.rollout.role,
        "scratch_directory": ROLLOUT_SCRATCH_DIR,
        "allowed_output_filenames": ["audit_report.json"],
        "proposal_schema_id": "briefloop.audit_report_artifact.v2",
        "adapter_binding_fingerprint": "0" * 64,
        "source_plan_fingerprint": "0" * 64,
        "executor_kind": "codex",
        "context_mode": "workspace",
        "review_mode": "none",
        "dispatch_instruction": "execute_in_current_session",
        "task_instructions": (
            "Audit the frozen brief against the frozen claim ledger. Inputs: "
            "output/intermediate/audited_brief.md, output/intermediate/claim_ledger.json, "
            "config.yaml, user.md. Write audit_report.json under your scratch directory "
            f"({ROLLOUT_SCRATCH_DIR}/audit_report.json) using the current AuditReport "
            "contract (top-level audit_status, audit_score, findings, metadata). "
            "Report every real defect you find with full detail in each finding."
        ),
    }


def _role_instructions(workspace: Path, role: str) -> str:
    agent_toml = workspace / ".codex" / "agents" / f"briefloop-{role}.toml"
    text = agent_toml.read_text(encoding="utf-8")
    start = text.index("developer_instructions = '''") + len(
        "developer_instructions = '''"
    )
    end = text.index("'''", start)
    return text[start:end].strip()


def _harvest_audit_report(workspace: Path) -> Mapping[str, Any]:
    """Read the rollout's audit report from the envelope scratch directory."""
    report_path = workspace / ROLLOUT_SCRATCH_DIR / "audit_report.json"
    if not report_path.exists():
        candidates = sorted(
            workspace.glob("scratch/*/audit_report.json"), key=lambda p: p.stat().st_mtime
        )
        if not candidates:
            raise RuntimeError("rollout produced no audit_report.json under scratch/")
        report_path = candidates[-1]
    return json.loads(report_path.read_text(encoding="utf-8"))


def build_codex_rollout(
    *,
    workdir: str | Path,
    model: str | None = None,
    exec_timeout_seconds: int = 900,
):
    """Build a real rollout function driving the packaged Codex kit per case.

    Per case: materialise a seeded workspace (deterministic, no model
    calls), install the kit files, launch ``codex exec`` with the role
    instructions from the generated agent TOML plus the harness-owned
    reporting contract, harvest the agent-written audit report from the
    envelope scratch directory, and fold it into a ``RolloutOutcome``.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    contract_text = _reporting_contract()

    def rollout(case: EvaluationCase) -> RolloutOutcome:
        workspace = workdir / case.case_id
        materialize_case_workspace(case, workspace)
        if not (workspace / ".codex" / "agents" / "briefloop-auditor.toml").exists():
            install_runtime_kit(workspace=workspace, runtime="codex")

        envelope = _build_envelope(case)
        prompt = (
            f"{_COMMON_FRAME}\n\n## RoleTaskEnvelope (host-supplied)\n\n```json\n"
            f"{json.dumps(envelope, indent=2)}\n```\n\n## Role instructions\n\n"
            f"{_role_instructions(workspace, case.rollout.role)}\n\n{contract_text}"
        )
        cmd = [
            "codex", "exec",
            "--cd", str(workspace),
            "-s", "workspace-write",
            "--skip-git-repo-check",
        ]
        if model:
            cmd += ["-m", model]
        cmd.append("-")
        env = dict(os.environ)
        env.pop("CODEX_HOME", None)  # auth lives in the user's ~/.codex
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=exec_timeout_seconds,
        )
        log_dir = workspace / ROLLOUT_SCRATCH_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "codex_stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (log_dir / "codex_stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex exec failed for case {case.case_id} "
                f"(exit {proc.returncode}); see {log_dir}"
            )

        payload = _harvest_audit_report(workspace)
        findings, noncompliant = parse_reported_audit(payload)
        return outcome_from_findings(
            case,
            findings,
            blocked=payload.get("audit_status") == "fail",
            noncompliant_finding_count=noncompliant,
        )

    return rollout
