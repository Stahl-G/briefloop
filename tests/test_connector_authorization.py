"""Synthetic credentials only; HTTP request capture never contacts a server."""
from contextlib import asynccontextmanager
import json
import sys

import pytest

from briefloop.connectors import ConnectorService
from briefloop.connectors.config import ConnectorError, validate_secrets


CONFIG = {'name': 'Synthetic MCP', 'transport': 'http', 'url': 'https://example.invalid/mcp'}


@pytest.mark.parametrize('damage', ['missing', 'malformed'])
def test_legacy_unreadable_credentials_preserve_healthy_records_and_allow_repair(tmp_path, damage):
    service = ConnectorService(tmp_path)
    try:
        healthy = service.save({**CONFIG, 'name': 'Healthy'}, secrets={'bearer_token': 'healthy-synthetic'})
        damaged = service.save({**CONFIG, 'name': 'Damaged'}, secrets={'authorization_header': 'old-synthetic'})
        record = service._config.records[damaged['id']]
        record.pop('credential_type')
        service._config.persist()
        credential_path = service._config.credential_path(record['credential_binding'])
        if damage == 'missing':
            credential_path.unlink()
        else:
            credential_path.write_text('{"private": "DO_NOT_DISCLOSE"')
        before = service._config.path.read_bytes()
        views = {row['id']: row for row in service.list()}
        assert len(views) == 2 and views[healthy['id']] == healthy
        failed = views[damaged['id']]
        assert failed['credential_type'] == 'unknown' and failed['state'] == 'error'
        assert failed['error']['code'] == 'credentials_unreadable'
        assert service.status(damaged['id']) == failed
        assert 'DO_NOT_DISCLOSE' not in json.dumps(views)
        assert str(credential_path) not in json.dumps(views)
        assert service._config.path.read_bytes() == before
        assert credential_path.exists() == (damage == 'malformed')
        repaired = service.save({**CONFIG, 'name': 'Damaged'}, connector_id=damaged['id'],
                                secrets={'authorization_header': 'replacement-synthetic'})
        assert repaired['id'] == damaged['id'] and repaired['revision'] == damaged['revision'] + 1
        assert repaired['credential_type'] == 'authorization'
        assert repaired['has_credentials'] and repaired['state'] == 'disabled' and repaired['error'] is None
        assert service.status(healthy['id']) == healthy and len(service.list()) == 2
    finally:
        service.close()


def test_raw_authorization_stays_private_and_survives_config_edit(tmp_path):
    service = ConnectorService(tmp_path)
    try:
        saved = service.save(CONFIG, secrets={'authorization_header': 'synthetic-raw-token'})
        assert saved['has_credentials'] and not saved['enabled']
        assert saved['credential_type'] == 'authorization'
        edited = service.save({**CONFIG, 'name': 'Edited'}, connector_id=saved['id'])
        assert edited['has_credentials']
        assert 'synthetic-raw-token' not in json.dumps(service.list())
        assert 'synthetic-raw-token' not in service._config.path.read_text()
        record = service._config.records[saved['id']]
        assert service._config.get_secrets(record)['authorization_header'] == 'synthetic-raw-token'
    finally:
        service.close()
    reloaded = ConnectorService(tmp_path)
    try:
        assert reloaded.status(saved['id'])['has_credentials']
        # Existing credential JSON without the new field remains readable.
        record = reloaded._config.records[saved['id']]
        record.pop('credential_type')
        reloaded._config.credential_path(record['credential_binding']).write_text(
            json.dumps({'bearer_token': 'synthetic-legacy-token', 'env': {}}))
        legacy = reloaded._config.get_secrets(record)
        assert legacy == {'bearer_token': 'synthetic-legacy-token', 'authorization_header': '', 'env': {}}
        assert reloaded.status(saved['id'])['credential_type'] == 'bearer'
        switched = reloaded.save(CONFIG, connector_id=saved['id'], secrets={'authorization_header': 'new-raw'})
        assert switched['has_credentials']
        assert reloaded._config.get_secrets(reloaded._config.records[saved['id']])['bearer_token'] == ''
        cleared = reloaded.save(CONFIG, connector_id=saved['id'], secrets={})
        assert not cleared['has_credentials'] and cleared['credential_type'] == 'none'
    finally:
        reloaded.close()


def test_authorization_rejects_conflicts_injection_and_wrong_transport(tmp_path):
    invalid = [
        {'bearer_token': 'old', 'authorization_header': 'new'},
        {'headers': {'Authorization': 'new', 'X-Injected': 'bad'}},
        *({'authorization_header': value} for value in
          ('raw\r\nX-Injected: bad', 'raw\x00', 'raw\t', 'raw\x7f', '非ASCII', ' raw', 'raw ', 'x' * 16385, {}, None)),
    ]
    for value in invalid:
        with pytest.raises(ConnectorError):
            validate_secrets(value)
    service = ConnectorService(tmp_path)
    try:
        with pytest.raises(ConnectorError, match='stdio'):
            service.save({'name': 'Process', 'transport': 'stdio', 'command': sys.executable},
                         secrets={'authorization_header': 'synthetic-raw'})
        assert service.list() == []
    finally:
        service.close()


@pytest.mark.parametrize(('secrets', 'expected'), [
    ({'authorization_header': 'synthetic.raw-token'}, 'synthetic.raw-token'),
    ({'authorization_header': 'Basic synthetic-value'}, 'Basic synthetic-value'),
    ({'bearer_token': 'synthetic-legacy-token'}, 'Bearer synthetic-legacy-token'),
    ({}, None),
])
def test_http_transport_sends_exact_authorization(monkeypatch, secrets, expected):
    anyio = pytest.importorskip('anyio')
    httpx2 = pytest.importorskip('httpx2')
    pytest.importorskip('mcp')
    from briefloop.connectors import transport

    seen = []
    def receive(request):
        seen.append(dict(request.headers))
        return httpx2.Response(200, json={})
    original_client = httpx2.AsyncClient
    monkeypatch.setattr(transport.httpx2, 'AsyncClient',
                        lambda **kwargs: original_client(transport=httpx2.MockTransport(receive), **kwargs))

    @asynccontextmanager
    async def capture(url, *, http_client):
        await http_client.post(url, json={'method': 'synthetic'})
        yield (None, None, None)
    monkeypatch.setattr(transport, 'streamable_http_client', capture)

    async def exercise():
        async with transport.connection_transport(
                {**CONFIG, 'timeout_seconds': 1, 'max_response_bytes': 1024}, secrets, {}):
            pass
    anyio.run(exercise)
    assert len(seen) == 1 and seen[0].get('authorization') == expected
    assert seen[0]['accept-encoding'] == 'identity'
    assert 'x-injected' not in seen[0]
