"""Native ACL checks use only synthetic credentials inside temporary workspaces."""
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows DACL behavior')


def test_connector_credentials_remove_inherited_and_explicit_broad_grants(tmp_path):
    from briefloop.connectors.config import LocalConfig
    from briefloop.connectors.windows_acl import verify_private

    root = tmp_path / '中文 credential workspace'
    root.mkdir()
    result = subprocess.run(['icacls.exe', str(root), '/grant', '*S-1-1-0:(OI)(CI)F'],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    config = LocalConfig(root)
    secret = {'bearer_token': 'synthetic-only-令牌', 'env': {}}
    binding = config.new_binding(secret)
    config.records = {'synthetic': {'credential_binding': binding}}
    config.persist()
    credential = config.credential_path(binding)
    # Simulate a legacy file with an explicit grant; directory hardening alone is insufficient.
    result = subprocess.run(['icacls.exe', str(credential), '/grant', '*S-1-1-0:F'],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with pytest.raises(OSError):
        verify_private(credential)
    reopened = LocalConfig(root)
    assert reopened.get_secrets(reopened.records['synthetic']) == secret
    for path in (reopened.directory, *reopened.directory.rglob('*')):
        verify_private(path)


def test_acl_failure_preserves_previous_file_and_writes_no_secret(tmp_path, monkeypatch):
    from briefloop.connectors.config import ConnectorError, LocalConfig, atomic_json
    from briefloop.connectors import windows_acl

    config = LocalConfig(tmp_path)
    config.persist()
    previous = config.path.read_bytes()
    def denied(path):
        assert Path(path).read_bytes() == b''  # The temporary file is protected before serialization.
        raise PermissionError('synthetic ACL denial')
    monkeypatch.setattr(windows_acl, 'protect_private', denied)
    with pytest.raises(ConnectorError) as failure:
        atomic_json(config.path, {'bearer_token': 'must-never-reach-disk'})
    assert failure.value.code == 'private_storage_unavailable'
    assert config.path.read_bytes() == previous
    assert not list(config.directory.glob('.save-*'))


def test_acl_api_success_without_private_acl_still_fails_verification(tmp_path, monkeypatch):
    from briefloop.connectors import windows_acl

    path = tmp_path / 'synthetic.txt'
    path.write_text('')
    original = windows_acl._api
    def api(library, name, args, result=None):
        if name == 'SetSecurityInfo':
            return lambda *arguments: 0
        return original(library, name, args) if result is None else original(library, name, args, result)
    monkeypatch.setattr(windows_acl, '_api', api)
    with pytest.raises(OSError):
        windows_acl.protect_private(path)


def test_connector_rejects_directory_junction_without_touching_target(tmp_path):
    import _winapi
    from briefloop.connectors.config import ConnectorError, LocalConfig
    from briefloop.connectors.windows_acl import verify_private

    target = tmp_path / 'unrelated synthetic directory'
    target.mkdir()
    marker = target / 'keep.txt'
    marker.write_bytes(b'keep synthetic data')
    root = tmp_path / 'workspace'
    root.mkdir()
    junction = root / '.connectors'
    _winapi.CreateJunction(str(target), str(junction))
    try:
        with pytest.raises(ConnectorError):
            LocalConfig(root)
        assert list(target.iterdir()) == [marker]
        assert marker.read_bytes() == b'keep synthetic data'
        with pytest.raises(OSError):
            verify_private(target)
    finally:
        junction.rmdir()
