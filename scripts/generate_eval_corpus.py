#!/usr/bin/env python3
"""Deterministic generator for the packaged agent-rollout corpus.

Clean brief sentences are conservative paraphrases of their claims: the
executive lines restate the claim statements, the detail lines repeat the
claims' evidence text.  Generic expansion phrasing appears ONLY on seeded
defect sentences (see DECISIONS.md D3: random-sampling rejection of the
earlier corpus whose clean sentences drifted from claim evidence).
Deterministically generate the 80-case detection evaluation corpus.

Writes ``src/multi_agent_brief/evaluation_v2/corpus_data/cases/<split>/*.yaml``
(80 files) and rewrites ``corpus_data/manifest.yaml``.  Pure function of the
static tables in this file: no randomness, no seeded PRNG, no network, no
model calls, no clock reads.  Running it twice produces identical bytes.

Corpus composition (validator-enforced, asserted below before anything is
written):

* 80 cases: 40 train / 40 val; 24 blocking + 16 clean per split (ratio 0.60).
* Per finding type across the corpus: ``claim_support_matrix_blocking_support``
  x12, ``number_without_source`` x12, ``stale_source`` x12,
  ``target_priority_claim_missing_from_summary`` x12.
* 14 legacy-derived scenarios (the 6 defect-bearing legacy specs expressible in
  the 4-type detection vocabulary + the 8 no-defect specs as clean
  true-negative material) + 66 new cases.  The two legacy specs whose seeded
  defects live outside the detection vocabulary (``target_relevance_gap`` and
  the five warning-only ``final_*`` types) are NOT representable here and are
  skipped; see corpus_data/REVIEW.md.

Each case embeds, inside the ``source_pack`` string, every synthetic input the
rollout adapter needs (config values, source metadata with published dates,
the claim ledger, the seeded audited brief, the analyst snapshot, and the
claim-support matrix rows): one self-contained YAML file per case, no external
source files.

Construction-time oracle (no model calls): before a case is written, the
generator re-runs the deterministic offline checks on the exact bytes it is
about to ship -- ``run_deterministic_audit`` for number/stale detection, the
``target_relevance`` gate logic for summary-priority detection, and structural
support-matrix checks for the matrix type -- and asserts every seeded defect
is detectable at its recorded locator while every clean claim stays clean.
Any miss aborts generation loudly.

Usage:
    PYTHONPATH=src python3 scripts/generate_eval_corpus.py
    PYTHONPATH=src python3 scripts/generate_eval_corpus.py --output-dir /tmp/corpus
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from multi_agent_brief.audit.deterministic import (  # noqa: E402
    NUMBER_PATTERN,
    run_deterministic_audit,
)
from multi_agent_brief.core.citations import SRC_REF_PATTERN  # noqa: E402
from multi_agent_brief.core.claim_ledger import ClaimLedger  # noqa: E402
from multi_agent_brief.core.schemas import Claim  # noqa: E402
from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES  # noqa: E402
from multi_agent_brief.evaluation_v2.corpus import (  # noqa: E402
    CORPUS_SCHEMA_VERSION,
    MIN_BLOCKING_PER_SPLIT,
    SPLITS,
    load_corpus,
    validate_corpus,
)

# The target-priority oracle reuses the production gate logic (single source
# of truth for what the deterministic gate can express) rather than
# re-implementing its summary-refs rule here.
from multi_agent_brief.quality_gates.evaluation import (  # noqa: E402
    _target_relevance_findings,
)

DEFAULT_OUTPUT_DIR = (
    ROOT / "src" / "multi_agent_brief" / "evaluation_v2" / "corpus_data"
)

WINDOW_DAYS = 14
STALE_OFFSET_DAYS = 20  # just outside the 14-day freshness window

TOTAL_CASES = 80
CASES_PER_SPLIT = 40
BLOCKING_PER_SPLIT = 24
CLEAN_PER_SPLIT = 16
BLOCKING_PER_TYPE_PER_SPLIT = 6  # 4 types x 6 = 24 blocking per split
CASES_PER_BLOCKING_TYPE = 12  # 4 types x 12 = 48 blocking cases (ratio 0.60)

CSM = "claim_support_matrix_blocking_support"
NWS = "number_without_source"
STALE = "stale_source"
TPC = "target_priority_claim_missing_from_summary"

#: Support-matrix labels that constitute blocking-support evidence (mirror of
#: the gate rule summary: "unsupported, contradicted, or insufficiently
#: evidenced").  Anything else is a weak/inference row, not this finding type.
BLOCKING_SUPPORT_LABELS = frozenset(
    {"unsupported", "contradicted", "insufficient_evidence"}
)

# ---------------------------------------------------------------------------
# Static content tables (the ONLY inputs composition depends on)
# ---------------------------------------------------------------------------

REPORT_DATES: tuple[str, ...] = (
    "2026-01-12",
    "2026-02-09",
    "2026-03-16",
    "2026-04-13",
    "2026-05-11",
    "2026-06-08",
    "2026-07-06",
    "2026-08-03",
)


@dataclass(frozen=True)
class World:
    """One plainly fictional company setting a case is written in."""

    company: str
    industry: str
    regulator: str
    press: str
    event: str
    second_event: str
    metric: str
    topic: str
    facility: str


WORLDS: tuple[World, ...] = (
    World("ExampleCo", "synthetic logistics", "Example Commerce Regulator",
          "Sample Trade Daily", "opened a regional distribution hub",
          "signed a multi-year fleet agreement", "fleet utilization",
          "regional coverage", "depots"),
    World("SampleCorp", "synthetic energy", "National Sample Energy Authority",
          "Example Energy Weekly", "commissioned a battery storage site",
          "signed a grid connection agreement", "generation uptime",
          "storage rollout", "substations"),
    World("Demo Dynamics", "synthetic manufacturing",
          "Example Industrial Authority", "Sample Factory Review",
          "opened a precision parts plant", "launched an automation retrofit",
          "line utilization", "plant expansion", "lines"),
    World("TestCo Foods", "synthetic agriculture", "Example Food Safety Agency",
          "Sample Agri Report", "opened a cold-chain warehouse",
          "signed a regional supplier contract", "cold-chain throughput",
          "distribution reach", "warehouses"),
    World("MockCo Rail", "synthetic transport", "Example Transport Board",
          "Sample Rail Journal", "launched a regional rail service",
          "ordered additional trainsets", "service punctuality",
          "network expansion", "stations"),
    World("StubCo Health", "synthetic healthcare", "Example Health Regulator",
          "Sample Care Weekly", "opened a diagnostic clinic",
          "partnered with a public lab network", "lab turnaround",
          "clinic rollout", "clinics"),
    World("PlaceholderCo Telecom", "synthetic telecom",
          "Example Communications Authority", "Sample Spectrum News",
          "activated a regional broadband ring", "expanded fixed-line coverage",
          "network availability", "broadband rollout", "exchanges"),
    World("DummyCo Retail", "synthetic retail", "Example Consumer Board",
          "Sample Retail Watch", "opened a flagship storefront",
          "launched a customer loyalty program", "same-store sales growth",
          "store expansion", "stores"),
    World("FauxCo Chemicals", "synthetic materials",
          "Example Chemicals Inspectorate", "Sample Materials Digest",
          "commissioned a polymer line", "signed an offtake agreement",
          "batch yield", "capacity expansion", "reactors"),
    World("PretendCo Mining", "synthetic mining", "Example Mining Board",
          "Sample Mining Ledger", "reopened a pilot mine site",
          "signed a haulage agreement", "ore grade consistency",
          "site restart", "shafts"),
    World("NotRealCo Insurance", "synthetic insurance",
          "Example Insurance Supervisor", "Sample Underwriter Weekly",
          "launched a parametric cover product", "renewed a reinsurance treaty",
          "claims ratio", "product rollout", "branches"),
    World("FictionalCo Aviation", "synthetic aviation",
          "Example Aviation Authority", "Sample Flight Review",
          "opened a maintenance base", "added a regional route",
          "dispatch reliability", "route expansion", "hubs"),
    World("UnrealCo Semiconductors", "synthetic semiconductors",
          "Example Technology Agency", "Sample Chip Bulletin",
          "commissioned a packaging line", "qualified a new materials supplier",
          "wafer yield", "capacity upgrade", "fabs"),
    World("ImaginaryCo Water", "synthetic utilities", "Example Water Regulator",
          "Sample Utility Monitor", "upgraded a treatment plant",
          "signed a municipal supply contract", "treatment uptime",
          "network renewal", "plants"),
    World("SyntheticCo Pharma", "synthetic pharmaceuticals",
          "Example Medicines Agency", "Sample Pharma Notes",
          "opened a formulation lab", "initiated a bridging study",
          "batch release rate", "lab expansion", "labs"),
    World("VirtualCo Data", "synthetic data centers",
          "Example Digital Infrastructure Board", "Sample Compute Weekly",
          "energized a data hall", "contracted renewable supply",
          "hall occupancy", "capacity buildout", "halls"),
)

#: The four legacy claim-support-matrix plots, mirrored from the legacy specs
#: (mixed_metric_scope, media_only_legal_policy, company_event_latest_check,
#: third_party_price_snapshot).  New csm cases cycle the same four shapes.
CSM_PLOTS: tuple[str, ...] = (
    "metric_scope_conflict",
    "official_source_missing",
    "latest_official_check_missing",
    "third_party_price_snapshot",
)

#: Worlds for legacy-derived cases, keyed by legacy spec short name.
LEGACY_WORLDS: dict[str, World] = {
    "unsupported_material_fact": World(
        "Synthetic TargetCo", "synthetic operations",
        "Synthetic Operations Regulator", "Synthetic Trade Daily",
        "completed a systems upgrade", "renewed a site lease",
        "operating margin", "upgrade program", "platforms",
    ),
    "stale_current_claim": World(
        "Synthetic TargetCo", "synthetic operations",
        "Synthetic Operations Regulator", "Synthetic Trade Daily",
        "announced an operating update", "renewed a maintenance contract",
        "service availability", "operations update", "platforms",
    ),
    "mixed_metric_scope_support_blocker": World(
        "Synthetic Demo Company", "synthetic manufacturing",
        "Synthetic Industrial Regulator", "Synthetic Manufacturing Weekly",
        "opened an assembly hall", "signed a components agreement",
        "output yield", "scope coverage", "lines",
    ),
    "media_only_legal_policy_blocks_research_review": World(
        "Synthetic MediaCo", "synthetic media",
        "Synthetic Communications Regulator", "Synthetic Press Review",
        "launched a regional channel", "renewed a broadcast license",
        "audience reach", "licensing matters", "stations",
    ),
    "company_event_missing_latest_official_check": World(
        "Synthetic EventCo", "synthetic facilities",
        "Synthetic Facilities Regulator", "Synthetic Facilities Bulletin",
        "opened a regional office", "renewed a facilities permit",
        "site occupancy", "office rollout", "offices",
    ),
    "third_party_price_snapshot_formal_block": World(
        "Synthetic PriceCo", "synthetic commodities",
        "Synthetic Markets Regulator", "Synthetic Markets Bulletin",
        "listed a reference contract", "published a fee schedule",
        "traded volume", "reference pricing", "contracts",
    ),
    "provenance_projection_minimal": World(
        "Synthetic ProvenanceCo", "synthetic logistics",
        "Synthetic Logistics Regulator", "Synthetic Logistics Weekly",
        "opened a sorting center", "signed a transport agreement",
        "sorting throughput", "network growth", "centers",
    ),
    "reader_facing_source_appendix": World(
        "Synthetic AppendixCo", "synthetic publishing",
        "Synthetic Publishing Board", "Synthetic Publishing Weekly",
        "released a public dataset", "renewed a distribution deal",
        "dataset adoption", "publication growth", "archives",
    ),
    "reader_clean_failed_no_delivery_promotion": World(
        "Synthetic DeliveryCo", "synthetic delivery services",
        "Synthetic Delivery Authority", "Synthetic Delivery Weekly",
        "opened a parcel depot", "signed a last-mile contract",
        "parcel throughput", "depot growth", "depots",
    ),
    "static_hermes_no_skip_finalize": World(
        "Synthetic GateOrderCo", "synthetic operations",
        "Synthetic Operations Regulator", "Synthetic Operations Weekly",
        "passed a readiness review", "closed a compliance program",
        "readiness score", "program completion", "sites",
    ),
    "release_readiness_forged_event_blocker": World(
        "Synthetic ReleaseCo", "synthetic software",
        "Synthetic Software Board", "Synthetic Software Weekly",
        "shipped a release candidate", "renewed a support agreement",
        "release pass rate", "release cadence", "services",
    ),
    "unauthorized_institution_branding_blocks_release": World(
        "Synthetic BrandingCo", "synthetic education",
        "Synthetic Education Board", "Synthetic Education Weekly",
        "opened a training center", "renewed a curriculum license",
        "enrollment rate", "center rollout", "centers",
    ),
    "formal_release_missing_human_approval": World(
        "Synthetic ApprovalCo", "synthetic finance",
        "Synthetic Finance Authority", "Synthetic Finance Weekly",
        "filed a annual disclosure", "renewed an audit engagement",
        "filing completeness", "disclosure program", "filings",
    ),
    "same_evidence_reader_quality_regression": World(
        "Synthetic RegressionCo", "synthetic research",
        "Synthetic Research Board", "Synthetic Research Weekly",
        "published a benchmark study", "renewed a data-sharing agreement",
        "study coverage", "benchmark program", "studies",
    ),
}


# ---------------------------------------------------------------------------
# Case specification table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    split: str
    kind: str  # "csm" | "nws" | "stale" | "tpc" | "clean"
    legacy_spec: str = ""  # legacy spec short name; "" for new cases
    csm_plot: str = ""  # required when kind == "csm"
    report_date: str = ""  # override; defaults to REPORT_DATES[seq % 8]


#: Defect-bearing legacy scenarios (detection-vocabulary subset of the specs).
LEGACY_DEFECT_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # (split, legacy short name, kind, csm plot or "")
    ("train", "unsupported_material_fact", "nws", ""),
    ("val", "stale_current_claim", "stale", ""),
    ("train", "mixed_metric_scope_support_blocker", "csm",
     "metric_scope_conflict"),
    ("val", "media_only_legal_policy_blocks_research_review", "csm",
     "official_source_missing"),
    ("train", "company_event_missing_latest_official_check", "csm",
     "latest_official_check_missing"),
    ("val", "third_party_price_snapshot_formal_block", "csm",
     "third_party_price_snapshot"),
)

#: No-defect legacy scenarios: clean true-negative material for the corpus.
LEGACY_CLEAN_SPECS: tuple[tuple[str, str], ...] = (
    ("train", "provenance_projection_minimal"),
    ("train", "reader_facing_source_appendix"),
    ("train", "release_readiness_forged_event_blocker"),
    ("train", "unauthorized_institution_branding_blocks_release"),
    ("val", "reader_clean_failed_no_delivery_promotion"),
    ("val", "static_hermes_no_skip_finalize"),
    ("val", "formal_release_missing_human_approval"),
    ("val", "same_evidence_reader_quality_regression"),
)

KIND_ORDER: tuple[str, ...] = ("csm", "nws", "stale", "tpc", "clean")
KIND_TO_FINDING_TYPE: dict[str, str] = {
    "csm": CSM,
    "nws": NWS,
    "stale": STALE,
    "tpc": TPC,
}


def build_case_specs() -> list[CaseSpec]:
    """Deterministically enumerate all 80 case specifications."""
    specs: list[CaseSpec] = []
    for split in SPLITS:
        for kind in KIND_ORDER:
            legacy_for_kind = [
                (name, plot)
                for (
                    legacy_split,
                    name,
                    legacy_kind,
                    plot,
                ) in LEGACY_DEFECT_SPECS
                if legacy_split == split and legacy_kind == kind
            ] + [
                (name, "")
                for clean_split, name in LEGACY_CLEAN_SPECS
                if clean_split == split and kind == "clean"
            ]
            for name, plot in legacy_for_kind:
                specs.append(
                    CaseSpec(
                        case_id=f"{split}_legacy_{name}",
                        split=split,
                        kind=kind,
                        legacy_spec=name,
                        csm_plot=plot,
                        report_date=_legacy_report_date(name),
                    )
                )
            new_needed = (
                BLOCKING_PER_TYPE_PER_SPLIT - len(legacy_for_kind)
                if kind != "clean"
                else CLEAN_PER_SPLIT - len(legacy_for_kind)
            )
            code = "clean" if kind == "clean" else kind
            for index in range(1, new_needed + 1):
                specs.append(
                    CaseSpec(
                        case_id=f"{split}_{code}_{index:02d}",
                        split=split,
                        kind=kind,
                        csm_plot=CSM_PLOTS[(index - 1) % len(CSM_PLOTS)]
                        if kind == "csm"
                        else "",
                    )
                )
    return specs


def _legacy_report_date(legacy_spec: str) -> str:
    """Pin report dates the legacy specs themselves state."""
    if legacy_spec == "stale_current_claim":
        return "2026-06-08"  # the spec's fixed report date
    return ""


# ---------------------------------------------------------------------------
# Deterministic numeric derivation (no randomness, pure modular arithmetic)
# ---------------------------------------------------------------------------


def _pct(seq: int) -> int:
    return 61 + (seq * 7) % 31


def _revenue_musd(seq: int) -> int:
    return 120 + (seq * 17) % 150


def _guidance_musd(seq: int) -> int:
    return 180 + (seq * 11) % 220


def _units(seq: int) -> int:
    return 3 + (seq * 5) % 9


def _price(seq: int) -> int:
    return 12 + (seq * 13) % 44


def _claim(
    claim_id: str,
    statement: str,
    source_id: str,
    published_at: str,
    evidence_text: str,
    *,
    importance: str = "normal",
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "statement": statement,
        "source_id": source_id,
        "evidence_text": evidence_text,
        "source_url": f"https://example.invalid/claims/{claim_id.lower()}",
        "claim_type": "fact",
        "confidence": "medium",
        "metadata": {"published_at": published_at, "importance": importance},
    }


def _source(
    source_id: str, title: str, publisher: str, published_at: str
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": title,
        "publisher": publisher,
        "published_at": published_at,
        "url": f"https://example.invalid/sources/{source_id.lower()}",
    }


def _summary_refs(brief: str) -> set[str]:
    """Claim ids cited inside the '## Executive Summary' section."""
    refs: set[str] = set()
    in_summary = False
    for line in brief.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_summary = "executive summary" in stripped.lower()
            continue
        if not in_summary or not stripped:
            continue
        for match in SRC_REF_PATTERN.finditer(stripped):
            refs.add(match.group(1))
    return refs


class _LiteralDumper(yaml.SafeDumper):
    """Safe dumper that emits multiline strings as readable block literals.

    Width is effectively unbounded so scalars never fold: case files stay
    line-stable and the embedded brief appears verbatim, which makes the
    shipped locator lines human-inspectable.  Deterministic by construction.
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|"
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralDumper.add_representer(str, _represent_str)


