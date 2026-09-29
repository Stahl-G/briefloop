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
    dispatched = workspace_action(store, {'action': 'generate',
                                          'requirements': {'title': 't', 'objective': 'o'},
                                          'source_ids': [source['id']],
                                          'runtime': {'model': 'opencode-go/gpt-5.6-luna',
                                                      'agent_backend': 'opencode'}})
    assert json.loads(store.one('jobs', dispatched['job_id'])['payload'])['agent_backend'] == 'opencode'
