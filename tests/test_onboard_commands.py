from __future__ import annotations

from types import SimpleNamespace

from multi_agent_brief.cli.onboard_commands import _onboarding_search_backend_plain


def test_onboard_serializes_declined_search_as_none():
    profile = SimpleNamespace(
        web_search_mode="disabled",
        search_backend="",
    )

    assert _onboarding_search_backend_plain(profile) == "none"


def test_onboard_serializes_search_modes_for_replay():
    assert (
        _onboarding_search_backend_plain(
            SimpleNamespace(web_search_mode="configure_later", search_backend="")
        )
        == "configure_later"
    )
    assert (
        _onboarding_search_backend_plain(
            SimpleNamespace(web_search_mode="runtime_tool", search_backend="")
        )
        == "runtime_websearch"
    )
    assert (
        _onboarding_search_backend_plain(
            SimpleNamespace(web_search_mode="external_api", search_backend="tavily")
        )
        == "tavily"
    )
