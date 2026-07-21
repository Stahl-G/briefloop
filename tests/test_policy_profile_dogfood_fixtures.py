from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml

from multi_agent_brief.outputs.finalize import finalize_reader_outputs


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "policy_profile_dogfood" / "cases.json"
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _load_fixture_bundle() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))






def test_policy_profile_dogfood_fixture_bundle_is_public_safe_and_bounded() -> None:
    bundle = _load_fixture_bundle()
    rendered = json.dumps(bundle, ensure_ascii=False)

    assert bundle["schema_version"] == "briefloop.policy_profile_dogfood_fixture.v1"
    assert bundle["metadata"]["synthetic"] is True
    assert bundle["metadata"]["public_safe"] is True
    assert "private_planning" not in rendered
    assert "/Users/" not in rendered
    assert "file://" not in rendered.lower()
    assert "truth proof" in bundle["metadata"]["boundary"]
    assert "release readiness" in bundle["metadata"]["boundary"]
    urls = _urls_from_fixture(bundle)
    assert urls
    for url in urls:
        assert urlparse(url).hostname == "example.com"


def test_policy_profile_dogfood_url_guard_extracts_json_string_values() -> None:
    payload = {"source_url": "https://example.com/targetco-demo", "note": "synthetic URL"}

    assert _urls_from_fixture(payload) == ["https://example.com/targetco-demo"]










def _urls_from_fixture(value: Any) -> list[str]:
    if isinstance(value, str):
        return [match.rstrip(".,);]") for match in _URL_RE.findall(value)]
    if isinstance(value, dict):
        urls: list[str] = []
        for item in value.values():
            urls.extend(_urls_from_fixture(item))
        return urls
    if isinstance(value, list):
        urls = []
        for item in value:
            urls.extend(_urls_from_fixture(item))
        return urls
    return []
