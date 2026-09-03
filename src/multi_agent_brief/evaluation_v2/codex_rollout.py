"""Codex rollout adapter: audit-report measurement plus the gate oracle.

Only the pure half of the adapter lives here.  ``build_codex_rollout`` --
the callable that materialises ``case.source_pack`` into a scratch
workspace, drives the auditor role named by ``case.rollout`` through the
packaged Codex kit, and reads back the recorded audit report -- lands with
the Phase-2 rollout task and is deliberately NOT defined in this module,
so the CLI seam (``multi_agent_brief.cli.eval_commands._build_rollout``)
keeps failing closed until real invocation wiring exists.

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
