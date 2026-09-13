"""A CLI installed outside the service's PATH must still be found."""


def test_finds_a_binary_that_is_not_on_path(tmp_path, monkeypatch):
    import os
    from briefloop import host_bins
    directory = tmp_path/'bin'; directory.mkdir()
    executable = directory/('demo-host.exe' if os.name=='nt' else 'demo-host'); executable.write_text('#!/bin/sh\n'); executable.chmod(0o755)
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert host_bins.find('demo-host', extra=(str(directory),)) == str(executable)
    assert host_bins.find('definitely-not-installed-host') is None


def test_known_directories_cover_the_host_clis():
    from briefloop import host_bins
    assert '~/.opencode/bin' in host_bins.EXTRA_DIRS
    assert '/opt/homebrew/bin' in host_bins.EXTRA_DIRS


def test_npm_cli_runs_with_desktop_path(tmp_path, monkeypatch):
    import os
    import subprocess
    from pathlib import Path
    import pytest
    from briefloop import host_bins
    from briefloop.platform_support import OwnedProcess
    if os.name == 'nt':
        pytest.skip('POSIX npm shebang; Windows shims have their own checks')
    node = host_bins.find('node')
    if not node:
        pytest.skip('Node needed for actual npm shim execution')
    entry = tmp_path/'npm-cli.js'
    entry.write_text('#!/usr/bin/env node\nconsole.log(process.argv[2]);\n')
    entry.chmod(0o755)
    shim = tmp_path/'codex'; shim.symlink_to(entry)
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', (str(Path(node).parent),))
    p = OwnedProcess([str(shim), 'MODEL_OK'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=10)
        assert p.returncode == 0, err
        assert out.strip() == 'MODEL_OK'
    finally:
        p.close_tree(timeout=.2)
