"""Public and runtime guidance for the narrow Tavily vertical."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
REFERENCE_PATHS = (
    Path(".agents/skills/briefloop/references/codex-controlstore-v2.md"),
    Path(
        "integrations/hermes-plugin/mabw/skills/briefloop/references/"
        "codex-controlstore-v2.md"
    ),
    Path(
        "src/multi_agent_brief/runtime_kits/codex/skills/briefloop/references/"
        "controlstore-v2.md"
    ),
)


def test_runtime_references_are_byte_identical_and_truthful() -> None:
    payloads = tuple((ROOT / path).read_bytes() for path in REFERENCE_PATHS)

    assert payloads[0] == payloads[1] == payloads[2]
    text = payloads[0].decode("utf-8")
    assert "RunSourceDiscoveryAuthorization" in text
    assert "one exact Human-confirmed Tavily Search request" in text
    assert "Search snippets are" in text
    assert "claims-ineligible" in text
    assert "provider-returned durable content is claims-eligible" in text
    assert "Runs without either authorization" in text


def test_public_tavily_claims_stay_within_recorded_evidence() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture-status.md").read_text(
        encoding="utf-8"
    )
    support = (ROOT / "docs/support-matrix.md").read_text(encoding="utf-8")
    migration = (ROOT / "docs/MIGRATION.md").read_text(encoding="utf-8")

    for text in (english, chinese, architecture, support):
        assert "Experimental" in text
        assert "NOT MEASURED" in text
    for text in (english, architecture, support, migration):
        assert "synthetic" in text.lower()
        assert "claims-ineligible" in text
    for text in (architecture, support):
        assert "checkout_publication_unsupported" in text
        assert "Windows" in text
        assert "provider" in text
        assert "no source promotion" in text
