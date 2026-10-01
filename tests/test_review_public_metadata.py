"""Synthetic host receipts: privacy, recovery and existing-workspace migration."""
import json
import pytest

from briefloop.execution_records import public_tool_name
from briefloop.plain_isolation import tool_uses
from briefloop.progress import AUTH, CONNECTION, HOST_PERMISSION, MODEL, QUOTA, ProgressTracker, public_failure
from briefloop.review import enqueue_review, get_review, review_status, run_review
from briefloop.review_capability import review_route
from briefloop.store import Store, dump


@pytest.mark.parametrize('message', [CONNECTION, AUTH, MODEL, HOST_PERMISSION,
    QUOTA.format(reset=''), QUOTA.format(reset='，约 3 小时 29 分钟后恢复')])
def test_fixed_errors_are_idempotent_but_appended_private_text_is_never_public(tmp_path, message):
    assert public_failure(message) == message
    secret = 'https://private.invalid/SYNTHETIC_PRIVATE_PATH'
    store = Store(tmp_path / 'workspace')
    folder = store.root / 'jobs' / 'error-proof'; folder.mkdir(parents=True)
    (folder / 'events.jsonl').write_text(dump({'type': 'error', 'data': {'message': message + ' ' + secret}}) + '\n')
    tracker = ProgressTracker(store, 'error-proof', folder)
    tracker.update()
    assert 'SYNTHETIC_PRIVATE' not in tracker.failure_message
    raw = store.rows("SELECT data FROM events WHERE kind='runtime_progress'")[-1]['data']
    assert 'SYNTHETIC_PRIVATE' not in raw and 'https://' not in raw


@pytest.mark.parametrize('title', ['Read SYNTHETIC_PRIVATE_PATH', 'ReadSYNTHETIC_PRIVATE_PATH',
    'curl -H Authorization: Bearer SYNTHETIC_PRIVATE_CREDENTIAL https://private.invalid/path'])
def test_free_form_tool_titles_are_not_public_identifiers(tmp_path, title):
    assert public_tool_name('Read') == 'Read'
    assert public_tool_name('search_web') == 'search_web'
    assert public_tool_name(title) == '其他宿主工具'
    (tmp_path / 'events.jsonl').write_text(dump({'data': {'item': {
        'id': 'one', 'type': 'runtime_tool', 'tool': title}}}) + '\n')
    assert tool_uses(tmp_path) == ['其他宿主工具']


@pytest.mark.parametrize('interrupted', [False, True])
def test_observed_review_captures_safe_tool_evidence_on_live_and_cached_admission(tmp_path, interrupted):
    store = Store(tmp_path)
    store.update_settings({'agent_backend': 'claude', 'model': 'sonnet', 'auto_learn': False})
    source = store.add_source('Synthetic', 'Revenue 12 million USD.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Read data', 'fact_check': False}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'Revenue 12 million USD.'})
    job = enqueue_review(store, brief['id']); folder = store.root / 'jobs' / job['id']
    private_title = 'Read /private/SYNTHETIC_PRIVATE_PATH'
    class Runtime:
        calls = 0
        def execute(self, stage, prompt, folder, **kwargs):
            self.calls += 1
            index = json.loads((folder / 'packet/index.json').read_text())
            (folder / 'events.jsonl').write_text('\n'.join(dump({'data': {'item': {
                'id': str(i), 'type': 'runtime_tool', 'tool': name}}}) for i, name in enumerate(['Read', private_title])) + '\n')
            (folder / 'review.json').write_text(dump({'version_id': brief['id'], 'fingerprint': index['fingerprint'],
                'status': 'incomplete', 'summary': 'Synthetic fixture only',
                'unchecked_items': [{'description': 'No semantic model review', 'importance': 'core'}]}))
            if interrupted:
                raise InterruptedError('Synthetic interruption after output')
            return {'returncode': 0}
    runtime = Runtime()
    if interrupted:
        with pytest.raises(InterruptedError):
            run_review(store, runtime, job, brief['id'], folder)
    reviewed = run_review(store, runtime, job, brief['id'], folder)
    assert runtime.calls == 1
    assert reviewed['data']['host_tools'] == ['Read', '其他宿主工具']
    public = review_status(store, brief['id'])['reviews'][0]
    assert public['host_tools'] == ['Read', '其他宿主工具']
    assert 'SYNTHETIC_PRIVATE' not in dump(public)
    # Historical metadata from the earlier implementation is filtered on read too.
    data = {**get_review(store, reviewed['id'])['data'], 'host_tools': [private_title]}
    with store.tx() as connection:
        connection.execute('UPDATE reviews SET data=? WHERE id=?', (dump(data), reviewed['id']))
    assert review_status(store, brief['id'])['reviews'][0]['host_tools'] == ['其他宿主工具']


