"""Optional agent backend: workspace default, per-job freeze, transport routing."""
import json
import pytest
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.interactive_runtime import InteractiveRuntime


def test_archive_completed_partitions_by_backend(tmp_path):
    from briefloop.harness import HarnessManager
    from briefloop.opencode_harness import OpencodeHarness
    store = Store(tmp_path / 'workspace')
    codex = HarnessManager(store)
    opencode = OpencodeHarness(store)
    c = codex.create_session('c', runtime={'model': 'gpt-5.6-luna'})['id']
    o = opencode.create_session('o', runtime={'model': 'opencode-go/x'})['id']
    codex.chat.message(c, 'hi', status='completed')
    opencode.chat.message(o, 'hi', status='completed')
    assert codex.archive_completed('codex') == {'count': 1}
    assert opencode.archive_completed('opencode') == {'count': 1}
    assert codex.archive_completed('codex') == {'count': 0}
    assert opencode.archive_completed('opencode') == {'count': 0}


def test_chat_generate_pins_session_backend(tmp_path):
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path / 'workspace')
    source = store.add_source('memo', 'evidence')
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode',
                                'model': 'opencode-go/gpt-5.6-luna'})
    saved = {'title': 'prior', 'objective': 'o', 'completion_mode': 'fast',
             'research_strategy': 'goal_driven', 'target_minutes': 10}
    store.set_meta('requirements', saved)
    reference = workspace_action(store, {'action': 'inspect'})['requirements']
    assert reference == {'title': 'prior', 'objective': 'o'}
    assert store.meta('requirements') == saved
    store.set_meta('requirements', {**saved, 'completion_mode': 'standard'})
    assert workspace_action(store, {'action': 'inspect'})['requirements']['target_minutes'] == 10
    dispatched = workspace_action(store, {'action': 'generate',
                                          'requirements': {'title': 't', 'objective': 'o', 'completion_mode': 'fast',
                                                           'research_strategy': 'goal_driven', 'target_minutes': 10},
                                          'source_ids': [source['id']],
                                          'runtime': {'model': 'opencode-go/gpt-5.6-luna',
                                                      'agent_backend': 'opencode'}})
    assert json.loads(store.one('jobs', dispatched['job_id'])['payload'])['agent_backend'] == 'opencode'

    requirements = json.loads(store.one('runs', dispatched['run_id'])['requirements'])
    assert (requirements['completion_mode'], requirements['research_strategy'], requirements['target_minutes']) == ('fast', 'goal_driven', 10)
