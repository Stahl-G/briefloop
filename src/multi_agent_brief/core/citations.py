"""Shared helpers for internal claim citation markers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


CLAIM_ID_RE_FRAGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{1,127}"
SRC_REF_PATTERN = re.compile(rf"\[src:({CLAIM_ID_RE_FRAGMENT})\]")
VALID_SRC_REF_PATTERN = re.compile(rf"\[src:{CLAIM_ID_RE_FRAGMENT}\]")
CLAIM_ID_TOKEN_RE = re.compile(rf"^{CLAIM_ID_RE_FRAGMENT}$")

_BRACKETED_SOURCE_MARKER_RE = re.compile(r"\[(src|source)\s*:\s*([^\]\[\r\n]*)\]", re.IGNORECASE)
_BARE_SOURCE_MARKER_PREFIX_RE = re.compile(r"(?:(?<=_)|\b)(src|source):", re.IGNORECASE)
_CLAIM_ID_BOUNDARY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_CLAIM_ID_LEFT_CONTEXT_BLOCKERS = frozenset(":/?#&=.")
_CLAIM_ID_RIGHT_CONTEXT_BLOCKERS = frozenset("/\\:?&#=")
_TRAILING_PROSE_PUNCTUATION = frozenset(".,;:!\"'”’。．，、；：！？）】》」』")
_MARKDOWN_FORMATTING_DELIMITERS = frozenset("`*~")

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

    for match in _BARE_SOURCE_MARKER_PREFIX_RE.finditer(markdown):
        opening_underscore_count = _opening_markdown_underscore_count(markdown, match.start())
        if not opening_underscore_count and not _has_bare_source_marker_left_boundary(markdown, match.start()):
            continue
        candidate, end = _bare_marker_candidate_at(
            markdown,
            match.end(),
            opening_underscore_count=opening_underscore_count,
        )
        if valid_ids is None or candidate not in valid_ids:
            if not _is_explicit_source_marker_candidate(candidate):
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


def _normalized_bare_marker_candidate(
    *,
    candidate: str,
    end: int,
    opening_underscore_count: int = 0,
) -> tuple[str, int]:
    while candidate:
        if candidate[-1] in _TRAILING_PROSE_PUNCTUATION:
            candidate = candidate[:-1].rstrip()
            end -= 1
            continue
        if (
            opening_underscore_count
            and len(candidate) > opening_underscore_count
            and candidate.endswith("_" * opening_underscore_count)
        ):
            candidate = candidate[:-opening_underscore_count].rstrip()
            end -= opening_underscore_count
            continue
        break
    return candidate, end


def _bare_marker_candidate_at(
    markdown: str,
    start: int,
    *,
    opening_underscore_count: int = 0,
) -> tuple[str, int]:
    end = start
    while end < len(markdown):
        char = markdown[end]
        if char.isspace() or char in "][(){}<>" or char in _MARKDOWN_FORMATTING_DELIMITERS:
            break
        if char in _TRAILING_PROSE_PUNCTUATION and _is_bare_marker_delimiter(markdown, end):
            break
        end += 1
    return _normalized_bare_marker_candidate(
        candidate=markdown[start:end].strip(),
        end=end,
        opening_underscore_count=opening_underscore_count,
    )


def _is_bare_marker_delimiter(markdown: str, index: int) -> bool:
    char = markdown[index]
    if char != ".":
        return True
    next_char = markdown[index + 1] if index + 1 < len(markdown) else ""
    return not next_char or next_char.isspace() or next_char in _TRAILING_PROSE_PUNCTUATION


def _iter_known_claim_id_spans(markdown: str, valid_claim_ids: set[str]):
    for claim_id in sorted(valid_claim_ids, key=len, reverse=True):
        if not _is_free_standing_bare_claim_candidate(claim_id):
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


def _opening_markdown_underscore_count(markdown: str, marker_start: int) -> int:
    index = marker_start - 1
    count = 0
    while index >= 0 and markdown[index] == "_":
        count += 1
        index -= 1
    if not count:
        return 0
    before = markdown[index] if index >= 0 else ""
    if before and (before in _CLAIM_ID_BOUNDARY_CHARS or before in _CLAIM_ID_LEFT_CONTEXT_BLOCKERS):
        return 0
    return count


def _has_bare_source_marker_left_boundary(markdown: str, marker_start: int) -> bool:
    before = markdown[marker_start - 1] if marker_start > 0 else ""
    return before not in _CLAIM_ID_BOUNDARY_CHARS and before not in _CLAIM_ID_LEFT_CONTEXT_BLOCKERS


def _is_free_standing_bare_claim_candidate(candidate: str) -> bool:
    if not CLAIM_ID_TOKEN_RE.fullmatch(candidate):
        return False
    return any(ch.isdigit() or ch in "_-" for ch in candidate)


def _is_explicit_source_marker_candidate(candidate: str) -> bool:
    if _is_free_standing_bare_claim_candidate(candidate):
        return True
    return bool(CLAIM_ID_TOKEN_RE.fullmatch(candidate)) and candidate.isalpha() and candidate.isupper() and len(candidate) >= 6


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
