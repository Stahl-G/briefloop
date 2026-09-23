"""Search key storage with synthetic secrets and native filesystem permissions."""
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from briefloop import tavily, bocha, zhipu


PROVIDERS = [(tavily, 'TAVILY_API_KEY'), (bocha, 'BOCHA_API_KEY'), (zhipu, 'ZHIPU_SEARCH_API_KEY')]


@pytest.mark.parametrize('provider,variable', PROVIDERS, ids=['tavily', 'bocha', 'zhipu'])
def test_search_key_is_private_before_write_and_after_replace(tmp_path, monkeypatch, provider, variable):
    monkeypatch.setenv(variable, '')
    root = tmp_path / 'shared parent'; root.mkdir()
    protected = []
    if os.name == 'nt':
        from briefloop.connectors import windows_acl
        grant = subprocess.run(['icacls.exe', str(root), '/grant', '*S-1-1-0:(OI)(CI)R'],
                               capture_output=True, timeout=10)
        assert grant.returncode == 0, grant.stderr
        protect = windows_acl.protect_private
        def protect_empty(path):
            assert Path(path).read_bytes() == b''
            protect(path)
            windows_acl.verify_private(path)
            protected.append(path)
        monkeypatch.setattr(windows_acl, 'protect_private', protect_empty)
    else:
        chmod = os.fchmod
        def protect_empty(descriptor, mode):
            assert os.fstat(descriptor).st_size == 0
            chmod(descriptor, mode)
            protected.append(descriptor)
        monkeypatch.setattr(os, 'fchmod', protect_empty)
    path = root / (provider.NAME + '.key')
    key = 'synthetic-only-令牌'
    assert provider.save_key(key, key_file=path) == {'configured': True, 'source': 'file'}
    if os.name == 'nt':
        windows_acl.verify_private(path)
    else:
        assert path.stat().st_mode & 0o777 == 0o600
    assert len(protected) == 1
    assert path.read_bytes() == key.encode('utf-8')
    assert provider._read_key(key_file=path) == (key, 'file')
    assert set(root.iterdir()) == {path}


@pytest.mark.parametrize('provider,variable', PROVIDERS, ids=['tavily', 'bocha', 'zhipu'])
def test_search_key_permission_failure_preserves_old_file_and_closes_temp(tmp_path, monkeypatch, provider, variable):
    monkeypatch.setenv(variable, '')
    path = tmp_path / (provider.NAME + '.key')
    provider.save_key('synthetic-old-key', key_file=path)
    previous = path.read_bytes()
    created = []
    mkstemp = tempfile.mkstemp
    def track_temp(*args, **kwargs):
        result = mkstemp(*args, **kwargs)
        created.append(result)
        return result
    monkeypatch.setattr(tempfile, 'mkstemp', track_temp)

    def denied(*args):
        assert len(created) == 1 and Path(created[0][1]).read_bytes() == b''
        raise PermissionError('synthetic private storage denial')
    if os.name == 'nt':
        from briefloop.connectors import windows_acl
        monkeypatch.setattr(windows_acl, 'protect_private', denied)
    else:
        monkeypatch.setattr(os, 'fchmod', denied)
    with pytest.raises(PermissionError, match='synthetic private storage denial'):
        provider.save_key('synthetic-new-key', key_file=path)
    assert path.read_bytes() == previous
    assert set(tmp_path.iterdir()) == {path}
    with pytest.raises(OSError):
        os.fstat(created[0][0])
