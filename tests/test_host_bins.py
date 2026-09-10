"""A CLI installed outside the service's PATH must still be found."""


def test_finds_a_binary_that_is_not_on_path(tmp_path, monkeypatch):
    from briefloop import host_bins
    directory = tmp_path/'bin'; directory.mkdir()
    executable = directory/'demo-host'; executable.write_text('#!/bin/sh\n'); executable.chmod(0o755)
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert host_bins.find('demo-host', extra=(str(directory),)) == str(executable)
    assert host_bins.find('definitely-not-installed-host') is None


def test_known_directories_cover_the_host_clis():
    from briefloop import host_bins
    assert '~/.opencode/bin' in host_bins.EXTRA_DIRS
    assert '/opt/homebrew/bin' in host_bins.EXTRA_DIRS
