"""Background task notes: owned conversation, one per attempt, no global side effects."""
import json

from briefloop import task_notify
from briefloop.chat_store import ChatStore
from briefloop.store import Store, dump


def _notes(chat, session_id):
    return [m for m in chat.snapshot(session_id)['messages'] if m['mode'] == 'notice']


def test_note_does_not_disturb_another_running_session(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    a = chat.create('A', {'model': 'x', 'backend': 'codex'}, tmp_path)
    b = chat.create('B', {'model': 'x', 'backend': 'codex'}, tmp_path)
    chat.update(b['id'], status='running', turn_id='native-turn')
    chat.message(b['id'], 'reply', role='assistant', status='streaming', turn_id='native-turn')
    chat.add_request(b['id'], {'id': 1}, {'method': 'x'})
    store.enqueue('audit_bundle', {'session_id': a['id']})
    session = chat.session(b['id'])
    assert session['status'] == 'running' and session['turn_id'] == 'native-turn' and session['busy'] is True
    assert chat.snapshot(b['id'])['messages'][0]['status'] == 'streaming'
    assert chat.snapshot(b['id'])['requests'][0]['status'] == 'pending'


def test_note_requires_an_owned_session(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    other = chat.create('另一个对话', {}, tmp_path)
    store.enqueue('generate', {'run_id': 'r1'})  # no session_id: never guess a conversation
    assert _notes(chat, other['id']) == []


def test_note_dedups_per_attempt_and_notifies_a_second_failure(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    session = chat.create('对话', {}, tmp_path)
    job = store.enqueue('audit_bundle', {'session_id': session['id']})
    assert len(_notes(chat, session['id'])) == 1  # queued
    job = store.one('jobs', job['id'])
    task_notify.notify(store, job, 'failed')
    task_notify.notify(store, job, 'failed')  # replay of the same attempt
    assert len(_notes(chat, session['id'])) == 2
    payload = json.loads(job['payload']); payload['attempt'] = 2
    with store.tx() as c:
        c.execute('UPDATE jobs SET payload=? WHERE id=?', (dump(payload), job['id']))
    task_notify.notify(store, store.one('jobs', job['id']), 'failed')  # a new attempt
    assert len(_notes(chat, session['id'])) == 3


def test_worker_settle_sends_the_terminal_note(tmp_path):
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    chat = ChatStore(store)
    session = chat.create('对话', {}, tmp_path)
    job = store.enqueue('audit_bundle', {'session_id': session['id']})
    with store.tx() as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    Worker(store)._settle_job(job['id'], 'complete', result={'ok': True})
    assert any('已完成' in m['text'] for m in _notes(chat, session['id']))


def test_worker_stop_sends_the_cancelled_note(tmp_path):
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    chat = ChatStore(store)
    session = chat.create('对话', {}, tmp_path)
    job = store.enqueue('audit_bundle', {'session_id': session['id']})
    with store.tx() as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
    Worker(store).stop_job(job['id'])
    assert any('已停止' in m['text'] for m in _notes(chat, session['id']))


def test_internal_execution_sessions_are_not_listed(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    visible = chat.create('对话', {}, tmp_path)
    internal = chat.create('生成简报', {}, tmp_path)
    chat.event(internal['id'], 'session/internal', {})
    assert [s['id'] for s in chat.sessions('active')] == [visible['id']]
