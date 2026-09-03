"""Guard tests for the legacy generator specs under corpus_data/specs/.

The 16 ``legacy_*.yaml`` files are generator SPEC INPUTS distilled from the
read-only legacy fixture manifest
(``evaluation_cases/fixtures/manifest.yaml``): scenario narratives plus
defect/blocking truth.  They deliberately carry no locators -- locator ground
truth is constructed by the Phase-2 corpus generator, not ported (see
``corpus_data/REVIEW.md``).  These tests freeze the classification verified
in that review: exactly 16 files, 7 blocking / 1 warning-only / 8 no-defect,
roles inferred from the legacy gate stages, and specs kept out of runtime
package data.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

import yaml

from multi_agent_brief.evaluation_v2.contracts import FINDING_TYPES

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DATA = ROOT / "src" / "multi_agent_brief" / "evaluation_v2" / "corpus_data"
SPECS_DIR = CORPUS_DATA / "specs"
REVIEW = CORPUS_DATA / "REVIEW.md"
LEGACY_MANIFEST = (
    ROOT / "src" / "multi_agent_brief" / "evaluation_cases" / "fixtures" / "manifest.yaml"
)

SPEC_KEYS = frozenset(
    {
        "spec_id",
        "origin",
        "scenario",
        "defect_plot",
        "rollout_role",
        "seeded_defects",
        "must_not_report",
        "clean_claim_guidance",
    }
)
DEFECT_KEYS = frozenset({"defect_id", "finding_type", "expected_blocking_level"})

#: Finding types whose legacy gate_stage_id is `auditor` (see REVIEW.md).
AUDITOR_DEFECT_TYPES = frozenset(
    {
        "claim_support_matrix_blocking_support",
        "number_without_source",
        "stale_source",
        "target_priority_claim_missing_from_summary",
    }
)

#: Roles documented per case in REVIEW.md for the 8 no-defect specs.
NO_DEFECT_ROLES = {
    "legacy_provenance_projection_minimal": "auditor",
    "legacy_reader_facing_source_appendix": "editor",
    "legacy_reader_clean_failed_no_delivery_promotion": "editor",
    "legacy_static_hermes_no_skip_finalize": "editor",
    "legacy_release_readiness_forged_event_blocker": "editor",
    "legacy_unauthorized_institution_branding_blocks_release": "editor",
    "legacy_formal_release_missing_human_approval": "editor",
    "legacy_same_evidence_reader_quality_regression": "editor",
}


def _load_specs() -> list[tuple[str, dict]]:
    paths = sorted(SPECS_DIR.glob("legacy_*.yaml"))
    loaded = []
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{path.name} must be a YAML mapping"
        loaded.append((path.stem, payload))
    return loaded


def _levels(spec: dict) -> set[str]:
    return {defect["expected_blocking_level"] for defect in spec["seeded_defects"]}


# ---------------------------------------------------------------------------
# Inventory and shape
# ---------------------------------------------------------------------------


def test_exactly_sixteen_legacy_specs_and_review_exist():
    specs = _load_specs()
    assert len(specs) == 16
    assert REVIEW.is_file()


def test_spec_ids_match_filenames_and_declare_legacy_origin():
    for stem, spec in _load_specs():
        assert set(spec) == SPEC_KEYS, f"{stem} has unexpected/missing keys"
        assert spec["spec_id"] == stem
        case_id = stem.removeprefix("legacy_")
        assert spec["origin"] == f"ported from read-only legacy fixture {case_id}"


def test_specs_carry_no_locators():
    """Locator truth is constructed by the generator, never ported."""
    for stem, spec in _load_specs():
        assert "locator" not in spec
        for defect in spec["seeded_defects"]:
            assert "locator" not in defect, f"{stem} carries a locator"


def test_seeded_defects_and_must_not_report_use_canonical_vocabulary():
    for stem, spec in _load_specs():
        defect_ids = []
        for defect in spec["seeded_defects"]:
            assert set(defect) == DEFECT_KEYS, f"{stem} defect keys drift"
            assert defect["finding_type"] in FINDING_TYPES
            assert defect["expected_blocking_level"] in {"blocking", "warning"}
            defect_ids.append(defect["defect_id"])
        assert len(defect_ids) == len(set(defect_ids)), f"{stem} defect ids collide"
        assert isinstance(spec["must_not_report"], list)
        for finding_type in spec["must_not_report"]:
            assert finding_type in FINDING_TYPES


def test_narrative_fields_non_empty_where_required():
    for stem, spec in _load_specs():
        assert isinstance(spec["scenario"], str) and spec["scenario"].strip()
        assert (
            isinstance(spec["clean_claim_guidance"], str)
            and spec["clean_claim_guidance"].strip()
        )
        if spec["seeded_defects"]:
            # defect_plot tells the generator HOW to embed the defect(s)
            assert isinstance(spec["defect_plot"], str) and spec["defect_plot"].strip(), (
                f"{stem} seeds defects but has no defect_plot"
            )
        else:
            # defect_plot may be empty only for no-defect specs
            assert isinstance(spec["defect_plot"], str)


# ---------------------------------------------------------------------------
# Classification (verified against the legacy fixture data; see REVIEW.md)
# ---------------------------------------------------------------------------


def test_classification_counts_are_seven_blocking_one_warning_eight_clean():
    blocking = warning_only = empty = 0
    for _stem, spec in _load_specs():
        levels = _levels(spec)
        if not levels:
            empty += 1
        elif levels == {"blocking"}:
            blocking += 1
        elif levels == {"warning"}:
            warning_only += 1
        else:
            raise AssertionError(f"mixed blocking/warning levels in {_stem}: {levels}")
    assert (blocking, warning_only, empty) == (7, 1, 8)


def test_warning_only_spec_is_final_abstract_quality_warning_surface():
    warning_only = [stem for stem, spec in _load_specs() if _levels(spec) == {"warning"}]
    assert warning_only == ["legacy_final_abstract_quality_warning_surface"]
    spec = dict(_load_specs())["legacy_final_abstract_quality_warning_surface"]
    assert {d["finding_type"] for d in spec["seeded_defects"]} == {
        "final_scope_title_mismatch",
        "final_missing_comparison_basis",
        "final_missing_limitation_section",
        "final_incomplete_key_case_fields",
        "final_unsupported_superlative",
    }


def test_blocking_specs_match_the_fixture_truth():
    legacy = yaml.safe_load(LEGACY_MANIFEST.read_text(encoding="utf-8"))
    legacy_blocking: dict[str, set[str]] = {}
    for entry in legacy["cases"]:
        findings = (entry.get("expected", {}) or {}).get("findings_any") or []
        blocking_types = {
            f["finding_type"] for f in findings if f.get("blocking_level") == "blocking"
        }
        if blocking_types:
            legacy_blocking[f"legacy_{entry['case_id']}"] = blocking_types

    ported_blocking = {
        stem: {d["finding_type"] for d in spec["seeded_defects"]}
        for stem, spec in _load_specs()
        if _levels(spec) == {"blocking"}
    }
    assert ported_blocking == legacy_blocking


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def test_roles_follow_the_gate_stage_rule():
    for stem, spec in _load_specs():
        assert spec["rollout_role"] in {"auditor", "editor"}
        types = {d["finding_type"] for d in spec["seeded_defects"]}
        if types:
            expected = (
                "auditor" if types & AUDITOR_DEFECT_TYPES else "editor"
            )
            assert spec["rollout_role"] == expected, f"{stem} role drift"
        else:
            assert spec["rollout_role"] == NO_DEFECT_ROLES[stem], f"{stem} role drift"


# ---------------------------------------------------------------------------
# Packaging: specs are build-time inputs, never runtime package data
# ---------------------------------------------------------------------------


def test_specs_stay_out_of_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["multi_agent_brief"]
    assert not [p for p in patterns if "specs" in p], (
        "corpus_data/specs/ are generator inputs and must stay out of package-data"
    )
