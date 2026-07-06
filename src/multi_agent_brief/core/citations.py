"""Shared helpers for internal claim citation markers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


CLAIM_ID_RE_FRAGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{1,127}"
SRC_REF_PATTERN = re.compile(rf"\[src:({CLAIM_ID_RE_FRAGMENT})\]")
VALID_SRC_REF_PATTERN = re.compile(rf"\[src:{CLAIM_ID_RE_FRAGMENT}\]")
CLAIM_ID_TOKEN_RE = re.compile(rf"^{CLAIM_ID_RE_FRAGMENT}$")

_BRACKETED_SOURCE_MARKER_RE = re.compile(r"\[(src|source)\s*:\s*([^\]]*)\]", re.IGNORECASE)
_BARE_SOURCE_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9_/:?&=#.-])\b(src|source):([^\s\]\[(){}<>]+)",
    re.IGNORECASE,
)
_CLAIM_ID_BOUNDARY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_CLAIM_ID_LEFT_CONTEXT_BLOCKERS = frozenset(":/?#&=.")
_CLAIM_ID_RIGHT_CONTEXT_BLOCKERS = frozenset("/\\:?&#=")
_TRAILING_PROSE_PUNCTUATION = frozenset(".,;:!\"'”’")

InternalCitationKind = Literal["bracketed_source_marker", "bare_source_marker", "bare_claim_id"]
InternalCitationStatus = Literal["resolved", "unresolved", "malformed"]


@dataclass(frozen=True)
class InternalCitationMarker:
    """Parsed internal citation marker or known bare Claim Ledger ID."""

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
    """Parse internal citation markers without hard-coding claim-id families.

    ``valid_claim_ids`` is the authority for whether an extracted token resolves.
    When omitted, explicit source markers are parsed but left unresolved; bare
    claim IDs are only discoverable when a valid ID set is supplied.
    """

    valid_ids = _normalized_claim_id_set(valid_claim_ids)
    markers: list[InternalCitationMarker] = []
    occupied_spans: list[tuple[int, int]] = []

    for match in _BRACKETED_SOURCE_MARKER_RE.finditer(markdown):
        candidate = _normalized_marker_candidate(match.group(2).strip())
        markers.append(
            _marker_from_candidate(
                kind="bracketed_source_marker",
                raw=match.group(0),
                candidate=candidate,
                start=match.start(),
                end=match.end(),
                valid_claim_ids=valid_ids,
            )
        )
        occupied_spans.append((match.start(), match.end()))

    for match in _BARE_SOURCE_MARKER_RE.finditer(markdown):
        candidate, end = _normalized_bare_marker_candidate(
            candidate=match.group(2).strip(),
            end=match.end(),
        )
        if valid_ids is None or candidate not in valid_ids:
            if not _is_bare_citation_candidate(candidate):
                continue
        if _span_overlaps(match.start(), end, occupied_spans):
            continue
        markers.append(
            _marker_from_candidate(
                kind="bare_source_marker",
                raw=markdown[match.start():end],
                candidate=candidate,
                start=match.start(),
                end=end,
                valid_claim_ids=valid_ids,
            )
        )
        occupied_spans.append((match.start(), end))

    if include_bare_claim_ids and valid_ids:
        for start, end, claim_id in _iter_known_claim_id_spans(markdown, valid_ids):
            if _span_overlaps(start, end, occupied_spans):
                continue
            markers.append(
                InternalCitationMarker(
                    kind="bare_claim_id",
                    raw=markdown[start:end],
                    claim_id=claim_id,
                    start=start,
                    end=end,
                    status="resolved",
                )
            )
            occupied_spans.append((start, end))

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
    kind: InternalCitationKind,
    raw: str,
    candidate: str,
    start: int,
    end: int,
    valid_claim_ids: set[str] | None,
) -> InternalCitationMarker:
    if not candidate:
        return InternalCitationMarker(
            kind=kind,
            raw=raw,
            claim_id="",
            start=start,
            end=end,
            status="malformed",
            message="source marker is empty",
        )
    if valid_claim_ids is None:
        return InternalCitationMarker(
            kind=kind,
            raw=raw,
            claim_id=candidate,
            start=start,
            end=end,
            status="unresolved",
            message="claim id has not been resolved against a ledger",
        )
    if candidate in valid_claim_ids:
        return InternalCitationMarker(
            kind=kind,
            raw=raw,
            claim_id=candidate,
            start=start,
            end=end,
            status="resolved",
        )
    return InternalCitationMarker(
        kind=kind,
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


def _normalized_marker_candidate(candidate: str) -> str:
    while candidate and candidate[-1] in _TRAILING_PROSE_PUNCTUATION:
        candidate = candidate[:-1].rstrip()
    return candidate


def _normalized_bare_marker_candidate(*, candidate: str, end: int) -> tuple[str, int]:
    while candidate and candidate[-1] in _TRAILING_PROSE_PUNCTUATION:
        candidate = candidate[:-1].rstrip()
        end -= 1
    return candidate, end


def _iter_known_claim_id_spans(markdown: str, valid_claim_ids: set[str]):
    for claim_id in sorted(valid_claim_ids, key=len, reverse=True):
        if not _is_bare_citation_candidate(claim_id):
            continue
        start = 0
        while True:
            index = markdown.find(claim_id, start)
            if index < 0:
                break
            end = index + len(claim_id)
            if _has_citation_token_boundaries(markdown, index, end):
                yield index, end, claim_id
            start = index + 1


def _is_bare_citation_candidate(candidate: str) -> bool:
    return (
        bool(CLAIM_ID_TOKEN_RE.fullmatch(candidate))
        and any(ch.isdigit() or ch in "_-" for ch in candidate)
    )


def _has_citation_token_boundaries(markdown: str, start: int, end: int) -> bool:
    before = markdown[start - 1] if start > 0 else ""
    after = markdown[end] if end < len(markdown) else ""
    after_next = markdown[end + 1] if after == "." and end + 1 < len(markdown) else ""
    return (
        before not in _CLAIM_ID_BOUNDARY_CHARS
        and before not in _CLAIM_ID_LEFT_CONTEXT_BLOCKERS
        and after not in _CLAIM_ID_BOUNDARY_CHARS
        and after not in _CLAIM_ID_RIGHT_CONTEXT_BLOCKERS
        and not (after == "." and after_next and not after_next.isspace())
    )


def _span_overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)
