"""Shared helpers for strict BriefLoop internal citation markers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


CLAIM_ID_RE_FRAGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{1,127}"
SRC_REF_PATTERN = re.compile(rf"\[src:({CLAIM_ID_RE_FRAGMENT})\]")
VALID_SRC_REF_PATTERN = re.compile(rf"\[src:{CLAIM_ID_RE_FRAGMENT}\]")
CLAIM_ID_TOKEN_RE = re.compile(rf"^{CLAIM_ID_RE_FRAGMENT}$")

# These patterns are deliberately owned here.  Consumers must ask this module
# for marker/token spans instead of re-declaring one of the internal ID
# grammars locally.  The residue grammar preserves the historical reader gate
# behavior, including bracketed bare CL/CLM IDs and prefixed CLAIM IDs.
CLAIM_ID_RESIDUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:\[(?:CLM-\d{3,}|CL-\d{3,})\]|"
    r"CLM-\d{3,}|CL-\d{3,}|(?:[A-Z][A-Z0-9]*_)?CLAIM_[A-Z0-9][A-Z0-9_-]*)(?![A-Za-z0-9_])"
)
INTERNAL_ID_RESIDUE_PATTERN = re.compile(
    r"\b(?:SYN_)?(?:CLAIM|SRC|SOURCE|CLM)_[A-Z0-9][A-Z0-9_-]*\b"
)

_SRC_MARKER_OPEN = "[src:"

InternalCitationKind = Literal["src_marker"]
InternalCitationStatus = Literal["resolved", "unresolved", "malformed"]


@dataclass(frozen=True)
class InternalCitationMarker:
    """Parsed canonical internal citation marker.

    BriefLoop v1.0 RC intentionally supports exactly one projectable citation
    syntax: ``[src:<claim_id>]``. Source prose, ``[source:...]``, bare
    ``src:...`` / ``source:...``, and bare claim IDs are not citation markers.
    """

    kind: InternalCitationKind
    raw: str
    claim_id: str
    start: int
    end: int
    status: InternalCitationStatus
    message: str = ""


@dataclass(frozen=True)
class InternalClaimIdResidue:
    """A bare/internal claim-ID token found outside marker parsing."""

    raw: str
    claim_id: str
    start: int
    end: int


@dataclass(frozen=True)
class BracketedSourceMarker:
    """A canonical or legacy bracketed source marker span."""

    raw: str
    start: int
    end: int



def extract_src_ref_ids(markdown: str) -> list[str]:
    return [
        marker.claim_id
        for marker in parse_internal_citation_markers(markdown)
        if marker.status != "malformed" and marker.claim_id
    ]


def iter_claim_id_residue_tokens(markdown: str) -> list[InternalClaimIdResidue]:
    """Return bare/internal claim-ID residue spans in source order."""

    return [
        InternalClaimIdResidue(
            raw=match.group(0),
            claim_id=match.group(0).strip("[]"),
            start=match.start(),
            end=match.end(),
        )
        for match in CLAIM_ID_RESIDUE_PATTERN.finditer(markdown)
    ]


def extract_claim_id_tokens(markdown: str) -> list[str]:
    """Return residue claim IDs in first-appearance order, preserving duplicates."""

    return [token.claim_id for token in iter_claim_id_residue_tokens(markdown)]


def contains_internal_id(value: str) -> bool:
    """Return whether text contains an internal claim/source identifier token."""

    return bool(INTERNAL_ID_RESIDUE_PATTERN.search(value))


def iter_bracketed_source_markers(markdown: str) -> list[BracketedSourceMarker]:
    """Return canonical and legacy bracketed source-marker spans.

    This helper centralizes legacy/case-insensitive residue recognition for
    reader and cleanup consumers.  Canonical status/ledger interpretation
    remains in :func:`parse_internal_citation_markers`.
    """

    spans: list[BracketedSourceMarker] = []
    start = 0
    lowered = markdown.casefold()
    while True:
        marker_start = lowered.find("[", start)
        if marker_start < 0:
            return spans
        cursor = marker_start + 1
        while cursor < len(markdown) and markdown[cursor].isspace():
            cursor += 1
        if not (
            lowered.startswith("src:", cursor)
            or lowered.startswith("source:", cursor)
        ):
            start = marker_start + 1
            continue
        close = markdown.find("]", cursor)
        end = close + 1 if close >= 0 else len(markdown)
        spans.append(
            BracketedSourceMarker(
                raw=markdown[marker_start:end],
                start=marker_start,
                end=end,
            )
        )
        start = end


def remove_src_marker_spans(markdown: str) -> str:
    """Remove lower-case canonical ``[src:...]`` marker spans."""

    markers = parse_internal_citation_markers(markdown)
    if not markers:
        return markdown
    parts: list[str] = []
    cursor = 0
    for marker in markers:
        parts.append(markdown[cursor:marker.start])
        cursor = marker.end
    parts.append(markdown[cursor:])
    return "".join(parts)


def remove_empty_source_marker_residue(markdown: str) -> str:
    """Remove empty canonical/legacy source markers while preserving others."""

    parts: list[str] = []
    cursor = 0
    for marker in iter_bracketed_source_markers(markdown):
        if not marker.raw.endswith("]"):
            continue
        body = marker.raw[marker.raw.find(":") + 1 : -1]
        if body.strip():
            continue
        parts.append(markdown[cursor:marker.start])
        cursor = marker.end
    if cursor == 0:
        return markdown
    parts.append(markdown[cursor:])
    return "".join(parts)


def parse_internal_citation_markers(
    markdown: str,
    *,
    valid_claim_ids: Iterable[str] | None = None,
    include_bare_claim_ids: bool = True,
) -> list[InternalCitationMarker]:
    """Parse canonical ``[src:<claim_id>]`` markers.

    ``valid_claim_ids`` is the authority for whether an extracted token resolves.
    When omitted, syntactically valid markers are parsed but left unresolved.
    ``include_bare_claim_ids`` is retained for call-site compatibility; bare IDs
    are not projectable citations in the v1.0 RC grammar.
    """

    valid_ids = _normalized_claim_id_set(valid_claim_ids)
    markers: list[InternalCitationMarker] = []
    start = 0
    while True:
        marker_start = markdown.find(_SRC_MARKER_OPEN, start)
        if marker_start < 0:
            break
        marker = _parse_src_marker_at(markdown, marker_start, valid_ids)
        markers.append(marker)
        start = max(marker.end, marker_start + len(_SRC_MARKER_OPEN))

    return sorted(markers, key=lambda marker: (marker.start, marker.end))


def resolved_internal_citation_ids(
    markdown: str,
    *,
    valid_claim_ids: Iterable[str],
    include_bare_claim_ids: bool = True,
) -> list[str]:
    """Return resolved internal citation IDs in first-appearance order."""

    seen: set[str] = set()
    resolved: list[str] = []
    for marker in parse_internal_citation_markers(
        markdown,
        valid_claim_ids=valid_claim_ids,
        include_bare_claim_ids=include_bare_claim_ids,
    ):
        if marker.status != "resolved" or marker.claim_id in seen:
            continue
        seen.add(marker.claim_id)
        resolved.append(marker.claim_id)
    return resolved


def unresolved_internal_citation_markers(
    markdown: str,
    *,
    valid_claim_ids: Iterable[str],
) -> list[InternalCitationMarker]:
    return [
        marker
        for marker in parse_internal_citation_markers(
            markdown,
            valid_claim_ids=valid_claim_ids,
        )
        if marker.status != "resolved"
    ]


def _marker_from_candidate(
    *,
    raw: str,
    candidate: str,
    start: int,
    end: int,
    valid_claim_ids: set[str] | None,
) -> InternalCitationMarker:
    if not candidate:
        return InternalCitationMarker(
            kind="src_marker",
            raw=raw,
            claim_id="",
            start=start,
            end=end,
            status="malformed",
            message="source marker is empty",
        )
    if not CLAIM_ID_TOKEN_RE.fullmatch(candidate):
        return InternalCitationMarker(
            kind="src_marker",
            raw=raw,
            claim_id=candidate,
            start=start,
            end=end,
            status="malformed",
            message="source marker claim id is malformed",
        )
    if valid_claim_ids is None:
        return InternalCitationMarker(
            kind="src_marker",
            raw=raw,
            claim_id=candidate,
            start=start,
            end=end,
            status="unresolved",
            message="claim id has not been resolved against a ledger",
        )
    if candidate in valid_claim_ids:
        return InternalCitationMarker(
            kind="src_marker",
            raw=raw,
            claim_id=candidate,
            start=start,
            end=end,
            status="resolved",
        )
    return InternalCitationMarker(
        kind="src_marker",
        raw=raw,
        claim_id=candidate,
        start=start,
        end=end,
        status="unresolved",
        message="source marker does not resolve to a Claim Ledger ID",
    )


def _normalized_claim_id_set(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    return {str(value).strip() for value in values if str(value).strip()}


def _parse_src_marker_at(
    markdown: str,
    marker_start: int,
    valid_claim_ids: set[str] | None,
) -> InternalCitationMarker:
    candidate_start = marker_start + len(_SRC_MARKER_OPEN)
    line_end = markdown.find("\n", candidate_start)
    if line_end < 0:
        line_end = len(markdown)
    next_marker_start = markdown.find(_SRC_MARKER_OPEN, candidate_start)
    close = markdown.find("]", candidate_start)
    malformed_end = min(
        value
        for value in (line_end, next_marker_start if next_marker_start >= 0 else len(markdown))
        if value >= candidate_start
    )
    if close < 0 or close > malformed_end:
        raw = markdown[marker_start:malformed_end]
        return InternalCitationMarker(
            kind="src_marker",
            raw=raw,
            claim_id=markdown[candidate_start:malformed_end],
            start=marker_start,
            end=malformed_end,
            status="malformed",
            message="source marker is missing a closing bracket",
        )
    candidate = markdown[candidate_start:close]
    raw = markdown[marker_start : close + 1]
    return _marker_from_candidate(
        raw=raw,
        candidate=candidate,
        start=marker_start,
        end=close + 1,
        valid_claim_ids=valid_claim_ids,
    )
