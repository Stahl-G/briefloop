"""Background check coverage stays explicit; fixtures do not prove semantic quality."""
import json

import pytest

from briefloop.runtime import Worker, assessment_prompt
from briefloop.store import Store, dump


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
    prompt = assessment_prompt(store, brief, folder, backend)
    packet_root = folder / 'packet' if backend == 'briefloop-native' else folder
    packet = json.loads((packet_root / 'input.json').read_text())
    checks = {check['id']: check for check in packet['assessment_checks']}
    assert FAST_CHECKS.issubset(checks)
    assert all(checks[key]['scope'] for key in FAST_CHECKS)
    assert packet['brief']['hash'] == brief['hash']
    assert packet['brief']['citations'][0]['source_id'] == source['id']
    # Both runtime routes receive the same distinction; the rich source/qualifier
    # wording is supplied by the shared role prompt and per-check scope.
    assert '位置匹配不能代替语义核对' in prompt
    assert '事件发生日/发布日/更新日' in checks['fact_qualifiers']['scope']
    assert '表头、单位与限定条件' in checks['evidence_support']['scope']


def test_standard_report_retains_existing_assessment_contract(tmp_path):
    store, _, brief = saved_report(tmp_path, 'standard')
    context = store.assessment_context(brief['id'])
    assert {check['id'] for check in context['assessment_checks']} == {
        'summary_consistency', 'inference_support'}
