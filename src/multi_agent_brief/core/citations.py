"""Shared helpers for strict BriefLoop internal citation markers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


CLAIM_ID_RE_FRAGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{1,127}"
SRC_REF_PATTERN = re.compile(rf"\[src:({CLAIM_ID_RE_FRAGMENT})\]")
VALID_SRC_REF_PATTERN = re.compile(rf"\[src:{CLAIM_ID_RE_FRAGMENT}\]")
CLAIM_ID_TOKEN_RE = re.compile(rf"^{CLAIM_ID_RE_FRAGMENT}$")

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



def extract_src_ref_ids(markdown: str) -> list[str]:
    return SRC_REF_PATTERN.findall(markdown)


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
            claim_id=markdown[candidate_start:malformed_end].strip(),
            start=marker_start,
            end=malformed_end,
            status="malformed",
            message="source marker is missing a closing bracket",
        )
    candidate = markdown[candidate_start:close].strip()
    raw = markdown[marker_start : close + 1]
    return _marker_from_candidate(
        raw=raw,
        candidate=candidate,
        start=marker_start,
        end=close + 1,
        valid_claim_ids=valid_claim_ids,
    )
