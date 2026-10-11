"""Background check coverage stays explicit; fixtures do not prove semantic quality."""
import json

import pytest

from briefloop.runtime import assessment_prompt
from briefloop.store import Store


FAST_CHECKS = {'fact_qualifiers', 'evidence_support'}


def saved_report(tmp_path, mode):
    store = Store(tmp_path)
    source = store.add_source('Synthetic project notice',
                              'Published April 3; updated April 8. Phase one is under construction. '
                              'The project targets up to 80 units by December for selected partners.')
    run = store.create_run({'title': 'Project update', 'objective': 'Explain the project status.',
                            'completion_mode': mode}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Project update',
                                      'markdown': 'On April 8, all partners received 80 completed units.',
                                      'citations': [{'source_id': source['id'], 'locator': 'line 1',
                                                     'excerpt': store.source_text(source['id'])}]})
    return store, source, brief


@pytest.mark.parametrize('mode,backend', [('fast', 'codex'), ('fast_web', 'briefloop-native')])
def test_fast_assessment_packet_carries_qualifier_and_support_scope(tmp_path, mode, backend):
    store, source, brief = saved_report(tmp_path, mode)
    folder = tmp_path / 'evaluation'; folder.mkdir()
    assessment_prompt(store, brief, folder, backend)
    packet_root = folder / 'packet' if backend == 'briefloop-native' else folder
    packet = json.loads((packet_root / 'input.json').read_text())
    checks = {check['id']: check for check in packet['assessment_checks']}
    assert FAST_CHECKS.issubset(checks)
    assert all(checks[key]['scope'] for key in FAST_CHECKS)
    assert packet['brief']['hash'] == brief['hash']
    assert packet['brief']['citations'][0]['source_id'] == source['id']
