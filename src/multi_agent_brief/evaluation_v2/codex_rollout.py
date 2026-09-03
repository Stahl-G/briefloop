"""Codex rollout adapter: the offline findings-to-outcome mapping.

Only the pure half of the adapter lives here.  ``build_codex_rollout`` --
the callable that materialises ``case.source_pack`` into a scratch
workspace, drives the role named by ``case.rollout`` through the packaged
Codex kit, and reads back the recorded quality-gate report -- lands with
the Phase-2 rollout task and is deliberately NOT defined in this module,
so the CLI seam (``multi_agent_brief.cli.eval_commands._build_rollout``)
keeps failing closed until real invocation wiring exists.

What is here is the mapping a rollout hands its recorded findings through:

* ``parse_reported_findings(payload)`` accepts a recorded
  ``quality_gate_report.json`` payload and returns its raw finding
  records.
* ``outcome_from_findings(case, findings, *, blocked)`` validates each raw
  finding into a ``ReportedFinding`` and folds them, plus the case's
  ground truth, into a ``RolloutOutcome``.

Raw finding record shape (producer: ``_finding()`` in
``src/multi_agent_brief/quality_gates/evaluation.py``; recorded examples in
``examples/reference-workspaces/industry-weekly-demo/artifacts/quality_gate_report.json``
and
``src/multi_agent_brief/evaluation_cases/fixtures/cases/provenance_projection_minimal/workspace/output/intermediate/quality_gate_report.json``):
a record carries ``finding_id``, ``gate_id``, ``finding_type``,
``category``, ``severity``, ``blocking_level`` with a redundant boolean
mirror ``blocking``, repair/stage/artifact routing ids, ``claim_id``,
``source_id``, ``line_number``, prose fields, and ``metadata``.  There is
no ``locator`` field.

Field mapping onto ``ReportedFinding``:

* ``finding_type`` -> verbatim; must be one of the evaluation contract's
  ``FINDING_TYPES``.  The gate layer emits a wider vocabulary (for example
  ``market_quote_metadata_incomplete``); a rollout reporting a type outside
  the evaluation contract is a loud error, never a silent drop, because a
  dropped finding would quietly read as a miss.
* ``blocking_level`` -> verbatim; only ``blocking`` and ``warning`` are
  accepted (finding producers never emit another level).  The ``blocking``
  boolean mirror is ignored.
* ``locator`` -> derived when not recorded explicitly: a non-blank
  ``locator`` key on the record wins verbatim (extra keys are not
  forbidden by the gate-report validator, and the Phase-2 rollout prompt
  can require the role to record one); otherwise
  ``"<anchor>#L<line_number>"`` with the first non-blank anchor of
  ``source_id``, ``claim_id``, ``artifact_id`` when ``line_number`` is a
  positive integer; otherwise the first non-blank anchor verbatim.  A
  record naming no position at all is a loud error for the same reason as
  an unknown type: it could never satisfy the double match.

``blocked`` is an argument, not derived here.  The delivery verdict lives
in report-level state (``status`` / ``gate_results``) and reading it is
the Phase-2 adapter's job; this function only passes it through.

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
    EvaluationCase,
    ReportedFinding,
    RolloutOutcome,
)
from multi_agent_brief.quality_gates.contract import QUALITY_GATE_SCHEMA

#: The only blocking levels finding producers emit; anything else --
#: including the ``"none"`` the gate validator tolerates -- is rejected.
_BLOCKING_LEVELS: tuple[str, ...] = ("blocking", "warning")

#: Anchor preference for derived locators: a source is finer than a claim,
#: a claim is finer than the artifact the finding was raised on.
_LOCATOR_ANCHOR_KEYS: tuple[str, ...] = ("source_id", "claim_id", "artifact_id")


def _text_or_none(value: Any) -> str | None:
    """Return stripped non-blank text, else ``None``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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

    line_number = finding.get("line_number")
    if (
        isinstance(line_number, int)
        and not isinstance(line_number, bool)
        and line_number >= 1
        and anchor is not None
    ):
        return f"{anchor}#L{line_number}"

    if anchor is not None:
        return anchor

    raise ValueError(
        f"finding[{index}] ({finding_type}) carries no locator: record an "
        "explicit 'locator', or a position derivable from "
        "'source_id'/'claim_id'/'artifact_id' together with 'line_number'"
    )


def _normalize_finding(finding: Mapping[str, Any], *, index: int) -> ReportedFinding:
    """Validate one raw gate-report finding into a ``ReportedFinding``."""
    if not isinstance(finding, Mapping):
        raise ValueError(
            f"finding[{index}] must be an object, got {type(finding).__name__}"
        )

    finding_type = _text_or_none(finding.get("finding_type"))
    if finding_type is None:
        raise ValueError(
            f"finding[{index}] is missing required non-blank 'finding_type'"
        )
    if finding_type not in FINDING_TYPES:
        raise ValueError(
            f"finding[{index}] has unknown finding_type {finding_type!r}; "
            f"expected one of the evaluation contract types: "
            f"{sorted(FINDING_TYPES)}"
        )

    blocking_level = finding.get("blocking_level")
    if blocking_level not in _BLOCKING_LEVELS:
        raise ValueError(
            f"finding[{index}] has unknown blocking_level {blocking_level!r}; "
            f"expected one of {list(_BLOCKING_LEVELS)}"
        )

    return ReportedFinding(
        finding_type=finding_type,
        locator=_derive_locator(finding, index=index, finding_type=finding_type),
        blocking_level=blocking_level,
    )


def parse_reported_findings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract the raw finding records from a recorded gate-report payload.

    Accepts the payload exactly as the gate layer records it (top-level
    ``schema_version``, ``status``, ``gate_results``, ``findings``,
    ``metadata``, timestamps) and returns shallow copies of the finding
    records, in recorded order, for ``outcome_from_findings``.
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

    raw: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise ValueError(f"findings[{index}] must be an object")
        raw.append(dict(finding))
    return raw


def outcome_from_findings(
    case: EvaluationCase,
    findings: Iterable[Mapping[str, Any]],
    *,
    blocked: bool,
) -> RolloutOutcome:
    """Fold raw reported findings onto the case's ground truth.

    Every raw finding is validated into a ``ReportedFinding`` and recorded
    on the outcome in input order.  ``found_defect_ids`` lists the seeded
    defects whose ``(finding_type, locator)`` pair a finding matches
    (level-agnostic, in case order); ``flagged_claim_locators`` lists the
    clean-claim locators any finding's locator equals; ``blocked`` is the
    argument, passed through untouched.
    """
    reported = [
        _normalize_finding(finding, index=index)
        for index, finding in enumerate(findings)
    ]

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
    )
