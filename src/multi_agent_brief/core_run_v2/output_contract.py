"""Pure catalog and reader-body measurement for frozen output contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Protocol

from multi_agent_brief.audit.deterministic import (
    _heading_level,
    source_reference_section_level,
)


OUTPUT_EXTENT_CATALOG_ID = "briefloop.output_extent_catalog.v1"
BODY_LENGTH_BASIS = "reader_body_excluding_source_reference_sections"
BODY_LENGTH_UNIT = "word_equivalent_tokens"
OutputExtent = Literal["compact", "balanced", "detailed"]
LanguageFamily = Literal["latin", "cjk"]

_EXTENT_BUDGETS: dict[LanguageFamily, dict[OutputExtent, tuple[int, int]]] = {
    "latin": {
        "compact": (350, 550),
        "balanced": (600, 800),
        "detailed": (900, 1400),
    },
    "cjk": {
        "compact": (700, 1100),
        "balanced": (1200, 1800),
        "detailed": (2000, 3200),
    },
}
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
_CJK_IDEOGRAPH = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


class _OutputContract(Protocol):
    output_extent: str
    extent_catalog_id: str
    body_length_basis: str
    body_length_unit: str
    resolved_minimum: int
    resolved_maximum: int


@dataclass(frozen=True)
class ResolvedOutputBudget:
    output_extent: OutputExtent
    language_family: LanguageFamily
    extent_catalog_id: str
    body_length_basis: str
    body_length_unit: str
    resolved_minimum: int
    resolved_maximum: int


@dataclass(frozen=True)
class ReaderBodyMeasurement:
    output_extent: str
    extent_catalog_id: str
    actual: int
    resolved_minimum: int
    resolved_maximum: int
    basis: str
    unit: str
    in_bounds: bool


def language_family_for(output_language: str) -> LanguageFamily:
    """Resolve only the product-supported language families; never guess."""

    normalized = output_language.strip().casefold().replace("_", "-")
    if normalized in {"en", "english"} or normalized.startswith("en-"):
        return "latin"
    if normalized in {"zh", "chinese", "中文"} or normalized.startswith("zh-"):
        return "cjk"
    raise ValueError("output language has no output extent catalog family")


def resolve_output_extent(
    output_extent: str,
    output_language: str,
    *,
    extent_catalog_id: str = OUTPUT_EXTENT_CATALOG_ID,
) -> ResolvedOutputBudget:
    if extent_catalog_id != OUTPUT_EXTENT_CATALOG_ID:
        raise ValueError("unknown output extent catalog")
    if output_extent not in {"compact", "balanced", "detailed"}:
        raise ValueError("unknown output extent")
    family = language_family_for(output_language)
    minimum, maximum = _EXTENT_BUDGETS[family][output_extent]  # type: ignore[index]
    return ResolvedOutputBudget(
        output_extent=output_extent,  # type: ignore[arg-type]
        language_family=family,
        extent_catalog_id=extent_catalog_id,
        body_length_basis=BODY_LENGTH_BASIS,
        body_length_unit=BODY_LENGTH_UNIT,
        resolved_minimum=minimum,
        resolved_maximum=maximum,
    )


def verify_output_contract(contract: _OutputContract, output_language: str) -> ResolvedOutputBudget:
    expected = resolve_output_extent(
        contract.output_extent,
        output_language,
        extent_catalog_id=contract.extent_catalog_id,
    )
    if (
        contract.body_length_basis != expected.body_length_basis
        or contract.body_length_unit != expected.body_length_unit
        or contract.resolved_minimum != expected.resolved_minimum
        or contract.resolved_maximum != expected.resolved_maximum
    ):
        raise ValueError("output contract does not match its catalog resolution")
    return expected


def reader_body_markdown(markdown: str) -> str:
    """Exclude exactly the source/reference sections recognized by audit."""

    source_level: int | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        heading_level = _heading_level(line)
        if heading_level is not None and source_level is not None and heading_level <= source_level:
            source_level = None
        section_level = source_reference_section_level(line)
        if section_level is not None:
            source_level = section_level
            continue
        if source_level is None:
            body.append(line)
    return "\n".join(body)


def count_word_equivalent_tokens(markdown: str) -> int:
    return len(_ASCII_WORD.findall(markdown)) + len(_CJK_IDEOGRAPH.findall(markdown))


def measure_reader_body(markdown: str, contract: _OutputContract) -> ReaderBodyMeasurement:
    actual = count_word_equivalent_tokens(reader_body_markdown(markdown))
    return ReaderBodyMeasurement(
        output_extent=contract.output_extent,
        extent_catalog_id=contract.extent_catalog_id,
        actual=actual,
        resolved_minimum=contract.resolved_minimum,
        resolved_maximum=contract.resolved_maximum,
        basis=contract.body_length_basis,
        unit=contract.body_length_unit,
        in_bounds=contract.resolved_minimum <= actual <= contract.resolved_maximum,
    )


__all__ = [
    "BODY_LENGTH_BASIS",
    "BODY_LENGTH_UNIT",
    "OUTPUT_EXTENT_CATALOG_ID",
    "ReaderBodyMeasurement",
    "ResolvedOutputBudget",
    "count_word_equivalent_tokens",
    "language_family_for",
    "measure_reader_body",
    "reader_body_markdown",
    "resolve_output_extent",
    "verify_output_contract",
]
