import threading

import pytest

from briefloop.bridge_harness import BridgeHarness
from briefloop.chat_dispatch import ChatDispatcher
from briefloop.harness import HarnessManager
from briefloop.store import Store
from test_bridge_harness import BridgeFixture
from test_harness import RPC, until


def test_actual_driver_queue_switch_back_and_cancel_owner(tmp_path):
    store = Store(tmp_path)
    codex = HarnessManager(store, RPC)
    bridge = BridgeFixture()
    buddy = BridgeHarness(store, bridge, 'codebuddy')
    dispatch = ChatDispatcher({'codex': codex, 'codebuddy': buddy})
    sid = codex.create_session(runtime={'model': 'a', 'permission': 'read-only'})['id']
    codex.send(sid, '记住 17 件', message_id='a1')
    until(lambda: codex.chat.session(sid)['turn_id'] == 'turn1')
    # New host defaults must not inherit Codex's unsupported read-only permission.
    buddy.send(sid, '接着处理', runtime={'backend': 'codebuddy', 'model': 'b'}, message_id='b1')
    codex.send(sid, '回切，结合 B 的回答', runtime={'backend': 'codex', 'model': 'a'}, message_id='a2')
    assert dispatch.select(sid=sid) is codex
    assert not bridge.starts
    with pytest.raises(ValueError, match='追加'):
        buddy.send(sid, '不能中途切', mode='steer', runtime={'backend': 'codebuddy'})
    codex.handle_notification({'method': 'item/completed', 'params': {'threadId': 't1', 'turnId': 'turn1',
                              'item': {'type': 'agentMessage', 'id': 'answer-a', 'text': '订单 17 件'}}})
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'turn1', 'status': 'completed'}}})
    until(lambda: codex.chat.session(sid)['turn_id'] == 'turn2')
    assert len(bridge.starts) == 1
    assert 'session_id' not in bridge.starts[0]
    assert '订单 17 件' in bridge.starts[0]['prompt']
    turns = [p for method, p in codex.client.calls if method == 'turn/start']
    assert turns[-1]['threadId'] == 't2'
    assert 'visible answer' in turns[-1]['input'][0]['text']
    assert 'weighing options' not in turns[-1]['input'][0]['text']
    # Late old Codex completion cannot terminate the newly active A segment.
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'turn1', 'status': 'completed'}}})
    assert codex.chat.session(sid)['turn_id'] == 'turn2'
    codex.handle_notification({'method': 'turn/completed', 'params': {'threadId': 't2', 'turn': {'id': 'old-turn', 'status': 'completed'}}})
    assert codex.chat.session(sid)['turn_id'] == 'turn2'
    codex.handle_notification({'method': 'item/completed', 'params': {'threadId': 't2', 'turnId': 'turn2',
                              'item': {'type': 'agentMessage', 'id': 'answer-a', 'text': 'New answer'}}})
    answers = [m['text'] for m in codex.snapshot(sid)['messages'] if m['role'] == 'assistant']
    assert '订单 17 件' in answers and 'New answer' in answers
    buddy.send(sid, '排队 B', runtime={'backend': 'codebuddy'}, message_id='b2')
    dispatch.select(sid=sid).cancel(sid)
    assert codex.client.calls[-1] == ('turn/interrupt', {'threadId': 't2', 'turnId': 'turn2'})
    assert next(m for m in codex.snapshot(sid)['messages'] if m['id'] == 'b2')['status'] == 'cancelled'
    assert len(bridge.starts) == 1
    codex.close()


def test_cancel_before_native_dispatch_and_restart_do_not_replay(tmp_path):
    codex = HarnessManager(Store(tmp_path), RPC)
    dispatch = ChatDispatcher({'codex': codex})
    ready, release = threading.Event(), threading.Event()
    original = codex._dispatch

    def delayed(sid):
        ready.set()
        assert release.wait(3)
        original(sid)

    codex._dispatch = delayed
    sid = codex.create_session()['id']
    codex.send(sid, 'must not reach native host', message_id='cancel-before-start')
    assert ready.wait(3)
    codex.cancel(sid)
    release.set()
    until(lambda: codex.chat.session(sid)['status'] == 'interrupted')
    assert codex.client is None
    # Emulate a process loss immediately after durable admission.
    message = codex.chat.message(sid, 'pending admission', mid='crash', runtime={'backend': 'codex', 'model': 'a'})
    assert dispatch.execution.admit(sid, message['id'])
    codex.chat.recover_stale()
    dispatch.recover()
    assert next(m for m in codex.snapshot(sid)['messages'] if m['id'] == 'crash')['status'] == 'interrupted'
    assert not codex.chat.session(sid)['busy']