def _dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_LiteralDumper,
        sort_keys=False,
        width=1_000_000,
        allow_unicode=True,
    )


# ---------------------------------------------------------------------------
# Case builders
# ---------------------------------------------------------------------------


def _clean_skeleton(
    world: World, report_date: str, seq: int
) -> dict[str, Any]:
    """Build the shared 3-claim / 3-source / cited-brief skeleton."""
    day = date.fromisoformat(report_date)
    d1 = (day - timedelta(days=2)).isoformat()
    d2 = (day - timedelta(days=5)).isoformat()
    d3 = (day - timedelta(days=9)).isoformat()
    pct = _pct(seq)

    sources = [
        _source(
            "SRC-0001",
            f"{world.company} facility filing",
            world.regulator,
            d1,
        ),
        _source(
            "SRC-0002",
            f"{world.company} quarterly disclosure",
            world.company,
            d2,
        ),
        _source(
            "SRC-0003",
            f"{world.company} coverage in {world.press}",
            world.press,
            d3,
        ),
    ]
    claims = [
        _claim(
            "CL-0001",
            f"{world.company} {world.event} on {d1}.",
            "SRC-0001",
            d1,
            f"A filing published by the {world.regulator} records that "
            f"{world.company} {world.event} on {d1}.",
        ),
        _claim(
            "CL-0002",
            f"{world.company} reported {world.metric} of {pct} percent in its "
            "latest quarterly disclosure.",
            "SRC-0002",
            d2,
            f"The quarterly disclosure states {world.metric} of {pct} "
            "percent for the reporting period.",
        ),
        _claim(
            "CL-0003",
            f"{world.company} {world.second_event}, according to trade "
            "coverage.",
            "SRC-0003",
            d3,
            f"Trade coverage in {world.press} reports that "
            f"{world.company} {world.second_event}.",
        ),
    ]
    brief_lines = [
        f"# {world.company} Weekly Brief",
        "",
        "## Executive Summary",
        "",
        f"{world.company} {world.event} on {d1}. [src:CL-0001]",
        f"{world.company} reported {world.metric} of {pct} percent in its "
        "latest quarterly disclosure. [src:CL-0002]",
        "",
        "## Detail",
        "",
        f"A filing published by the {world.regulator} on {d1} recorded that "
        f"{world.company} {world.event}. [src:CL-0001]",
        f"The quarterly disclosure states {world.metric} of {pct} "
        "percent for the reporting period. [src:CL-0002]",
        f"Trade coverage in {world.press} reports that "
        f"{world.company} {world.second_event}. [src:CL-0003]",
    ]
    return {
        "sources": sources,
        "claims": claims,
        "brief_lines": brief_lines,
        "clean_claims": ["CL-0001", "CL-0002", "CL-0003"],
        "matrix": [],
    }


