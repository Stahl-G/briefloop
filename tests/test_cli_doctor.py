import json
import sys


def test_doctor_reports_supported_opencode_majors_without_starting_host(tmp_path, monkeypatch, capsys):
    from briefloop import cli, host_bins
    from briefloop.backends.opencode_server import SUPPORTED_MAJORS

    monkeypatch.setattr(host_bins, 'find', lambda name: '/fixture/' + name)
    monkeypatch.setattr(sys, 'argv', ['briefloop', 'doctor', '--workspace', str(tmp_path)])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result['opencode_supported_majors'] == list(SUPPORTED_MAJORS) == [1, 2]
    assert 'opencode_expected_major' not in result
    assert result['opencode'] == '/fixture/opencode'
    assert '未验证本机协议' in result['note']
