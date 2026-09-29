"""Native ACL checks use only synthetic credentials inside temporary workspaces."""
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows DACL behavior')


@pytest.mark.parametrize('long_workspace', [False, True])
def test_connector_credentials_remove_inherited_and_explicit_broad_grants(tmp_path, long_workspace):
    from briefloop.connectors.config import LocalConfig
    from briefloop.connectors.windows_acl import verify_private
    from briefloop.connectors import ConnectorService
    from briefloop.platform_support import filesystem_path

    root = tmp_path / '中文 credential workspace'
    if long_workspace:
        root = tmp_path / ('中文 credential-' + 'w' * (224-len(str(tmp_path))-1-len('中文 credential-')))
    root.mkdir()
    result = subprocess.run(['icacls.exe', str(root), '/grant', '*S-1-1-0:(OI)(CI)F'],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    config = LocalConfig(root)
    secret = {'bearer_token': 'synthetic-only-令牌', 'env': {}}
    binding = config.new_binding(secret)
    config.records['synthetic'] = {'id':'synthetic',
        'config':{'name':'Synthetic ACL fixture','transport':'http','url':'https://example.invalid/mcp'},
        'revision':1,'enabled':False,'credential_binding':binding,'env_names':[]}
    config.persist()
    credential = config.credential_path(binding)
    # Simulate a legacy file with an explicit grant; directory hardening alone is insufficient.
    result = subprocess.run(['icacls.exe', str(filesystem_path(credential)), '/grant', '*S-1-1-0:F'],
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with pytest.raises(OSError):
        verify_private(credential)
    reopened = LocalConfig(root)
    assert reopened.get_secrets(reopened.records['synthetic']) == secret
    assert not str(credential).startswith('\\\\?\\')
    if long_workspace:assert len(str(credential)) > 260
    for path in (reopened.directory, *filesystem_path(reopened.directory).rglob('*')):
        verify_private(path)
    service = ConnectorService(root)
    try:
        updated = service.save(config.records['synthetic']['config'], connector_id='synthetic',
                               secrets={'bearer_token':'replacement-synthetic-only'})
        assert updated['revision'] == 2 and updated['state'] == 'disabled'
        assert not filesystem_path(credential).exists()
        assert len(list(filesystem_path(reopened.credentials).iterdir())) == 1
        service.delete('synthetic')
        assert service.list() == []
        assert not list(filesystem_path(reopened.credentials).iterdir())
        assert LocalConfig(root).records == {}
    finally:
        service.close()


@pytest.mark.parametrize('long_workspace', [False, True])
def test_acl_failure_preserves_previous_file_and_writes_no_secret(tmp_path, monkeypatch, long_workspace):
    from briefloop.connectors.config import ConnectorError, LocalConfig, atomic_json
    from briefloop.connectors import windows_acl
    from briefloop.platform_support import filesystem_path

    root = tmp_path
    if long_workspace:root = tmp_path / ('中文 denied-'+'w'*(224-len(str(tmp_path))-1-len('中文 denied-')))
    config = LocalConfig(root)
    config.persist()
    previous = filesystem_path(config.path).read_bytes()
    def denied(path):
        assert Path(path).read_bytes() == b''  # The temporary file is protected before serialization.
        raise PermissionError('synthetic ACL denial')
    monkeypatch.setattr(windows_acl, 'protect_private', denied)
    with pytest.raises(ConnectorError) as failure:
        atomic_json(config.path, {'bearer_token': 'must-never-reach-disk'})
    assert failure.value.code == 'private_storage_unavailable'
    assert filesystem_path(config.path).read_bytes() == previous
    assert not list(filesystem_path(config.directory).glob('.save-*'))


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