def _build_nws(
    world: World, report_date: str, seq: int, legacy: str
) -> dict[str, Any]:
    skeleton = _clean_skeleton(world, report_date, seq)
    guidance = (
        42 if legacy == "unsupported_material_fact" else _guidance_musd(seq)
    )
    # The defect: a material quantified assertion with NO [src:] citation.
    skeleton["brief_lines"].append(
        f"Management guidance points to full-year revenue of "
        f"${guidance} million."
    )
    defect_line = len(skeleton["brief_lines"])
    skeleton["defects"] = [
        {
            "defect_id": "d1",
            "finding_type": NWS,
            "locator": f"audited_brief#L{defect_line}",
            "expected_blocking_level": "blocking",
        }
    ]
    return skeleton


def _build_stale(
    world: World, report_date: str, seq: int, legacy: str
) -> dict[str, Any]:
    skeleton = _clean_skeleton(world, report_date, seq)
    day = date.fromisoformat(report_date)
    stale_day = (day - timedelta(days=STALE_OFFSET_DAYS)).isoformat()
    units = _units(seq)
    skeleton["sources"].append(
        _source(
            "SRC-0004",
            f"{world.company} operations disclosure",
            world.company,
            stale_day,
        )
    )
    statement = (
        f"{world.company} announced a current operating update."
        if legacy == "stale_current_claim"
        else f"{world.company} currently operates {units} {world.facility} "
        "across the region."
    )
    skeleton["claims"].append(
        _claim(
            "CL-0004",
            statement,
            "SRC-0004",
            stale_day,
            f"An operations disclosure published on {stale_day} states that "
            f"{statement.rstrip('.')}.",
        )
    )
    skeleton["brief_lines"].append(
        f"{statement} [src:CL-0004]"
    )
    skeleton["defects"] = [
        {
            "defect_id": "d1",
            "finding_type": STALE,
            "locator": "CL-0004",
            "expected_blocking_level": "blocking",
        }
    ]
    return skeleton


