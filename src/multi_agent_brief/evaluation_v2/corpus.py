"""Packaged corpus loading and the invariants that keep R meaningful.

A 40-case validation split moves 2.5 points per flipped case.  Thinner
splits, thinner per-split blocking floors, or thinner per-finding-type
coverage make R noise rather than signal, so these invariants are enforced,
not documented.  No invariant here depends on agent performance: corpus
composition is fixed before any rollout runs, which is what makes a
before/after reward comparison an experiment rather than a story.

Corpus data lives at ``multi_agent_brief/evaluation_v2/corpus_data/`` so it
is packaged data, wheel-visible through the same ``package-data`` mechanism
as ``evaluation_cases/fixtures/``.  The manifest references one YAML file
per case, relative to the manifest itself, and every case file is validated
through the strict ``EvaluationCase`` contract.

Anchor trap (verified): ``corpus_data`` is a plain data directory, not a
package, and because a ``corpus`` MODULE exists next to it,
``importlib.resources.files("multi_agent_brief.evaluation_v2.corpus_data")``
can silently resolve to the parent directory instead of erroring.  The
default corpus therefore anchors through the PARENT package and joins
``corpus_data/manifest.yaml`` by name; a dedicated test asserts the resolved
manifest really exists.

Invariant tiers:

* Unconditional, case-level (``load_corpus`` always enforces them; no flag,
  no threshold can switch them off): locators must be real locations, never
  the porting placeholder ``TO_BE_ANNOTATED`` (a placeholder locator would
  silently cap recall because the double-match rule can never hit it); a
  case whose derived ``must_block`` is False must carry at least one
  ``clean_claim`` (a case contributing to neither recall nor true-negative
  rate must not take a corpus slot); case ids must be unique across the
  corpus.
* Aggregate, full-scale (``validate_corpus`` with ``DEFAULT_THRESHOLDS``):
  at least 80 cases, at least 40 per split, at least 16 blocking-level cases
  per split, a global blocking ratio in [0.55, 0.65], and at least 4 cases
  per finding type across all 4 FINDING_TYPES members.  Unit-test and
  scaffolding corpora may lower these explicitly through
  ``CorpusThresholds``; overriding is never implicit, never environment
  driven, and never applies to the per-split blocking floor, which is a
  fixed constant protecting the val recall denominator.
* Structural, auditor-locator (``validate_corpus`` always enforces them;
  threshold overrides cannot switch them off): at most one seeded defect
  per finding_type per case (anchor-level locators cannot disambiguate
  same-type siblings, so a double hit could never be attributed honestly);
  seeded-defect locators must be claim-anchored (``CL-...``) or
  seeded-brief-anchored (``audited_brief#L<n>``), the two anchor classes
  the auditor's reporting contract can express; clean claims must be
  claim-anchored (clean claims are claim-scoped by construction).  Anything
  else is a corpus error, because it names a position the measured
  auditor can never anchor a finding on.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from multi_agent_brief.evaluation_v2.contracts import (
    FINDING_TYPES,
    EvaluationCase,
)

CORPUS_SCHEMA_VERSION: Final = "briefloop-evaluation-corpus/v1"

SPLITS: Final = ("train", "val")

MIN_TOTAL_CASES: Final = 80
MIN_SPLIT_CASES: Final = 40
MIN_BLOCK_RATIO: Final = 0.55
MAX_BLOCK_RATIO: Final = 0.65
MIN_CASES_PER_FINDING_TYPE: Final = 4

#: Fixed, deliberately NOT a ``CorpusThresholds`` field: the per-split
#: blocking floor protects the val recall denominator and must not drift
#: down when tests or scaffolding lower the scalable thresholds.
MIN_BLOCKING_PER_SPLIT: Final = 16

#: Placeholder locator used while porting the retired annotated corpus.
#: A case shipped with one can never score a recall hit, so it is a hard
#: load-time error rather than a warning.
PLACEHOLDER_LOCATOR: Final = "TO_BE_ANNOTATED"

#: Auditor locator forms (``validate_corpus``).  A seeded defect is anchored
#: either on a claim ledger id or on a line of the seeded audited brief --
#: the two anchor classes the auditor's finding-reporting contract carries
#: (``related_claim_id`` or ``line_number`` into audited_brief.md); a clean
#: claim is claim-scoped by construction.  ``[1-9][0-9]*`` rejects leading
#: zeros so one position has exactly one spelling.
CLAIM_LOCATOR_PATTERN: Final = re.compile(r"^CL-[A-Za-z0-9._:-]+$")
BRIEF_LINE_LOCATOR_PATTERN: Final = re.compile(r"^audited_brief#L[1-9][0-9]*$")

_MANIFEST_KEYS: Final = frozenset({"schema_version", "cases"})
_MANIFEST_ENTRY_KEYS: Final = frozenset({"case_id", "split", "path"})


class CorpusError(Exception):
    """Raised when a corpus cannot be loaded or violates an invariant."""


@dataclass(frozen=True)
class CorpusThresholds:
    """Full-scale composition thresholds.

    These are the production defaults.  Overriding them is for unit tests
    and corpus scaffolding ONLY: a small corpus must say so explicitly by
    passing ``thresholds=CorpusThresholds(...)`` rather than getting a
    silent free pass, and the per-split blocking floor
    (``MIN_BLOCKING_PER_SPLIT``) is not overridable at all.
    """

    min_total_cases: int = MIN_TOTAL_CASES
    min_split_cases: int = MIN_SPLIT_CASES
    min_block_ratio: float = MIN_BLOCK_RATIO
    max_block_ratio: float = MAX_BLOCK_RATIO
    min_cases_per_finding_type: int = MIN_CASES_PER_FINDING_TYPE

    def __post_init__(self) -> None:
        if self.min_total_cases < 1:
            raise ValueError("min_total_cases must be at least 1")
        if self.min_split_cases < 0:
            raise ValueError("min_split_cases must be non-negative")
        if not 0.0 <= self.min_block_ratio <= self.max_block_ratio <= 1.0:
            raise ValueError(
                "block ratio bounds must satisfy 0 <= min <= max <= 1"
            )
        if self.min_cases_per_finding_type < 0:
            raise ValueError("min_cases_per_finding_type must be non-negative")


DEFAULT_THRESHOLDS: Final = CorpusThresholds()


@dataclass(frozen=True)
class Corpus:
    """A loaded corpus plus its split assignment.

    ``splits`` maps ``case_id`` to one of ``SPLITS``; the mapping and the
    case tuple are checked for consistency at construction so that even a
    hand-built ``Corpus`` cannot disagree with itself.
    """

    cases: tuple[EvaluationCase, ...]
    splits: dict[str, str]
    manifest_path: Path | None = None

    def __post_init__(self) -> None:
        case_ids = {case.case_id for case in self.cases}
        if case_ids != set(self.splits):
            raise CorpusError(
                "Corpus cases and splits mapping disagree on case ids"
            )

    def split_of(self, case_id: str) -> str:
        """Return the split a case belongs to."""
        try:
            return self.splits[case_id]
        except KeyError:
            raise CorpusError(f"unknown case_id {case_id!r}") from None

    def select(self, split: str) -> tuple[EvaluationCase, ...]:
        """Return every case assigned to ``split``, in corpus order."""
        if split not in SPLITS:
            raise CorpusError(
                f"unknown split {split!r}; expected one of {SPLITS}"
            )
        return tuple(
            case for case in self.cases if self.splits[case.case_id] == split
        )


def default_corpus_manifest() -> Path:
    """Resolve the packaged corpus manifest through the PARENT package.

    Never use ``files("multi_agent_brief.evaluation_v2.corpus_data")``:
    ``corpus_data`` is a data directory, not a package, and with a
    ``corpus`` module present next to it, ``files()`` on such a dotted path
    can silently resolve to the parent directory instead of erroring.
    Joining ``corpus_data``/``manifest.yaml`` onto the parent package anchor
    keeps resolution honest; ``test_default_corpus_anchor...`` pins it.
    """
    resource = files("multi_agent_brief.evaluation_v2").joinpath(
        "corpus_data", "manifest.yaml"
    )
    try:
        return Path(resource)
    except TypeError:
        raise CorpusError(
            "packaged corpus manifest is not filesystem-backed"
        ) from None


#: Anchor of the packaged default corpus.  Tests assert this really exists.
DEFAULT_CORPUS: Final[Path] = default_corpus_manifest()


def load_default_corpus() -> Corpus:
    """Load the corpus packaged under ``evaluation_v2/corpus_data``."""
    return load_corpus(DEFAULT_CORPUS)


def load_corpus(manifest_path: Path | str) -> Corpus:
    """Load and strictly parse a corpus manifest plus its case files.

    The manifest carries ``schema_version`` and a ``cases`` list whose
    entries reference one case YAML file each; case payloads are validated
    through ``EvaluationCase`` (strict, no undeclared fields).  The
    unconditional case-level invariants run here, so no caller -- test,
    scaffolding, or production -- can load a corpus that violates them.
    """
    manifest_path = Path(manifest_path)
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(
            f"cannot read corpus manifest {manifest_path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise CorpusError(
            f"corpus manifest {manifest_path} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise CorpusError(
            f"corpus manifest {manifest_path} must be a mapping"
        )
    unexpected = sorted(set(raw) - _MANIFEST_KEYS)
    missing = sorted(_MANIFEST_KEYS - set(raw))
    if unexpected or missing:
        raise CorpusError(
            f"corpus manifest {manifest_path} keys invalid "
            f"(missing={missing}, unexpected={unexpected})"
        )
    version = raw["schema_version"]
    if version != CORPUS_SCHEMA_VERSION:
        raise CorpusError(
            f"corpus manifest {manifest_path} has unsupported "
            f"schema_version {version!r}; expected {CORPUS_SCHEMA_VERSION!r}"
        )
    entries = raw["cases"]
    if not isinstance(entries, list):
        raise CorpusError("corpus manifest 'cases' must be a list")

    root = manifest_path.resolve().parent
    cases: list[EvaluationCase] = []
    splits: dict[str, str] = {}
    for entry in entries:
        case, split = _load_case_entry(entry, root)
        if case.case_id in splits:
            raise CorpusError(
                f"duplicate case_id {case.case_id!r} in corpus manifest"
            )
        splits[case.case_id] = split
        cases.append(case)

    return Corpus(cases=tuple(cases), splits=splits, manifest_path=manifest_path)


def validate_corpus(
    corpus: Corpus,
    *,
    thresholds: CorpusThresholds = DEFAULT_THRESHOLDS,
) -> None:
    """Enforce the full-scale invariants that keep R a signal, not noise.

    Uses ``DEFAULT_THRESHOLDS`` (the production floor) unless a test or the
    corpus scaffolding explicitly passes smaller thresholds.  The per-split
    blocking floor is a fixed constant and applies even under override.
    """
    present = set(corpus.splits.values())
    if present != set(SPLITS):
        raise CorpusError(
            f"corpus splits must be exactly {list(SPLITS)}, "
            f"found {sorted(present)}"
        )

    total = len(corpus.cases)
    if total < thresholds.min_total_cases:
        raise CorpusError(
            f"corpus needs at least {thresholds.min_total_cases} cases, "
            f"found {total}"
        )

    for split in SPLITS:
        selected = corpus.select(split)
        if len(selected) < thresholds.min_split_cases:
            raise CorpusError(
                f"split {split!r} needs at least "
                f"{thresholds.min_split_cases} cases, found {len(selected)}"
            )
        blocking = sum(1 for case in selected if case.must_block)
        if blocking < MIN_BLOCKING_PER_SPLIT:
            raise CorpusError(
                f"split {split!r} needs at least {MIN_BLOCKING_PER_SPLIT} "
                f"blocking-level cases (derived must_block), found {blocking}"
            )

    blocking_total = sum(1 for case in corpus.cases if case.must_block)
    ratio = blocking_total / total
    if not thresholds.min_block_ratio <= ratio <= thresholds.max_block_ratio:
        raise CorpusError(
            f"must_block ratio {ratio:.2f} outside "
            f"[{thresholds.min_block_ratio}, {thresholds.max_block_ratio}]"
        )

    counts = Counter(
        defect.finding_type
        for case in corpus.cases
        for defect in case.seeded_defects
    )
    thin = sorted(
        finding_type
        for finding_type in FINDING_TYPES
        if counts.get(finding_type, 0) < thresholds.min_cases_per_finding_type
    )
    if thin:
        raise CorpusError(
            f"finding types with fewer than "
            f"{thresholds.min_cases_per_finding_type} cases: {', '.join(thin)}"
        )

    for case in corpus.cases:
        _enforce_structural_invariants(case)


def _enforce_structural_invariants(case: EvaluationCase) -> None:
    """Structural auditor-locator invariants; no threshold can disable them.

    Enforced in ``validate_corpus`` (not at load time) so scaffolding can
    load and inspect in-progress corpora, while no corpus can be VALIDATED
    with locators the measured auditor can never anchor a finding on.
    """
    seen_types: set[str] = set()
    for defect in case.seeded_defects:
        if defect.finding_type in seen_types:
            raise CorpusError(
                f"case {case.case_id!r} seeds finding_type "
                f"{defect.finding_type!r} more than once: anchor-level "
                "locators cannot disambiguate same-type siblings"
            )
        seen_types.add(defect.finding_type)
        if not (
            CLAIM_LOCATOR_PATTERN.match(defect.locator)
            or BRIEF_LINE_LOCATOR_PATTERN.match(defect.locator)
        ):
            raise CorpusError(
                f"case {case.case_id!r} defect {defect.defect_id!r} locator "
                f"{defect.locator!r} is neither claim-anchored "
                f"({CLAIM_LOCATOR_PATTERN.pattern}) nor seeded-brief-anchored "
                f"({BRIEF_LINE_LOCATOR_PATTERN.pattern})"
            )
    for claim in case.clean_claims:
        if not CLAIM_LOCATOR_PATTERN.match(claim):
            raise CorpusError(
                f"case {case.case_id!r} clean claim {claim!r} is not "
                f"claim-anchored ({CLAIM_LOCATOR_PATTERN.pattern}); clean "
                "claims are claim-scoped by construction"
            )


def _load_case_entry(entry: object, root: Path) -> tuple[EvaluationCase, str]:
    """Load one manifest entry: ``case_id``, ``split``, relative ``path``."""
    if not isinstance(entry, dict):
        raise CorpusError(
            "manifest case entry must be a mapping, found "
            f"{type(entry).__name__}"
        )
    missing = sorted(_MANIFEST_ENTRY_KEYS - set(entry))
    unexpected = sorted(set(entry) - _MANIFEST_ENTRY_KEYS)
    if missing or unexpected:
        raise CorpusError(
            f"manifest case entry keys invalid "
            f"(missing={missing}, unexpected={unexpected})"
        )

    split = entry["split"]
    if split not in SPLITS:
        raise CorpusError(
            f"manifest case entry has invalid split {split!r}; "
            f"expected one of {SPLITS}"
        )

    rel = entry["path"]
    if not isinstance(rel, str) or not rel:
        raise CorpusError(
            "manifest case entry 'path' must be a non-empty relative string"
        )
    case_path = _resolve_case_path(root, rel)

    try:
        payload = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(f"cannot read case file {rel!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CorpusError(f"case file {rel!r} is not valid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpusError(f"case file {rel!r} must be a mapping")

    try:
        case = EvaluationCase.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise CorpusError(f"case file {rel!r} failed validation: {exc}") from exc

    if entry["case_id"] != case.case_id:
        raise CorpusError(
            f"manifest case_id {entry['case_id']!r} does not match case file "
            f"{rel!r} case_id {case.case_id!r}"
        )

    _enforce_case_invariants(case, rel)
    return case, split


def _resolve_case_path(root: Path, rel: str) -> Path:
    """Resolve a manifest-relative case path, keeping it inside the root."""
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CorpusError(
            f"case path {rel!r} must be relative and stay inside the corpus "
            "manifest directory"
        )
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise CorpusError(
            f"case path {rel!r} escapes the corpus manifest directory"
        )
    return resolved


def _enforce_case_invariants(case: EvaluationCase, origin: str) -> None:
    """Case-level invariants that no flag or threshold can disable."""
    for locator in [defect.locator for defect in case.seeded_defects] + list(
        case.clean_claims
    ):
        if locator == PLACEHOLDER_LOCATOR:
            raise CorpusError(
                f"case {case.case_id!r} ({origin}) carries placeholder "
                f"locator {PLACEHOLDER_LOCATOR!r}; locators must be real "
                "locations or recall is silently capped"
            )
    if not case.must_block and not case.clean_claims:
        raise CorpusError(
            f"case {case.case_id!r} ({origin}) contributes to neither recall "
            "nor true-negative rate: non-blocking cases must carry at least "
            "one clean_claim"
        )


__all__ = [
    "BRIEF_LINE_LOCATOR_PATTERN",
    "CLAIM_LOCATOR_PATTERN",
    "CORPUS_SCHEMA_VERSION",
    "DEFAULT_CORPUS",
    "DEFAULT_THRESHOLDS",
    "MAX_BLOCK_RATIO",
    "MIN_BLOCKING_PER_SPLIT",
    "MIN_BLOCK_RATIO",
    "MIN_CASES_PER_FINDING_TYPE",
    "MIN_SPLIT_CASES",
    "MIN_TOTAL_CASES",
    "PLACEHOLDER_LOCATOR",
    "SPLITS",
    "Corpus",
    "CorpusError",
    "CorpusThresholds",
    "default_corpus_manifest",
    "load_corpus",
    "load_default_corpus",
    "validate_corpus",
]
