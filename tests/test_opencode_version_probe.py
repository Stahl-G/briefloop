"""Version admission uses the same safe CLI launch path as service startup."""
import os
import subprocess
from types import SimpleNamespace

import pytest

from briefloop import platform_support
from briefloop.backends import opencode_server


@pytest.mark.parametrize('explicit_environment', [False, True])
def test_version_probe_resolves_windows_npm_shim_and_clears_electron_mode(tmp_path, monkeypatch, explicit_environment):
    shim = tmp_path / '中文 tools' / 'opencode.cmd'
    native = shim.parent / 'node_modules' / 'opencode.exe'
    native.parent.mkdir(parents=True)
    native.write_bytes(b'fixture: never executed')
    shim.write_text('@echo off', encoding='utf-8')
    shim.with_suffix('').write_text('exec "$basedir/node_modules/opencode.exe" "$@"\n', encoding='utf-8')
    # Exercise the real Windows shim conversion on this platform, while the
    # external process is a fixture. This is not native Windows acceptance.
    monkeypatch.setattr(platform_support, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setenv('ELECTRON_RUN_AS_NODE', '1')
    environment = {'PATH': 'fixture-path', 'ELECTRON_RUN_AS_NODE': '1'} if explicit_environment else None
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, 'opencode v2.0.14\n', '')

    monkeypatch.setattr(opencode_server.subprocess, 'run', run)
    assert opencode_server.executable_version(str(shim), environment) == '2.0.14'
    arguments, kwargs = calls[0]
    assert arguments == [str(native.resolve()), '--version']
    assert kwargs['stdin'] == subprocess.DEVNULL
    assert 'ELECTRON_RUN_AS_NODE' not in kwargs['env']
    assert kwargs['env']['PATH'] == (environment['PATH'] if explicit_environment else os.environ['PATH'])
    assert os.environ['ELECTRON_RUN_AS_NODE'] == '1'
    if environment is not None:
        assert environment == {'PATH': 'fixture-path', 'ELECTRON_RUN_AS_NODE': '1'}
