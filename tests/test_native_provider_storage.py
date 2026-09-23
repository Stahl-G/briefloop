"""Private provider storage works in the wheel without POSIX-only APIs."""
import json
import os
from pathlib import Path

import pytest

from briefloop import native_providers as providers
from briefloop.connectors import config


def body():
    return {'provider': 'synthetic', 'model': 'fixture', 'name': '合成提供商',
            'protocol': 'chat-completions', 'base_url': 'https://example.invalid/v1',
            'api_key': 'synthetic-not-a-real-key'}


def test_save_and_update_without_posix_fchmod(tmp_path, monkeypatch):
    path = tmp_path / '中文配置' / 'providers.json'
    monkeypatch.setattr(providers, 'config_path', lambda: path)
    monkeypatch.delattr(os, 'fchmod', raising=False)
    assert providers.save(body())['model'] == 'synthetic/fixture'
    providers.save({**body(), 'model': 'second', 'api_key': 'synthetic-rotated'})
    providers.save({**body(), 'api_key': ''})
    rows = json.loads(path.read_text(encoding='utf-8'))
    assert rows['synthetic/fixture']['name'] == '合成提供商'
    assert {r['api_key'] for r in rows.values()} == {'synthetic-rotated'}
    assert 'api_key' not in providers.configurations()[0]
    assert all(r['has_key'] for r in providers.configurations())
    if os.name == 'nt':
        from briefloop.connectors.windows_acl import verify_private
        verify_private(path)
    else:
        assert path.stat().st_mode & 0o777 == 0o600


def test_protection_failure_preserves_old_config_and_removes_empty_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'providers.json'
    monkeypatch.setattr(providers, 'config_path', lambda: path)
    providers.save(body())
    old = path.read_bytes()
    def denied(temporary):
        assert Path(temporary).read_bytes() == b''
        raise config.ConnectorError('synthetic denial', code='private_storage_unavailable')
    monkeypatch.setattr(config, 'private_path', denied)
    with pytest.raises(ValueError, match='未保存凭据'):
        providers.save({**body(), 'api_key': 'must-never-be-written'})
    assert path.read_bytes() == old
    assert not list(tmp_path.glob('.save-*'))
