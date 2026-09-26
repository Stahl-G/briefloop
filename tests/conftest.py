"""Shared test setup.

Review, fact-check and lifecycle tests drive a scripted runtime under the default
`codex` backend name. They test the controller around the Reviewer, not a real
host, so the scripted host is treated as having a verified restricted Reviewer.
Tests of the capability boundary itself (#726) opt out with
`@pytest.mark.real_review_capabilities` and see the shipped declaration.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'real_review_capabilities: use the shipped backend capability declaration')


@pytest.fixture(autouse=True)
def _scripted_host_can_review(request, monkeypatch):
    if request.node.get_closest_marker('real_review_capabilities'):
        return
    from briefloop import backends
    monkeypatch.setitem(backends.CAPABILITIES, 'codex', backends.CAPABILITIES['codex'] | {'restricted_review'})
    # Scripted OpenCode transports model the verified v1 contract, regardless
    # of the developer machine's installed CLI. Version-gate tests opt out.
    monkeypatch.setattr('briefloop.review_capability._opencode_major', lambda: 1)