def _build_tpc(
    world: World, report_date: str, seq: int, legacy: str
) -> dict[str, Any]:
    skeleton = _clean_skeleton(world, report_date, seq)
    day = date.fromisoformat(report_date)
    fresh_day = (day - timedelta(days=3)).isoformat()
    skeleton["sources"].append(
        _source(
            "SRC-0004",
            f"{world.company} priority program record",
            world.regulator,
            fresh_day,
        )
    )
    statement = (
        f"{world.company} holds priority approval for {world.topic} under "
        f"the {world.regulator} program."
    )
    skeleton["claims"].append(
        _claim(
            "CL-0004",
            statement,
            "SRC-0004",
            fresh_day,
            f"A program record published by the {world.regulator} lists "
            f"{world.company} with priority approval for {world.topic}.",
            importance="high",
        )
    )
    # The claim is cited in the body but deliberately NOT in the summary:
    # that omission is the defect.
    skeleton["brief_lines"].append(
        f"{statement} [src:CL-0004]"
    )
    skeleton["defects"] = [
        {
            "defect_id": "d1",
            "finding_type": TPC,
            "locator": "CL-0004",
            "expected_blocking_level": "blocking",
        }
    ]
    return skeleton


def _build_csm(
    world: World, report_date: str, seq: int, legacy: str, plot: str
) -> dict[str, Any]:
    skeleton = _clean_skeleton(world, report_date, seq)
    day = date.fromisoformat(report_date)
    d_early = (day - timedelta(days=9)).isoformat()
    d_late = (day - timedelta(days=2)).isoformat()
    d_mid = (day - timedelta(days=4)).isoformat()
    d_regional = (day - timedelta(days=6)).isoformat()
    pct2 = _pct(seq) + 4
    price = _price(seq)

    if plot == "metric_scope_conflict":
        # The claim spans two scopes; each attached source measures one.
        statement = (
            f"{world.company} reported {world.metric} of {pct2} percent "
            "across all regions and periods."
        )
        skeleton["sources"].extend(
            [
                _source(
                    "SRC-0004",
                    f"{world.company} annual global filing",
                    world.regulator,
                    d_mid,
                ),
                _source(
                    "SRC-0005",
                    f"{world.company} quarterly regional disclosure",
                    world.company,
                    d_regional,
                ),
            ]
        )
        evidence = (
            f"The annual global filing measures the worldwide figure and the "
            f"quarterly regional disclosure measures a single region; "
            f"neither states the combined all-scope {world.metric} of "
            f"{pct2} percent."
        )
        source_id = "SRC-0004"
        reason_code = "metric_scope_conflict"
    elif plot == "official_source_missing":
        # A legal/policy claim supported only by secondary press coverage.
        statement = (
            f"The {world.regulator} opened an enforcement review of "
            f"{world.company}."
        )
        skeleton["sources"].append(
            _source(
                "SRC-0004",
                f"{world.company} enforcement review coverage",
                world.press,
                d_late,
            )
        )
        evidence = (
            f"Press coverage in {world.press} reports the enforcement "
            f"review; no regulator, agency, or company filing in the pack "
            "documents it."
        )
        source_id = "SRC-0004"
        reason_code = "official_source_missing"
    elif plot == "latest_official_check_missing":
        # The event is real and official, but the claim cites only the
        # earlier filing; a later official confirmation exists uncited.
        statement = f"{world.company} {world.event} according to its initial filing."
        skeleton["sources"].extend(
            [
                _source(
                    "SRC-0004",
                    f"{world.company} initial event filing",
                    world.regulator,
                    d_early,
                ),
                _source(
                    "SRC-0005",
                    f"{world.company} corrected event confirmation",
                    world.regulator,
                    d_late,
                ),
            ]
        )
        evidence = (
            f"The initial filing dated {d_early} records the event; a later "
            f"official confirmation dated {d_late} completes the record and "
            "is not accounted for by the claim."
        )
        source_id = "SRC-0004"
        reason_code = "latest_official_check_missing"
    else:  # third_party_price_snapshot
        # A formal price-level claim backed only by an aggregator snapshot.
        statement = (
            f"{world.company} set a formal reference price of ${price} per "
            "unit."
        )
        skeleton["sources"].append(
            _source(
                "SRC-0004",
                f"{world.company} reference price snapshot",
                "Sample Market Data Aggregator",
                d_late,
            )
        )
        evidence = (
            f"An aggregator snapshot dated {d_late} shows ${price} per "
            "unit; no primary venue record or official disclosure in the "
            "pack establishes the formal price."
        )
        source_id = "SRC-0004"
        reason_code = "third_party_price_snapshot_formal_block"

    published = next(
        source["published_at"]
        for source in skeleton["sources"]
        if source["source_id"] == source_id
    )
    skeleton["claims"].append(
        _claim("CL-0004", statement, source_id, published, evidence)
    )
    skeleton["brief_lines"].append(f"{statement} [src:CL-0004]")
    skeleton["matrix"] = [
        {
            "claim_id": "CL-0004",
            "support_label": "insufficient_evidence",
            "support_strength": "none",
            "support_reason": (
                f"{reason_code}: the support attached to this high-materiality "
                "claim is recorded as insufficient for delivery-blocking "
                "support."
            ),
            "required_action": "block_release",
            "repair_owner": "claim-ledger",
            "decision_source": "deterministic_support_matrix",
        }
    ]
    skeleton["defects"] = [
        {
            "defect_id": "d1",
            "finding_type": CSM,
            "locator": "CL-0004",
            "expected_blocking_level": "blocking",
        }
    ]
    return skeleton