def test_saved_report_chat_can_research_and_queue_new_version_evidence(tmp_path, monkeypatch):
    import json
    from briefloop import research_plan, sources
    from briefloop.chat_tools import workspace_action
    from briefloop.document_model import markdown_document
    from briefloop.report_revision import bind_context

    store = Store(tmp_path)
    store.update_settings({'model': 'a', 'model_selection_required': False, 'auto_revision': False, 'auto_learn': False})
    original = store.add_source('Original', 'Revenue 12 USD.')
    run = store.create_run({'title': 'Weekly', 'objective': 'Explain changes', 'allow_web': True,
                            'fact_check': False, 'completion_mode': 'direct'}, [original['id']], research_protocol='quality_v1')
    generation = store.enqueue('generate', {'run_id': run['id']})
    brief = store.publish(run['id'], {'title': 'Weekly', 'markdown': f'Revenue 12 USD. [@{original["id"]}]',
                                    'citations': [{'source_id': original['id']}]},
                          version_id='brief_' + generation['id'][4:])
    store.update_job(generation['id'], 'complete')
    research_plan.freeze(store, run['id'], structure={'breadth': 1, 'depth': 1, 'parallel': 1})
    research_plan.finish_round(store, run['id'])
    closed_rounds = research_plan.frozen(store, run['id'])['rounds']
    codex = HarnessManager(store, RPC)
    ChatDispatcher({'codex': codex})
    runtime = {'model': 'a', 'effort': 'medium', 'permission': 'workspace-write'}
    sid = codex.create_session(runtime=runtime)['id']
    bind_context(store, sid, 'feedback', {'version_id': brief['id'], 'hash': brief['hash']})
    with pytest.raises(ValueError):
        bind_context(store, sid, 'feedback', None)
    codex.send(sid, 'Find missing developments and revise this report.', message_id='feedback', allow_web=True)
    until(lambda: codex.chat.session(sid)['turn_id'] == 'turn1')
    from briefloop.draft_completion import binding
    old_check = store.enqueue('assess', {'version_id': brief['id'], 'run_id': run['id'],
        'continuation_of': generation['id'], 'checks_input': binding(store, brief['id'])})
    store.update_job(old_check['id'], 'running')
    bind_context(store, sid, 'same-report', {'version_id': brief['id'], 'hash': brief['hash']})
    codex.send(sid, 'Also retain the important comparisons.', message_id='same-report', mode='steer', allow_web=True)
    until(lambda: any(method == 'turn/steer' for method, _ in codex.client.calls))
    delivered = next(params for method, params in codex.client.calls if method == 'turn/steer')
    assert brief['id'] in delivered['input'][0]['text']
    bind_context(store, sid, 'different-report', None)
    with pytest.raises(ValueError, match='报告'):
        codex.send(sid, 'Unrelated message', message_id='different-report', mode='steer', allow_web=True)
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *_args, **_kwargs: (b'Revenue 15 USD.', 'text/plain', 'utf-8'))
    added = sources.fetch_for_run(store, run['id'], 'https://example.test/new')
    # Tool ingestion really passed closed-round admission and retained the new source.
    assert added['id'] in store.source_ids(run['id'])
    document = tmp_path / 'revision.json'
    document.write_text(json.dumps(markdown_document(f'Revenue 15 USD. [@{added["id"]}]')))
    revised = workspace_action(store, {'action': 'revise_document', 'base_version': brief['id'],
                                       'document_file': str(document), 'citations': [{'source_id': added['id']}]})
    assert json.loads(revised['detail'])['evidence_needs_review']
    codex.handle_notification({'method': 'item/completed', 'params': {'threadId': 't1', 'turnId': 'turn1',
                              'item': {'type': 'agentMessage', 'id': 'answer', 'text': 'Saved revised report.'}}})
    assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'active'
    completion = {'method': 'turn/completed', 'params': {'threadId': 't1', 'turn': {'id': 'turn1', 'status': 'completed'}}}
    codex.handle_notification(completion)
    codex.handle_notification(completion)
    assert len(store.rows("SELECT * FROM jobs WHERE kind='assess'")) == 1
    from briefloop.runtime import Worker
    Worker(store)._settle_job(old_check['id'], 'complete')
    checks = store.rows("SELECT * FROM jobs WHERE kind='assess' AND id!=?", (old_check['id'],))
    assert len(checks) == 1
    payload = json.loads(checks[0]['payload'])
    assert payload['version_id'] == revised['id'] and payload['auto_revision'] is False
    assert payload['runtime']['model'] == 'a'
    assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'completed'
    assert research_plan.frozen(store, run['id'])['rounds'] == closed_rounds
    assert store.one('briefs', brief['id'])['markdown'] == brief['markdown']
    codex.close()

    # A read-only discussion must not fund checks for another author's concurrent edit.
    store.update_job(checks[0]['id'], 'cancelled')
    readonly = codex.chat.create('Read only', {'model': 'a', 'permission': 'read-only'}, tmp_path)['id']
    question = codex.chat.message(readonly, 'Explain the report.', status='sending', runtime={'permission': 'read-only'})
    bind_context(store, readonly, question['id'], {'version_id': revised['id'], 'hash': revised['hash']})
    newer = store.revise(revised['id'], revised['markdown'] + '\n\nAnother author added a conclusion.',
                         allow_markdown_conversion=True, author='agent')
    count = len(store.rows("SELECT * FROM jobs WHERE kind='assess'"))
    codex.chat.patch_message(question['id'], status='completed')
    assert len(store.rows("SELECT * FROM jobs WHERE kind='assess'")) == count
    revised = store.brief_view(newer['id'])

    # Both alternate transports must admit the report before preparing its input.
    from briefloop.native_harness import NativeHarness
    from test_native_harness import EngineFixture, _wait_status
    def wait_report_closeout(message_id):
        until(lambda: store.rows("""SELECT 1 FROM chat_events WHERE kind='revision/message_settled'
            AND json_extract(data,'$.message_id')=?""", (message_id,)))
    class InspectEngine(EngineFixture):
        def call(self, method, params, timeout=None):
            if method == 'turn_start':
                assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'active'
            return super().call(method, params, timeout)
    class InspectBridge(BridgeFixture):
        def call(self, method, params, timeout=None):
            if method == 'start':
                assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'active'
            return super().call(method, params, timeout)
    for host, config in ((NativeHarness(store, InspectEngine()), {'model': 'fake/m1'}),
                         (BridgeHarness(store, InspectBridge(), 'codebuddy'), {'model': 'default'})):
        dispatcher = ChatDispatcher({host.backend: host})
        session = host.create_session(runtime=config)['id']
        bind_context(store, session, session, {'version_id': revised['id'], 'hash': revised['hash']})
        host.send(session, 'Check this report.', message_id=session, allow_web=True)
        _wait_status(host, session, session, 'completed')
        wait_report_closeout(session)
        assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'completed'
        prepare = dispatcher.input
        def broken_input(sid, message):
            prepare(sid, message)
            raise PermissionError('Synthetic input preparation failure')
        monkeypatch.setattr(dispatcher, 'input', broken_input)
        failed_id = session + '-input-failure'
        bind_context(store, session, failed_id, {'version_id': revised['id'], 'hash': revised['hash']})
        host.send(session, 'Prepare report input.', message_id=failed_id, allow_web=True)
        _wait_status(host, session, failed_id, 'failed')
        wait_report_closeout(failed_id)
        assert not host.chat.session(session)['busy']
        assert research_plan.frozen(store, run['id'])['revision_research']['status'] == 'failed'
        host.close()
