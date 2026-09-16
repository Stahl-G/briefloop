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


def _node_or_skip():
    import os
    import pytest
    from briefloop import host_bins
    if os.name == 'nt':
        pytest.skip('POSIX npm shebang; Windows shims have their own checks')
    node = host_bins.find('node')
    if not node:
        pytest.skip('Node needed for actual npm shim execution')
    return node


def _npm_link(directory, body='console.log(process.argv[2]);'):
    package = directory/'lib'/'node_modules'/'demo'; package.mkdir(parents=True)
    entry = package/'cli.js'
    entry.write_text('#!/usr/bin/env node\n' + body + '\n')
    entry.chmod(0o755)
    bin_dir = directory/'bin'; bin_dir.mkdir()
    shim = bin_dir/'codex'; shim.symlink_to(entry)
    return shim


def test_npm_cli_runs_when_desktop_path_lacks_node(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from briefloop import host_bins
    from briefloop.platform_support import OwnedProcess
    node = _node_or_skip()
    shim = _npm_link(tmp_path)
    monkeypatch.setenv('PATH', str(shim.parent))
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', (str(Path(node).parent),))
    p = OwnedProcess([str(shim), 'MODEL_OK'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=10)
        assert p.returncode == 0, err
        assert out.strip() == 'MODEL_OK'
    finally:
        p.close_tree(timeout=.2)


def test_npm_cli_prefers_node_installed_beside_the_linked_cli(tmp_path, monkeypatch):
    from briefloop import host_bins
    from briefloop.platform_support import cli_command
    real_node = _node_or_skip()
    shim = _npm_link(tmp_path)
    beside = shim.parent/'node'; beside.symlink_to(real_node)
    monkeypatch.setenv('PATH', '/nonexistent')
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', ())
    assert cli_command([shim, '--version']) == [str(beside.resolve()), str(shim.resolve()), '--version']


def test_non_node_and_bare_commands_are_left_to_popen(tmp_path, monkeypatch):
    import os
    import pytest
    from briefloop.platform_support import cli_command
    if os.name == 'nt':
        pytest.skip('POSIX command resolution')
    script = tmp_path/'tool'; script.write_text('#!/bin/sh\necho ok\n'); script.chmod(0o755)
    assert cli_command([script, 'x']) == [str(script), 'x']
    # A same-named file in the working directory must not shadow PATH lookup.
    (tmp_path/'codex').write_text('#!/usr/bin/env node\n')
    monkeypatch.chdir(tmp_path)
    assert cli_command(['codex', 'x']) == ['codex', 'x']
    assert cli_command([tmp_path/'missing', 'x']) == [str(tmp_path/'missing'), 'x']


def test_npm_cli_without_any_node_fails_clearly(tmp_path, monkeypatch):
    import pytest
    from briefloop import host_bins
    from briefloop.platform_support import cli_command
    _node_or_skip()
    shim = _npm_link(tmp_path)
    monkeypatch.setenv('PATH', '/nonexistent')
    monkeypatch.setattr(host_bins, 'EXTRA_DIRS', ())
    with pytest.raises(FileNotFoundError, match='Node.js'):
        cli_command([shim])