def build_case_payload(spec: CaseSpec, seq: int) -> dict[str, Any]:
    """Build one full EvaluationCase payload (the pure generator core)."""
    if spec.legacy_spec:
        world = LEGACY_WORLDS[spec.legacy_spec]
    else:
        world = WORLDS[seq % len(WORLDS)]
    report_date = spec.report_date or REPORT_DATES[seq % len(REPORT_DATES)]

    if spec.kind == "clean":
        built = _clean_skeleton(world, report_date, seq)
        built["defects"] = []
    elif spec.kind == "nws":
        built = _build_nws(world, report_date, seq, spec.legacy_spec)
    elif spec.kind == "stale":
        built = _build_stale(world, report_date, seq, spec.legacy_spec)
    elif spec.kind == "tpc":
        built = _build_tpc(world, report_date, seq, spec.legacy_spec)
    elif spec.kind == "csm":
        built = _build_csm(
            world, report_date, seq, spec.legacy_spec, spec.csm_plot
        )
    else:  # pragma: no cover - table is closed
        raise AssertionError(f"unknown kind {spec.kind!r}")

    brief = "\n".join(built["brief_lines"]) + "\n"
    source_pack = {
        "config": {
            "project": {
                "name": world.company,
                "industry": world.industry,
            },
            "report": {
                "date": report_date,
                "cadence": "weekly",
                "max_source_age_days": WINDOW_DAYS,
            },
            "selector_max_items": len(built["claims"]),
        },
        "sources": built["sources"],
        "claim_ledger": built["claims"],
        "claim_support_matrix": built["matrix"],
        "analyst_draft_snapshot": brief,
        "audited_brief": brief,
    }
    return {
        "case_id": spec.case_id,
        "synthetic": True,
        "source_pack": _dump(source_pack),
        "report_date": report_date,
        "rollout": {"role": "auditor", "runtime": "codex"},
        "seeded_defects": built["defects"],
        "clean_claims": built["clean_claims"],
    }