def test_legacy_role_override_is_bound_before_switch_or_chat_job(tmp_path):
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'codex', 'model': 'main-codex',
        'role_models': {'evaluator': {'model': 'saved-codex-evaluator'}}})
    assert store.role_model_config({'model': 'chat-claude'}, 'claude')['evaluator'] == {'model': 'chat-claude'}
    store.update_settings({'agent_backend': 'claude', 'model': 'main-claude'})
    assert store.role_model_config()['evaluator']['model'] == 'main-claude'
    assert store.settings()['role_models']['evaluator']['backend'] == 'codex'
    store.update_settings({'agent_backend': 'codex', 'model': 'main-codex'})
    assert store.role_model_config()['evaluator']['model'] == 'saved-codex-evaluator'
    store.update_settings({'agent_backend': 'claude', 'model': 'main-claude',
        'role_models': {'evaluator': {'model': 'new-claude-evaluator'}}})
    assert store.settings()['role_models']['evaluator']['backend'] == 'claude'
    assert store.role_model_config()['evaluator']['model'] == 'new-claude-evaluator'


def test_bridge_review_route_preserves_effort_field_used_by_frontend():
    assert review_route('codex', {'backend': 'claude', 'model': 'sonnet', 'reasoning_effort': 'high'}) == (
        'claude', {'model': 'sonnet', 'reasoning_effort': 'high'})


def test_historical_plain_metadata_is_normalized_without_changing_bound_brief(tmp_path):
    from briefloop.deliverable_spec import research_record
    from briefloop.task_progress import summary as progress_summary
    store = Store(tmp_path)
    store.update_settings({'model': 'synthetic-model'})
    source = store.add_source('Synthetic', 'Revenue 12 million USD.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Read data', 'fact_check': False}, [source['id']])
    private = 'curl -H Authorization: Bearer SYNTHETIC_PRIVATE_CREDENTIAL https://private.invalid/path'
    note = {'kind': 'fast_isolation', 'backend': 'claude', 'level': 'observed',
            'tools': [private], 'summary': private}
    brief = store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'Revenue 12 million USD.',
                                     'research_notes': [note]})
    job = store.enqueue('generate', {'run_id': run['id']})
    store.event(job['id'], 'plain_isolation', {**note, 'message': private})
    public = research_record(store, brief)
    assert public['notes'][0]['tools'] == ['其他宿主工具']
    assert 'SYNTHETIC_PRIVATE' not in dump(public)
    view = store.brief_view(brief['id'])
    assert view['hash'] == brief['hash']
    assert 'SYNTHETIC_PRIVATE' not in view['detail']
    timeline = progress_summary(store, job['id'])['timeline']
    assert 'SYNTHETIC_PRIVATE' not in dump(timeline)
    assert '其他宿主工具' in dump(timeline)
    assert store.one('briefs', brief['id'])['detail'] == brief['detail']


def test_bridge_activity_label_does_not_copy_host_title(tmp_path):
    from briefloop.bridge_harness import BridgeHarness
    from test_bridge_harness import BridgeFixture, _wait_status
    class TitledBridge(BridgeFixture):
        def call(self, method, params, timeout=None):
            if method != 'start':
                return super().call(method, params, timeout)
            for event in [
                {'kind': 'tool', 'id': 'one', 'name': 'Read /private/SYNTHETIC_PRIVATE_PATH', 'status': 'completed'},
                {'kind': 'text', 'text': 'Synthetic result'}, {'kind': 'end', 'status': 'completed'}]:
                self.sinks[params['execution_id']].put(event)
            return {'execution_id': params['execution_id']}
    harness = BridgeHarness(Store(tmp_path), TitledBridge(), 'claude')
    run = harness.start_internal('Synthetic tool title fixture')
    snapshot = _wait_status(harness, run.session_id, run.message_id, 'completed')
    event = next(item for item in snapshot['events'] if item['kind'] == 'item/completed')
    assert event['data']['item']['tool'] == '其他宿主工具'
    assert 'SYNTHETIC_PRIVATE' not in dump(event)
    # Replaying records produced before this fix has the same compact boundary.
    private_title = 'Read /private/SYNTHETIC_PRIVATE_PATH'
    harness.chat.event(run.session_id, 'item/completed', {'item': {
        'id': 'historical', 'type': 'runtime_tool', 'tool': private_title}})
    historical = next(event for event in harness.snapshot(run.session_id)['events']
                      if event['data'].get('item', {}).get('id') == 'historical')
    assert historical['data']['item']['tool'] == '其他宿主工具'
    assert 'SYNTHETIC_PRIVATE' not in dump(historical)
    stored = next(event for event in harness.chat.snapshot(run.session_id, private=True)['events']
                  if event['data'].get('item', {}).get('id') == 'historical')
    assert stored['data']['item']['tool'] == private_title
