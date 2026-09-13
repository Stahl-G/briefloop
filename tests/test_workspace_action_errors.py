"""Invalid tool inputs report actionable fields without echoing source content."""
import json
import sys

import pytest

from briefloop.cli import main


def test_action_validation_reports_fields_without_traceback(tmp_path, monkeypatch, capsys):
    request = tmp_path/'request.json'
    request.write_text(json.dumps({'action': 'evidence_span', 'evidence': {
        'source_id': 'synthetic', 'locator': 'PRIVATE_INVALID_VALUE', 'excerpt': 'Synthetic', 'value': 2.5}}))
    monkeypatch.setattr(sys, 'argv', ['briefloop', 'tool', '--workspace', str(tmp_path),
                                    'workspace-action', '--request', str(request)])
    with pytest.raises(SystemExit) as end:
        main()
    assert end.value.code == 2
    output = capsys.readouterr()
    data = json.loads(output.err)
    assert data['status'] == 'invalid'
    assert {e['field'] for e in data['errors']} == {'locator', 'value'}
    assert not output.out
    assert 'PRIVATE_INVALID_VALUE' not in output.err and 'Traceback' not in output.err


def test_inline_json_is_not_treated_as_a_request_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['briefloop', 'tool', '--workspace', str(tmp_path),
                                    'workspace-action', '--request', '{"private":"hidden"}'])
    with pytest.raises(SystemExit) as end:
        main()
    assert end.value.code == 2
    data = json.loads(capsys.readouterr().err)
    assert data['error'] == 'request_file_invalid'
    assert 'hidden' not in json.dumps(data)