# ---------------------------------------------------------------------------
# Construction-time oracle (deterministic, no model calls)
# ---------------------------------------------------------------------------


def _fail(case_id: str, message: str) -> None:
    raise AssertionError(f"oracle failure for {case_id}: {message}")


def verify_payload(payload: dict[str, Any]) -> None:
    """Assert ground truth for one built case from its exact shipped bytes.

    This is the construction-time oracle: it re-derives detectability from the
    payload the generator is about to write, using the production deterministic
    audit and gate logic plus structural checks.  Any violation raises.
    """
    case_id = payload["case_id"]
    pack = yaml.safe_load(payload["source_pack"])
    if not isinstance(pack, dict):
        _fail(case_id, "source_pack does not parse to a mapping")
    config = pack["config"]
    sources = pack["sources"]
    rows = pack["claim_ledger"]
    matrix = pack["claim_support_matrix"]
    brief = pack["audited_brief"]
    report_date = payload["report_date"]
    company = config["project"]["name"]

    # -- config consistency invariants (REVIEW.md P2-T0 constraints) -------
    if config["report"]["date"] != report_date:
        _fail(case_id, "embedded config report date disagrees with case")
    if config["report"]["max_source_age_days"] != WINDOW_DAYS:
        _fail(case_id, "embedded freshness window is not the default 14 days")
    if config["selector_max_items"] != len(rows):
        _fail(case_id, "selector_max_items must equal the claim count")
    title = brief.splitlines()[0]
    if company not in title:
        _fail(case_id, "brief title does not carry the configured company")

    # -- ledger materialization --------------------------------------------
    ledger = ClaimLedger([Claim.from_dict(row) for row in rows])
    if len(ledger) != len(rows):
        _fail(case_id, "duplicate claim ids in ledger")
    source_ids = {source["source_id"] for source in sources}
    for row in rows:
        if row["source_id"] not in source_ids:
            _fail(case_id, f"claim {row['claim_id']} cites an unknown source")

    # -- deterministic audit (number/stale world) ---------------------------
    audit = run_deterministic_audit(
        brief,
        ledger,
        report_date=report_date,
        max_source_age_days=WINDOW_DAYS,
    )
    numbers = [
        f for f in audit.findings if f.finding_type == "number_without_source"
    ]
    stales = [f for f in audit.findings if f.finding_type == "stale_source"]

    # -- target-priority gate oracle ----------------------------------------
    target_findings = _target_relevance_findings(
        markdown=brief,
        ledger=ledger,
        config={"project": {"name": company}},
        user_text="",
        reader_facing_mode=False,
        strict=True,
        stages=[],
        artifacts=[],
    )
    tpc_findings = [
        f
        for f in target_findings
        if f["finding_type"] == TPC
    ]
    gap_findings = [
        f
        for f in target_findings
        if f["finding_type"] == "target_relevance_gap"
    ]

    kinds = {d["finding_type"] for d in payload["seeded_defects"]}

    # -- per-type detectability ---------------------------------------------
    for defect in payload["seeded_defects"]:
        locator = defect["locator"]
        if defect["finding_type"] == NWS:
            line_no = int(locator.split("#L", 1)[1])
            lines = brief.splitlines()
            if not 1 <= line_no <= len(lines):
                _fail(case_id, f"locator {locator} outside the brief")
            line = lines[line_no - 1]
            if SRC_REF_PATTERN.search(line):
                _fail(case_id, f"locator {locator} line carries a citation")
            if not NUMBER_PATTERN.search(line):
                _fail(case_id, f"locator {locator} line has no number token")
            if not any(
                f.line_number == line_no for f in numbers
            ):
                _fail(case_id, f"{NWS} not detectable at {locator}")
        elif defect["finding_type"] == STALE:
            if ledger.get_claim(locator) is None:
                _fail(case_id, f"stale locator {locator} not in ledger")
            published = date.fromisoformat(
                str(ledger.get_claim(locator).metadata["published_at"])
            )
            age = (date.fromisoformat(report_date) - published).days
            if age <= WINDOW_DAYS:
                _fail(case_id, f"stale claim {locator} is inside the window")
            if not any(f.related_claim_id == locator for f in stales):
                _fail(case_id, f"{STALE} not detectable at {locator}")
        elif defect["finding_type"] == TPC:
            if ledger.get_claim(locator) is None:
                _fail(case_id, f"tpc locator {locator} not in ledger")
            hits = [f for f in tpc_findings if f.get("claim_id") == locator]
            if len(hits) != 1:
                _fail(case_id, f"{TPC} not detectable at {locator}")
            if locator in _summary_refs(brief):
                _fail(case_id, f"tpc claim {locator} is cited in the summary")
        elif defect["finding_type"] == CSM:
            if ledger.get_claim(locator) is None:
                _fail(case_id, f"csm locator {locator} not in ledger")
            rows_for_claim = [
                row for row in matrix if row["claim_id"] == locator
            ]
            if len(rows_for_claim) != 1:
                _fail(case_id, f"csm claim {locator} lacks its matrix record")
            row = rows_for_claim[0]
            if row["support_label"] not in BLOCKING_SUPPORT_LABELS:
                _fail(case_id, "csm row is not a blocking support label")
            if row["required_action"] != "block_release":
                _fail(case_id, "csm row does not block release")
            if f"[src:{locator}]" not in brief:
                _fail(case_id, f"csm claim {locator} is not used in the brief")
        else:  # pragma: no cover - vocabulary is closed
            _fail(case_id, f"unknown seeded type {defect['finding_type']}")

    # -- no collateral detection noise --------------------------------------
    if NWS not in kinds and numbers:
        _fail(case_id, "unexpected number_without_source findings")
    if STALE not in kinds and stales:
        _fail(case_id, "unexpected stale_source findings")
    if TPC not in kinds and tpc_findings:
        _fail(case_id, "unexpected target-priority findings")
    if gap_findings:
        _fail(case_id, "summary lost target visibility (collateral gap)")
    other = [
        f
        for f in audit.findings
        if f.finding_type
        not in {"number_without_source", "stale_source"}
    ]
    if other:
        _fail(
            case_id,
            f"clean material produced other findings: "
            f"{[f.finding_type for f in other]}",
        )

    # -- clean-claim construction guarantee ---------------------------------
    blocking_rows = {
        row["claim_id"]
        for row in matrix
        if row["support_label"] in BLOCKING_SUPPORT_LABELS
    }
    summary_refs = _summary_refs(brief)
    day = date.fromisoformat(report_date)
    for claim_id in payload["clean_claims"]:
        claim = ledger.get_claim(claim_id)
        if claim is None:
            _fail(case_id, f"clean claim {claim_id} missing from ledger")
        if claim.claim_id in blocking_rows:
            _fail(case_id, f"clean claim {claim_id} has a blocking row")
        if f"[src:{claim_id}]" not in brief:
            _fail(case_id, f"clean claim {claim_id} not cited in brief")
        published = date.fromisoformat(str(claim.metadata["published_at"]))
        age = (day - published).days
        if not 0 <= age <= WINDOW_DAYS:
            _fail(
                case_id,
                f"clean claim {claim_id} source age {age} outside window",
            )
        if str(claim.metadata.get("importance", "")).lower() in {
            "high",
            "critical",
            "blocking",
            "direct",
        } and claim_id not in summary_refs:
            _fail(
                case_id,
                f"high-priority clean claim {claim_id} missing from summary",
            )

    # -- vocabulary hygiene ---------------------------------------------------
    for defect in payload["seeded_defects"]:
        if defect["finding_type"] not in FINDING_TYPES:
            _fail(case_id, "seeded defect outside the detection vocabulary")


