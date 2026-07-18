"""Local CLIProxyAPI Responses adapter for experimental LAJ measurement.

CLIProxy is an explicit execution origin, not a hidden OpenAI endpoint override.
The adapter deliberately reuses the one OpenAI-compatible byte projector and
the package-owned provider outcome classifier.
"""

from __future__ import annotations

from multi_agent_brief.semantic_evaluator.adapters.openai_responses import (
    OpenAIResponsesAdapterV4,
)


CLIPROXY_ADAPTER_ID = "local_proxy_responses_v1"
CLIPROXY_PROVIDER_ID = "local_proxy_responses"
CLIPROXY_ADAPTER_VERSION = "local_proxy_responses_adapter_v1"
CLIPROXY_BASE_URL = "http://127.0.0.1:8317/v1"


class CLIProxyResponsesAdapterV1(OpenAIResponsesAdapterV4):
    """One fixed-loopback OpenAI-compatible Responses transport."""

    adapter_id = CLIPROXY_ADAPTER_ID
    adapter_version = CLIPROXY_ADAPTER_VERSION
    provider_id = CLIPROXY_PROVIDER_ID
    qualification_eligible = False
    base_url = CLIPROXY_BASE_URL


__all__ = [
    "CLIPROXY_ADAPTER_ID",
    "CLIPROXY_ADAPTER_VERSION",
    "CLIPROXY_BASE_URL",
    "CLIPROXY_PROVIDER_ID",
    "CLIProxyResponsesAdapterV1",
]
