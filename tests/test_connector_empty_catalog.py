"""Exercise real SDK schema validation using synthetic dispatcher responses."""
import json
from types import SimpleNamespace

import pytest

anyio = pytest.importorskip('anyio')
pytest.importorskip('mcp')
from mcp.client.session import ClientSession
from mcp_types import PaginatedRequestParams
from pydantic import ValidationError

from briefloop.connectors.runtime import Owner
from briefloop.connectors import ConnectorService


CONFIG = {'name': 'Synthetic', 'transport': 'http', 'url': 'https://example.invalid/mcp',
          'timeout_seconds': 1, 'max_response_bytes': 65536}
TOOL = {'name': 'read_synthetic', 'inputSchema': {'type': 'object'}}


def sdk_client(replies, calls):
    class Dispatcher:
        async def send_raw_request(self, method, params, opts):
            calls.append(method)
            return replies[method]
    session = ClientSession(dispatcher=Dispatcher())
    # Use the version reported by the real integration diagnostic.
    session._negotiated_version = '2025-06-18'
    def listing(method):
        async def call(*, cursor=None):
            return await method(params=PaginatedRequestParams(cursor=cursor))
        return call
    return SimpleNamespace(server_capabilities=SimpleNamespace(tools={}, resources={}),
                           list_tools=listing(session.list_tools),
                           list_resources=listing(session.list_resources),
                           list_resource_templates=listing(session.list_resource_templates))


def test_empty_optional_catalogs_keep_sdk_session_usable_and_disclose_warning(tmp_path, monkeypatch):
    replies = {'tools/list': {'tools': [TOOL]}, 'resources/list': {'resources': {}},
               'resources/templates/list': {'resourceTemplates': {}}}
    calls = []
    owner = Owner(CONFIG, {}, 'synthetic')
    owner.client = sdk_client(replies, calls)
    async def exercise():
        await owner._catalog()
        # SDK validation errors did not close the session. This is an explicit
        # follow-up directory check, not an automatic retry or business call.
        assert len((await owner.client.list_tools()).tools) == 1
    anyio.run(exercise)
    assert calls == ['tools/list', 'resources/list', 'resources/templates/list', 'tools/list']
    assert owner.capabilities['tools'] == [TOOL]
    assert owner.capabilities['resources'] == owner.capabilities['resource_templates'] == []
    assert [w['catalog'] for w in owner.warnings] == ['resources', 'resource_templates']
    assert all(w['code'] == 'empty_object_catalog' for w in owner.warnings)
    service = ConnectorService(tmp_path)
    try:
        saved = service.save(CONFIG)
        monkeypatch.setattr(service, '_start', lambda *args: owner)
        result = service.test(saved['id'])
        assert result['ok'] and result['warnings'] == owner.warnings
        assert service.status(saved['id'])['warnings'] == owner.warnings
        assert 'read_synthetic' in json.dumps(result['capabilities'])
    finally:
        service.close()


@pytest.mark.parametrize(('method', 'invalid'), [
    ('resources/list', {'resources': {'private': 'DO_NOT_DISCLOSE'}}),
    ('resources/list', {'resources': {}, 'nextCursor': 99}),
    ('resources/list', {'resources': None}),
    ('resources/list', {}),
    ('resources/templates/list', {'resourceTemplates': {'private': 'DO_NOT_DISCLOSE'}}),
    ('tools/list', {'tools': {}}),
])
def test_other_catalog_schema_errors_still_fail(method, invalid):
    replies = {'tools/list': {'tools': [TOOL]}, 'resources/list': {'resources': []},
               'resources/templates/list': {'resourceTemplates': []}, method: invalid}
    calls = []
    owner = Owner(CONFIG, {}, 'synthetic')
    owner.client = sdk_client(replies, calls)
    with pytest.raises(ValidationError):
        anyio.run(owner._catalog)
    assert owner.warnings == []
    assert calls.count(method) == 1 and 'tools/call' not in calls
