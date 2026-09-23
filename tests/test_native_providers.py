"""Native provider credential storage on the installed platform; no engine calls."""
import json
import os

import pytest

from briefloop import native_providers as providers


def test_native_credentials_redacted_and_not_in_workspace(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'providers.json'
    monkeypatch.setattr(providers, 'config_path', lambda: path)
    body = {'provider': 'synthetic', 'model': 'fixture', 'protocol': 'chat-completions',
            'api_key': 'test-not-a-real-key', 'base_url': 'https://example.test/v1'}
    assert providers.save(body)['model'] == 'synthetic/fixture'
    assert 'test-not-a-real-key' not in json.dumps(providers.configurations())
    if os.name == 'nt':
        from briefloop.connectors.windows_acl import verify_private
        verify_private(path)
    else:
        assert path.stat().st_mode & 0o777 == 0o600
    providers.save({**body, 'api_key': ''})
    assert json.loads(path.read_text(encoding='utf-8'))['synthetic/fixture']['api_key'] == 'test-not-a-real-key'


def test_native_credential_permission_failure_keeps_saved_config_and_closes_temp(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'providers.json'
    monkeypatch.setattr(providers, 'config_path', lambda: path)
    body = {'provider': 'synthetic', 'model': 'fixture', 'api_key': 'synthetic-old-key',
            'base_url': 'https://example.test/v1'}
    providers.save(body)
    previous = path.read_bytes()

    def denied(*args):
        pending = list(path.parent.glob('.providers-*'))
        assert len(pending) == 1 and pending[0].read_bytes() == b''
        raise PermissionError('synthetic private storage denial')

    if os.name == 'nt':
        from briefloop.connectors import windows_acl
        monkeypatch.setattr(windows_acl, 'protect_private', denied)
    else:
        monkeypatch.setattr(os, 'fchmod', denied)
    with pytest.raises(PermissionError, match='synthetic private storage denial'):
        providers.save({**body, 'api_key': 'synthetic-new-key'})
    assert path.read_bytes() == previous
    assert not list(path.parent.glob('.providers-*'))