# ---------------------------------------------------------------------------
# Composition check
# ---------------------------------------------------------------------------


def check_composition(payloads: list[dict[str, Any]], specs: list[CaseSpec]) -> None:
    """Assert the full-scale composition targets before anything is written."""
    assert len(payloads) == TOTAL_CASES, len(payloads)
    by_id = {p["case_id"]: p for p in payloads}
    assert len(by_id) == TOTAL_CASES

    for split in SPLITS:
        selected = [
            p for p in payloads if _split_of(p["case_id"], specs) == split
        ]
        assert len(selected) == CASES_PER_SPLIT, (split, len(selected))
        blocking = [p for p in selected if p["seeded_defects"]]
        assert len(blocking) == BLOCKING_PER_SPLIT, (split, len(blocking))
    assert BLOCKING_PER_SPLIT >= MIN_BLOCKING_PER_SPLIT

    blocking_total = sum(1 for p in payloads if p["seeded_defects"])
    ratio = blocking_total / TOTAL_CASES
    assert 0.55 <= ratio <= 0.65, ratio

    counts: dict[str, int] = {}
    for p in payloads:
        for defect in p["seeded_defects"]:
            counts[defect["finding_type"]] = (
                counts.get(defect["finding_type"], 0) + 1
            )
    assert set(counts) <= FINDING_TYPES, set(counts) - FINDING_TYPES
    for finding_type in sorted(FINDING_TYPES):
        assert counts.get(finding_type, 0) >= 4, finding_type
        assert counts.get(finding_type, 0) == CASES_PER_BLOCKING_TYPE

    legacy_ids = {
        f"{split}_legacy_{name}" for split, name in LEGACY_CLEAN_SPECS
    } | {
        f"{split}_legacy_{name}"
        for split, name, _kind, _plot in LEGACY_DEFECT_SPECS
    }
    assert legacy_ids <= set(by_id), legacy_ids - set(by_id)
    assert len(legacy_ids) == 14
    for payload in payloads:
        if "_legacy_" in payload["case_id"]:
            assert payload["case_id"] in legacy_ids

    # Every case must be warning-free or blocking-detectable; all defect
    # cases here are blocking-level, mirroring the legacy truth.
    for payload in payloads:
        for defect in payload["seeded_defects"]:
            assert defect["expected_blocking_level"] == "blocking"
        if not payload["seeded_defects"]:
            assert payload["clean_claims"]


