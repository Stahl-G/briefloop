from __future__ import annotations

from multi_agent_brief.contracts.audited_brief_contract import (
    validate_audited_brief_contract,
)


def test_audited_brief_contract_allows_finalizer_src_tokens() -> None:
    result = validate_audited_brief_contract(
        "# Brief\n\nExampleCo opened a demo facility. [src:CL-001]\n"
    )

    assert result.status == "pass"
    assert result.finding_count == 0


def test_audited_brief_contract_rejects_unmarked_internal_source_appendix() -> None:
    result = validate_audited_brief_contract(
        "# Brief\n\n"
        "ExampleCo opened a demo facility. [src:CL-001]\n\n"
        "## Source Appendix\n\n"
        "- Claim Ledger: CL-001 from input/sources/source-001.md\n"
    )

    assert result.status == "fail"
    assert result.finding_count >= 1
    assert "unmarked_internal_source_appendix" in {finding.kind for finding in result.findings}


def test_audited_brief_contract_rejects_source_list_internal_ids() -> None:
    result = validate_audited_brief_contract(
        "# Brief\n\n"
        "Reader-safe body.\n\n"
        "## Source Appendix\n\n"
        "- CL-001 / SRC-001\n"
    )

    assert result.status == "fail"
    assert "source_list_internal_id" in {finding.kind for finding in result.findings}


def test_audited_brief_contract_rejects_local_paths_before_projection() -> None:
    result = validate_audited_brief_contract(
        "# Brief\n\n"
        "A local workspace path leaked: /Users/example/workspace/input/sources/source.md\n"
    )

    assert result.status == "fail"
    assert {"internal_process_wording", "local_path"}.issubset(
        {finding.kind for finding in result.findings}
    )


def test_audited_brief_contract_ignores_explicit_internal_blocks() -> None:
    result = validate_audited_brief_contract(
        "# Brief\n\n"
        "Reader-safe content.\n\n"
        "<!-- briefloop:internal start -->\n"
        "Claim Ledger: CL-001 from input/sources/source-001.md\n"
        "<!-- briefloop:internal end -->\n"
    )

    assert result.status == "pass"
