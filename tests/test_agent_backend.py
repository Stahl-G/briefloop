"""Optional agent backend: workspace default, per-job freeze, transport routing."""
import json
import time
import pytest
from briefloop.backends import validate_backend, supports
from briefloop.models import Settings
from briefloop.store import Store, dump
from briefloop.runtime import Worker
from briefloop.interactive_runtime import InteractiveRuntime


def test_backend_defaults_to_codex_and_rejects_unknown(tmp_path):
    store = Store(tmp_path / 'workspace')
    assert store.settings()['agent_backend'] == 'codex'
    assert validate_backend('opencode') == 'opencode'
    with pytest.raises(ValueError):
        validate_backend('other')
    with pytest.raises(Exception):
        Settings.model_validate({'agent_backend': 'other'})
    assert supports('codex', 'steer') and supports('opencode', 'cancel')
    assert not supports('opencode', 'questions')


def test_enqueue_freezes_backend_and_old_jobs_stay_codex(tmp_path):
    store = Store(tmp_path / 'workspace')
    first = store.enqueue('generate', {'run_id': 'run-old'})
    assert json.loads(first['payload'])['agent_backend'] == 'codex'
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode',
                                'model': 'opencode-go/gpt-5.6-luna'})
    second = store.enqueue('generate', {'run_id': 'run-new'})
    frozen = json.loads(second['payload'])
    assert frozen['agent_backend'] == 'opencode'
    assert frozen['runtime'] == {'model': 'opencode-go/gpt-5.6-luna'}
    assert json.loads(store.one('jobs', first['id'])['payload'])['agent_backend'] == 'codex'
    with pytest.raises(ValueError):
        store.enqueue('generate', {'agent_backend': 'other'})
    # Codex-shaped ids cannot run on opencode; the failure names the backend.
    with pytest.raises(ValueError):
        store.enqueue('generate', {'runtime': {'model': 'gpt-5.6-luna'}})


def test_resume_across_backend_starts_new_attempt(tmp_path):
    store = Store(tmp_path / 'workspace')
    job = store.enqueue('generate', {'run_id': 'run-x'})
    store.update_job(job['id'], 'failed')
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode',
                                'model': 'opencode-go/gpt-5.6-luna'})
    resumed = Worker(store).resume(job['id'])
    assert resumed['id'] != job['id']
    assert json.loads(resumed['payload'])['agent_backend'] == 'opencode'


def test_runtime_routes_by_frozen_backend_and_pins_binding(tmp_path):
    store = Store(tmp_path / 'workspace')
    calls = []

    class FakeHarness:
        backend = 'codex'

        def create_session(self, *args, **kwargs):
            return {'id': 'chat-codex'}

        def start_internal(self, *args, **kwargs):
            calls.append(('codex', kwargs))
            raise RuntimeError('codex transport')

    class FakeOpencode:
        backend = 'opencode'

        def create_session(self, *args, **kwargs):
            return {'id': 'chat-opencode'}

        def start_internal(self, *args, **kwargs):
            calls.append(('opencode', kwargs))
            raise RuntimeError('opencode transport')

    runtime = InteractiveRuntime(store, backends={'codex': FakeHarness(), 'opencode': FakeOpencode()})
    folder = tmp_path / 'folder'
    folder.mkdir()
    codex_job = store.enqueue('generate', {'run_id': 'r1'})
    with pytest.raises(RuntimeError, match='codex transport'):
        runtime.execute(codex_job, 'prompt', folder)
    assert calls[-1][0] == 'codex'
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'opencode',
                                'model': 'opencode-go/gpt-5.6-luna'})
    opencode_job = store.enqueue('generate', {'run_id': 'r2'})
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(RuntimeError, match='opencode transport'):
        runtime.execute(opencode_job, 'prompt', other)
    assert calls[-1][0] == 'opencode'
    binding = json.loads((other / 'conversation.json').read_text())
    assert binding['backend'] == 'opencode'
    # A codex job must never resume an opencode-bound folder.
    with pytest.raises(ValueError, match='不一致|已改变'):
        runtime.execute(codex_job, 'prompt', other)


def test_legacy_codex_search_provider_migrates_to_native(tmp_path):
    from briefloop.models import normalize_search_provider
    store = Store(tmp_path / 'workspace')
    assert normalize_search_provider('codex') == 'native'
    assert normalize_search_provider(None) == 'native'
    store.set_meta('settings', {**store.settings(), 'search_provider': 'codex'})
    assert store.settings()['search_provider'] == 'native'
    job = store.enqueue('generate', {'search_provider': 'codex'})
    assert json.loads(job['payload'])['search_provider'] == 'native'


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
    from briefloop.chat_tools import workspace_action, chat_instructions
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
    text = chat_instructions(store, {'model': 'opencode-go/gpt-5.6-luna', 'backend': 'opencode'})
    assert 'task 工具' in text and 'question' in text
    codex_text = chat_instructions(store, {'model': 'gpt-5.6-luna', 'effort': 'high'})
    assert 'Codex 原生搜索' in codex_text and '沿用本机 Codex 配置' in codex_text
