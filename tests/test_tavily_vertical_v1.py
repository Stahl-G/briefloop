"""Public and runtime guidance for the narrow Tavily vertical."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
REFERENCE_PATHS = (
    Path(".agents/skills/briefloop/references/codex-controlstore-v2.md"),
    Path(
        "src/multi_agent_brief/runtime_kits/codex/skills/briefloop/references/"
        "controlstore-v2.md"
    ),
)


def test_runtime_references_are_byte_identical_and_truthful() -> None:
    payloads = tuple((ROOT / path).read_bytes() for path in REFERENCE_PATHS)

    assert payloads[0] == payloads[1]
    text = payloads[0].decode("utf-8")
    assert "RunSourceDiscoveryAuthorization" in text
    assert "frozen atomic task matrix" in text
    assert "20 advanced Search results" in text
    assert "deterministic 30-day backfill" in text
    assert "Search snippets are" in text
    assert "never source-pack members or claims-eligible" in text
    assert "successful Extract content is claims-eligible" in text
    assert "Runs without either authorization" in text
