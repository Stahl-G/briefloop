"""WikiSkill learning steps on the native engine: frozen handoff, validated submit."""
import json

from wikiskill import feedback_loop, native_agents

import pytest

from briefloop.native_roles import agent_id, bind_session, learning_packet, run_tool, runner_tool_specs
from briefloop.store import Store


def _study(tmp_path, store):
    source = store.add_source('Synthetic', 'Orders: 10 intent, 2 signed.')
    text = json.dumps({'kind': 'user_comment', 'comment': '不要把意向写成订单',
                       'sources': [{'id': source['id'], 'path': f"sources/{source['id']}.txt"}]}, ensure_ascii=False)
    study = tmp_path / 'study'
    feedback_loop.begin(study, feedback=[{'text': text, 'source': 'feedback_1', 'origin': 'human', 'learning_intent': 'feedback'}], rounds=1)
    handoff = native_agents.dispatch(study, 'codex')['handoffs'][0]
    return study, handoff, source


def test_the_handoff_is_frozen_into_a_packet_with_relative_paths(tmp_path):
    store = Store(tmp_path / 'ws')
    study, handoff, source = _study(tmp_path, store)
    packet = learning_packet(store, handoff, tmp_path / 'stage')
    payload = json.loads((packet / 'payload.json').read_text(encoding='utf-8'))
    assert payload == {'role': 'maintainer', 'role_file': 'role.md', 'context_file': 'learning-context.json',
                       'output': payload['output']}
    context = json.loads((packet / 'learning-context.json').read_text(encoding='utf-8'))
    assert all(not str(row.get('file', '')).startswith('/') for row in context['human_feedback'])
    assert (packet / context['human_feedback'][0]['file']).is_file()
    assert 'intent' in (packet / 'sources' / f"{source['id']}.txt").read_text(encoding='utf-8')
    assert 'Wiki Maintainer' in (packet / 'role.md').read_text(encoding='utf-8')
    assert str(study) not in (packet / 'learning-context.json').read_text(encoding='utf-8')


def test_submit_patterns_goes_through_wikiskill_validation(tmp_path):
    store = Store(tmp_path / 'ws')
    study, handoff, _ = _study(tmp_path, store)
    config = {'native_role': 'maintainer', 'study': str(study), 'request_id': handoff['request_id'], 'session_id': 's1'}
    unbound = run_tool(store, config, 'submit_patterns', {'patterns': []})
    assert not unbound['ok'] and '尚未绑定' in unbound['error']
    bind_session(config, 's1')
    bind_session(config, 's1')  # a recreated engine session of the same child
    with pytest.raises(ValueError, match='另一个子会话'):
        bind_session(config, 's2')
    from wikiskill import product
    assert product._load(study)['requests'][handoff['request_id']]['delegation']['agent_id'] == agent_id('s1')
    bad = run_tool(store, config, 'submit_patterns', {'patterns': [{'name': 'x', 'content': 'y', 'sources': ['made-up']}]})
    assert not bad['ok'] and 'Cite permitted' in bad['error']
    assert feedback_loop.work(study)['phase'] == 'maintainer'
    allowed = json.loads(open(json.loads(open(handoff['payload_file']).read())['context_file']).read())['human_feedback'][0]['id']
    good = run_tool(store, config, 'submit_patterns', {'patterns': [{'name': '口径', 'content': '意向不是订单', 'sources': [allowed]}]})
    assert good['ok'] and json.loads(good['settle'])['patterns'][0]['name'] == '口径'
    state = feedback_loop.work(study)
    assert state['phase'] == 'proposer' and '口径' in state['patterns']
    assert [t['name'] for t in runner_tool_specs('maintainer')] == ['submit_patterns']
    assert [t['name'] for t in runner_tool_specs('proposer')] == ['submit_proposal']

    proposer = native_agents.dispatch(study, 'codex')['handoffs'][0]
    config = {**config, 'native_role': 'proposer', 'request_id': proposer['request_id'], 'session_id': 's3'}
    bind_session(config, 's3')
    both = run_tool(store, config, 'submit_proposal', {'skill': '# s', 'no_action': True, 'note': 'n'})
    assert not both['ok'] and '二选一' in both['error']
    empty = run_tool(store, config, 'submit_proposal', {'note': 'n'})
    assert not empty['ok'] and 'no_action' in empty['error']
    done = run_tool(store, config, 'submit_proposal', {'skill': '# 口径分层\n意向、合同、交付分开写。', 'note': '依据反馈'})
    assert done['ok'], done
    assert feedback_loop.work(study)['candidate']['skill']
