from __future__ import annotations

from multi_agent_brief.models.base import ModelProvider


class MockProvider(ModelProvider):
    """Deterministic provider for local demos and tests."""

    name = "mock"

    def complete(self, prompt: str) -> str:
        return "MockProvider response: " + prompt[:200]

