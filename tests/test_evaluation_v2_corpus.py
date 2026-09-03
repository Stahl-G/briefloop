"""Corpus loading and the invariants that keep R meaningful.

The corpus is packaged data anchored under
``multi_agent_brief/evaluation_v2/corpus_data``; the anchor test below pins
the parent-package resolution because ``corpus_data`` is a directory, not a
package.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import tomllib

import pytest
import yaml

from multi_agent_brief.evaluation_v2.contracts import (
    FINDING_TYPES,
    EvaluationCase,
)
from multi_agent_brief.evaluation_v2.corpus import (
    CORPUS_SCHEMA_VERSION,
    DEFAULT_CORPUS,
    DEFAULT_THRESHOLDS,
    MIN_BLOCKING_PER_SPLIT,
    SPLITS,
    Corpus,
    CorpusError,
    CorpusThresholds,
    default_corpus_manifest,
    load_corpus,
    load_default_corpus,
    validate_corpus,
)

ROOT = Path(__file__).resolve().parents[1]
ORDERED_FINDING_TYPES = sorted(FINDING_TYPES)


def _defect(
    defect_id: str = "d1",
    finding_type: str = "stale_source",
    locator: str = "source-002.md#L14",
    expected_blocking_level: str = "blocking",
) -> dict:
    return {
        "defect_id": defect_id,
        "finding_type": finding_type,
        "locator": locator,
        "expected_blocking_level": expected_blocking_level,
    }


def _payload(
    case_id: str,
    defects: tuple[dict, ...] | list[dict] = (),
    clean_claims: tuple[str, ...] | list[str] = ("source-001.md#L8",),
) -> dict:
    return {
        "case_id": case_id,
        "synthetic": True,
        "source_pack": f"cases/{case_id}/sources",
        "report_date": "2026-06-08",
        "rollout": {"role": "auditor", "runtime": "codex"},
        "seeded_defects": list(defects),
        "clean_claims": list(clean_claims),
    }


def _blocking_payload(case_id: str, finding_type: str = "stale_source") -> dict:
    return _payload(
        case_id,
        defects=(_defect(finding_type=finding_type),),
        clean_claims=(),
    )


def _clean_payload(case_id: str) -> dict:
    return _payload(case_id, defects=(), clean_claims=("source-001.md#L8",))


def _write_case_file(tmp_path: Path, rel: str, payload: dict) -> None:
    case_file = tmp_path / rel
    case_file.parent.mkdir(parents=True, exist_ok=True)
    case_file.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )


def _write_manifest(
    tmp_path: Path,
    entries: list[dict],
    *,
    schema_version: str = CORPUS_SCHEMA_VERSION,
) -> Path:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"schema_version": schema_version, "cases": entries},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest


def _write_corpus(tmp_path: Path, cases: list[tuple[str, str, dict]]) -> Path:
    """Write case files and a manifest referencing them, one entry each."""
    entries = []
    for case_id, split, payload in cases:
        rel = f"cases/{split}/{case_id}.yaml"
        _write_case_file(tmp_path, rel, payload)
        entries.append({"case_id": case_id, "split": split, "path": rel})
    return _write_manifest(tmp_path, entries)


def _spread(
    split: str,
    n_blocking: int,
    n_clean: int,
    types: list[str] | tuple[str, ...] = ORDERED_FINDING_TYPES,
) -> list[tuple[str, str, dict]]:
    """Build (case_id, split, payload) triples with cycling finding types."""
    cases = []
    for index in range(n_blocking):
        case_id = f"b_{split}_{index}"
        cases.append(
            (
                case_id,
                split,
                _blocking_payload(
                    case_id, finding_type=types[index % len(types)]
                ),
            )
        )
    for index in range(n_clean):
        case_id = f"c_{split}_{index}"
        cases.append((case_id, split, _clean_payload(case_id)))
    return cases


# ---------------------------------------------------------------------------
# Loading round-trip and load-time (unconditional, case-level) invariants
# ---------------------------------------------------------------------------


def test_load_corpus_round_trip_from_manifest(tmp_path):
    manifest = _write_corpus(
        tmp_path,
        [
            ("b_train", "train", _blocking_payload("b_train")),
            ("c_val", "val", _clean_payload("c_val")),
        ],
    )
    corpus = load_corpus(manifest)

    assert [case.case_id for case in corpus.cases] == ["b_train", "c_val"]
    assert isinstance(corpus.cases[0], EvaluationCase)
    assert corpus.cases[0].must_block is True
    assert corpus.cases[1].must_block is False
    assert corpus.split_of("b_train") == "train"
    assert corpus.split_of("c_val") == "val"
    assert tuple(case.case_id for case in corpus.select("train")) == ("b_train",)
    assert tuple(case.case_id for case in corpus.select("val")) == ("c_val",)
    assert corpus.manifest_path == manifest

    with pytest.raises(CorpusError, match="unknown split"):
        corpus.select("holdout")
    with pytest.raises(CorpusError, match="unknown case_id"):
        corpus.split_of("missing")
    with pytest.raises(dataclasses.FrozenInstanceError):
        corpus.cases = ()


def test_load_corpus_rejects_unknown_schema_version(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [],
        schema_version="briefloop-evaluation-corpus/v0",
    )
    with pytest.raises(CorpusError, match="schema_version"):
        load_corpus(manifest)


def test_load_corpus_rejects_malformed_manifests(tmp_path):
    manifest = _write_manifest(tmp_path, [])
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": CORPUS_SCHEMA_VERSION,
                "cases": [],
                "notes": "unexpected manifest-level key",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="unexpected=\\['notes'\\]"):
        load_corpus(manifest)

    manifest = _write_manifest(
        tmp_path,
        [
            {
                "case_id": "b1",
                "split": "train",
                "path": "cases/train/b1.yaml",
                "note": "unexpected entry key",
            }
        ],
    )
    with pytest.raises(CorpusError, match="unexpected=\\['note'\\]"):
        load_corpus(manifest)

    manifest = _write_manifest(tmp_path, ["not-a-mapping"])
    with pytest.raises(CorpusError, match="must be a mapping"):
        load_corpus(manifest)


def test_load_corpus_rejects_duplicate_case_ids(tmp_path):
    entries = []
    for split, filename in (("train", "one"), ("val", "two")):
        rel = f"cases/{split}/{filename}.yaml"
        _write_case_file(tmp_path, rel, _blocking_payload("b1"))
        entries.append({"case_id": "b1", "split": split, "path": rel})
    manifest = _write_manifest(tmp_path, entries)
    with pytest.raises(CorpusError, match="duplicate case_id"):
        load_corpus(manifest)


def test_load_corpus_rejects_unknown_split(tmp_path):
    rel = "cases/holdout/b1.yaml"
    _write_case_file(tmp_path, rel, _blocking_payload("b1"))
    manifest = _write_manifest(
        tmp_path, [{"case_id": "b1", "split": "holdout", "path": rel}]
    )
    with pytest.raises(CorpusError, match="invalid split"):
        load_corpus(manifest)


def test_load_corpus_rejects_placeholder_locators(tmp_path):
    payload = _blocking_payload("b1")
    payload["seeded_defects"][0]["locator"] = "TO_BE_ANNOTATED"
    manifest = _write_corpus(tmp_path, [("b1", "train", payload)])
    with pytest.raises(CorpusError, match="TO_BE_ANNOTATED"):
        load_corpus(manifest)

    payload = _clean_payload("c1")
    payload["clean_claims"] = ["TO_BE_ANNOTATED"]
    manifest = _write_corpus(tmp_path, [("c1", "train", payload)])
    with pytest.raises(CorpusError, match="TO_BE_ANNOTATED"):
        load_corpus(manifest)


def test_load_corpus_rejects_non_blocking_case_without_clean_claims(tmp_path):
    payload = _payload("dead_weight", defects=(), clean_claims=())
    manifest = _write_corpus(tmp_path, [("dead_weight", "train", payload)])
    with pytest.raises(CorpusError, match="clean_claim"):
        load_corpus(manifest)


def test_load_corpus_rejects_manifest_case_id_drift(tmp_path):
    rel = "cases/train/b1.yaml"
    _write_case_file(tmp_path, rel, _blocking_payload("b2"))
    manifest = _write_manifest(
        tmp_path, [{"case_id": "b1", "split": "train", "path": rel}]
    )
    with pytest.raises(CorpusError, match="does not match"):
        load_corpus(manifest)


def test_load_corpus_rejects_case_paths_escaping_the_corpus_root(tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        yaml.safe_dump(_blocking_payload("b1"), sort_keys=False),
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path,
        [{"case_id": "b1", "split": "train", "path": "../outside.yaml"}],
    )
    with pytest.raises(CorpusError, match="escapes|must be relative"):
        load_corpus(manifest)

    manifest = _write_manifest(
        tmp_path,
        [{"case_id": "b1", "split": "train", "path": str(outside)}],
    )
    with pytest.raises(CorpusError, match="escapes|must be relative"):
        load_corpus(manifest)


def test_load_corpus_rejects_missing_case_file(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        [{"case_id": "b1", "split": "train", "path": "cases/train/b1.yaml"}],
    )
    with pytest.raises(CorpusError, match="cannot read case file"):
        load_corpus(manifest)


def test_load_corpus_wraps_case_contract_violations(tmp_path):
    payload = _blocking_payload("b1")
    payload["command"] = "rm -rf /"  # contracts structurally reject shell fields
    manifest = _write_corpus(tmp_path, [("b1", "train", payload)])
    with pytest.raises(CorpusError, match="failed validation"):
        load_corpus(manifest)


def test_corpus_rejects_inconsistent_direct_construction():
    case = EvaluationCase.model_validate(_blocking_payload("b1"), strict=True)
    with pytest.raises(CorpusError, match="disagree"):
        Corpus(cases=(case,), splits={"b1": "train", "ghost": "val"})


# ---------------------------------------------------------------------------
# Packaged anchor and skeleton
# ---------------------------------------------------------------------------


def test_default_corpus_anchor_resolves_to_real_manifest():
    # TRAP: the corpus must never be anchored via
    # files("multi_agent_brief.evaluation_v2.corpus_data").  corpus_data is a
    # plain data directory, not a package, and because a corpus MODULE exists
    # next to it, importlib.resources.files() on that dotted path can
    # silently resolve to the parent directory instead of erroring -- which
    # would quietly point DEFAULT_CORPUS at the wrong tree.  Anchoring
    # through the parent package and asserting the manifest really exists
    # makes a silent mis-anchor impossible to miss.
    assert DEFAULT_CORPUS.name == "manifest.yaml"
    assert DEFAULT_CORPUS.parent.name == "corpus_data"
    assert DEFAULT_CORPUS.parent.parent.name == "evaluation_v2"
    assert DEFAULT_CORPUS.is_file()
    assert default_corpus_manifest() == DEFAULT_CORPUS


def test_packaged_skeleton_loads_but_fails_full_scale_validation():
    corpus = load_default_corpus()
    assert corpus.cases == ()
    assert corpus.splits == {}
    with pytest.raises(CorpusError, match="splits must be exactly"):
        validate_corpus(corpus)


def test_pyproject_packages_corpus_data_explicitly():
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    patterns = set(
        pyproject["tool"]["setuptools"]["package-data"]["multi_agent_brief"]
    )
    corpus_patterns = {
        pattern
        for pattern in patterns
        if pattern.startswith("evaluation_v2/corpus_data")
    }
    # Explicit inclusion list only: manifest, case files, and the corpus
    # review doc.  No blanket corpus_data glob, and the build-time specs/
    # subtree stays out of runtime package data by not being listed.
    assert corpus_patterns == {
        "evaluation_v2/corpus_data/manifest.yaml",
        "evaluation_v2/corpus_data/cases/*.yaml",
        "evaluation_v2/corpus_data/REVIEW.md",
    }


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def test_default_thresholds_match_production_constants():
    assert DEFAULT_THRESHOLDS == CorpusThresholds()
    assert DEFAULT_THRESHOLDS.min_total_cases == 80
    assert DEFAULT_THRESHOLDS.min_split_cases == 40
    assert DEFAULT_THRESHOLDS.min_block_ratio == 0.55
    assert DEFAULT_THRESHOLDS.max_block_ratio == 0.65
    assert DEFAULT_THRESHOLDS.min_cases_per_finding_type == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_THRESHOLDS.min_total_cases = 1


def test_thresholds_reject_nonsense_bounds():
    with pytest.raises(ValueError, match="min_total_cases"):
        CorpusThresholds(min_total_cases=0)
    with pytest.raises(ValueError, match="block ratio"):
        CorpusThresholds(min_block_ratio=0.7, max_block_ratio=0.6)


# ---------------------------------------------------------------------------
# Aggregate, full-scale invariants
# ---------------------------------------------------------------------------


def test_validate_rejects_missing_split(tmp_path):
    manifest = _write_corpus(tmp_path, _spread("train", 48, 32))
    with pytest.raises(CorpusError, match="splits must be exactly"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_small_corpus(tmp_path):
    manifest = _write_corpus(
        tmp_path,
        [
            ("b1", "train", _blocking_payload("b1")),
            ("c1", "val", _clean_payload("c1")),
        ],
    )
    with pytest.raises(CorpusError, match="at least 80 cases"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_thin_split(tmp_path):
    manifest = _write_corpus(
        tmp_path, _spread("train", 47, 32) + _spread("val", 0, 1)
    )
    with pytest.raises(CorpusError, match="split 'val' needs at least 40"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_thin_blocking_floor_per_split(tmp_path):
    manifest = _write_corpus(
        tmp_path, _spread("train", 40, 0) + _spread("val", 10, 30)
    )
    with pytest.raises(CorpusError, match="16 blocking-level cases"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_skewed_block_ratio(tmp_path):
    manifest = _write_corpus(
        tmp_path, _spread("train", 40, 0) + _spread("val", 40, 0)
    )
    with pytest.raises(CorpusError, match="must_block ratio"):
        validate_corpus(load_corpus(manifest))


def test_validate_rejects_thin_finding_type_coverage(tmp_path):
    two_types = ORDERED_FINDING_TYPES[:2]
    manifest = _write_corpus(
        tmp_path,
        _spread("train", 24, 16, types=two_types)
        + _spread("val", 24, 16, types=two_types),
    )
    with pytest.raises(CorpusError, match="fewer than 4 cases"):
        validate_corpus(load_corpus(manifest))


def test_validate_accepts_a_full_scale_corpus(tmp_path):
    manifest = _write_corpus(
        tmp_path, _spread("train", 24, 16) + _spread("val", 24, 16)
    )
    corpus = load_corpus(manifest)
    assert len(corpus.cases) == 80
    validate_corpus(corpus)  # does not raise


def test_validate_threshold_overrides_are_explicit_and_blocking_floor_is_fixed(
    tmp_path,
):
    cases = []
    index = 0
    for split in SPLITS:
        for _ in range(16):
            case_id = f"b_{split}_{index}"
            defects = (
                _defect(
                    defect_id="d1",
                    finding_type=ORDERED_FINDING_TYPES[index % 10],
                ),
                _defect(
                    defect_id="d2",
                    finding_type=ORDERED_FINDING_TYPES[(index + 4) % 10],
                    locator="source-003.md#L7",
                ),
            )
            cases.append(
                (case_id, split, _payload(case_id, defects=defects, clean_claims=()))
            )
            index += 1
        for clean_index in range(9):
            case_id = f"c_{split}_{clean_index}"
            cases.append((case_id, split, _clean_payload(case_id)))
    manifest = _write_corpus(tmp_path, cases)
    corpus = load_corpus(manifest)
    assert len(corpus.cases) == 50

    # Production thresholds reject the half-scale corpus...
    with pytest.raises(CorpusError, match="at least 80 cases"):
        validate_corpus(corpus)

    # ...and only an explicit override accepts it.
    validate_corpus(
        corpus,
        thresholds=CorpusThresholds(min_total_cases=50, min_split_cases=25),
    )

    # The per-split blocking floor is a fixed constant, not a threshold that
    # the override could lower.
    assert MIN_BLOCKING_PER_SPLIT == 16
    assert "min_blocking_per_split" not in CorpusThresholds.__dataclass_fields__
