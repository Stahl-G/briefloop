"""Pure catalog and reader-body measurement rows for RUN-UX-1A."""

from __future__ import annotations

import pytest

from multi_agent_brief.contracts.v2 import RunOutputContract
from multi_agent_brief.core_run_v2.output_contract import (
    BODY_LENGTH_BASIS,
    BODY_LENGTH_UNIT,
    OUTPUT_EXTENT_CATALOG_ID,
    measure_reader_body,
    resolve_output_extent,
    verify_output_contract,
)


def _contract(extent: str, language: str) -> RunOutputContract:
    resolved = resolve_output_extent(extent, language)
    return RunOutputContract.model_validate(
        {
            "schema_version": RunOutputContract.schema_id,
            "output_extent": resolved.output_extent,
            "extent_catalog_id": resolved.extent_catalog_id,
            "body_length_basis": resolved.body_length_basis,
            "body_length_unit": resolved.body_length_unit,
            "resolved_minimum": resolved.resolved_minimum,
            "resolved_maximum": resolved.resolved_maximum,
        },
        strict=True,
    )


@pytest.mark.parametrize(
    ("language", "extent", "bounds"),
    [
        ("en", "compact", (350, 550)),
        ("en-US", "balanced", (600, 800)),
        ("english", "detailed", (900, 1400)),
        ("zh", "compact", (700, 1100)),
        ("zh-CN", "balanced", (1200, 1800)),
        ("chinese", "detailed", (2000, 3200)),
    ],
)
def test_catalog_resolves_every_supported_extent_language_row(
    language: str,
    extent: str,
    bounds: tuple[int, int],
) -> None:
    resolved = resolve_output_extent(extent, language)

    assert resolved.extent_catalog_id == OUTPUT_EXTENT_CATALOG_ID
    assert (resolved.resolved_minimum, resolved.resolved_maximum) == bounds
    assert resolved.body_length_basis == BODY_LENGTH_BASIS
    assert resolved.body_length_unit == BODY_LENGTH_UNIT


@pytest.mark.parametrize("count", [599, 801])
def test_english_balanced_a2_boundaries_block(count: int) -> None:
    measurement = measure_reader_body(" ".join(["word"] * count), _contract("balanced", "en"))

    assert measurement.in_bounds is False
    assert measurement.actual == count


@pytest.mark.parametrize("count", [600, 800])
def test_english_balanced_a2_endpoints_pass(count: int) -> None:
    assert measure_reader_body(" ".join(["word"] * count), _contract("balanced", "en")).in_bounds


def test_measurement_uses_canonical_source_heading_scope_and_cjk_tokens() -> None:
    markdown = """# Reader title

alpha-beta don't 研究

## Sources

ignored source words 资料来源

### Nested source note

still ignored

## Reader conclusion

继续 market"""

    measurement = measure_reader_body(markdown, _contract("compact", "en"))

    assert measurement.actual == 11


@pytest.mark.parametrize(
    ("extent", "language"),
    [
        ("unknown", "en"),
        ("balanced", "bilingual"),
        ("balanced", "fr"),
        ("balanced", "ja"),
        ("balanced", "ko-KR"),
    ],
)
def test_unknown_catalog_combinations_fail_closed(extent: str, language: str) -> None:
    with pytest.raises(ValueError):
        resolve_output_extent(extent, language)


def test_forged_resolved_contract_fails_catalog_verification() -> None:
    forged = _contract("balanced", "en").model_copy(update={"resolved_maximum": 801})

    with pytest.raises(ValueError):
        verify_output_contract(forged, "en")