def _split_of(case_id: str, specs: list[CaseSpec]) -> str:
    return next(spec.split for spec in specs if spec.case_id == case_id)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def case_document(spec: CaseSpec, seq: int) -> tuple[str, str]:
    """Return (relative path, YAML text) for one case at table position seq."""
    payload = build_case_payload(spec, seq)
    verify_payload(payload)
    return (
        f"cases/{spec.case_id}.yaml",
        _dump(payload),
    )


def build_documents() -> list[tuple[str, str, str]]:
    """Build every (case_id, relative path, YAML text) document."""
    specs = build_case_specs()
    payloads: list[dict[str, Any]] = [
        build_case_payload(spec, seq) for seq, spec in enumerate(specs)
    ]
    check_composition(payloads, specs)
    documents: list[tuple[str, str, str]] = []
    for spec, payload in zip(specs, payloads, strict=True):
        verify_payload(payload)
        documents.append(
            (
                spec.case_id,
                f"cases/{spec.case_id}.yaml",
                _dump(payload),
            )
        )
    return documents


MANIFEST_HEADER = """\
# Packaged agent-rollout evaluation corpus manifest.
#
# AUTO-GENERATED by scripts/generate_eval_corpus.py (deterministic; no
# randomness, no network, no model calls).  Do not edit case files or this
# manifest by hand: re-run the generator instead.
#
# Composition: 80 cases (40 train / 40 val; 24 blocking + 16 clean per split,
# global blocking ratio 0.60), 12 cases per detection finding type, 14
# legacy-derived scenarios + 66 new.  See corpus_data/REVIEW.md.
#
# Case files live flat under cases/ (case ids carry their split prefix) so
# the pinned package-data pattern evaluation_v2/corpus_data/cases/*.yaml
# keeps every case wheel-visible.
#
# Entry shape (one per case, paths relative to this manifest and never
# escaping corpus_data/):
#   - case_id: <id>     # must equal the case file's case_id
#     split: train      # one of: train, val
#     path: cases/<id>.yaml
"""


def write_corpus(output_dir: Path) -> list[tuple[str, str, str]]:
    """Write all case files plus the manifest under ``output_dir``."""
    documents = build_documents()
    cases_root = output_dir / "cases"
    if cases_root.exists():
        shutil.rmtree(cases_root)
    entries = []
    for case_id, rel, text in documents:
        target = output_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        # Every case id carries its split as its prefix (asserted by the
        # manifest validation that follows in main()).
        entries.append(
            {
                "case_id": case_id,
                "split": case_id.split("_", 1)[0],
                "path": rel,
            }
        )
    manifest_text = (
        MANIFEST_HEADER
        + _dump({"schema_version": CORPUS_SCHEMA_VERSION, "cases": entries})
    )
    (output_dir / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    return documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Corpus data directory to write (default: the packaged corpus).",
    )
    args = parser.parse_args(argv)

    documents = write_corpus(args.output_dir)
    print(f"generated {len(documents)} case files under {args.output_dir}")

    # Production self-check: load and validate exactly what was written,
    # through the same loader and thresholds the CLI and tests use.
    corpus = load_corpus(args.output_dir / "manifest.yaml")
    validate_corpus(corpus)
    blocking = sum(1 for case in corpus.cases if case.must_block)
    print(
        f"validated: {len(corpus.cases)} cases, {blocking} blocking "
        f"(ratio {blocking / len(corpus.cases):.2f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
