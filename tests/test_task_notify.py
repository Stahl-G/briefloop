"""Background tasks report their start and end into the active conversation."""
from briefloop.chat_store import ChatStore
from briefloop.store import Store


def _notes(chat, session_id):
    return [m for m in chat.snapshot(session_id)['messages'] if m['mode'] == 'notice']


def test_task_reports_start_and_terminal_once(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    session = chat.create('工作对话', {'model': 'x', 'backend': 'codex'}, tmp_path)
    job = store.enqueue('generate', {'run_id': 'r1', 'session_id': session['id']})
    notes = _notes(chat, session['id'])
    assert len(notes) == 1 and '已开始任务' in notes[0]['text']
    store.update_job(job['id'], 'complete')
    notes = _notes(chat, session['id'])
    assert len(notes) == 2 and '已完成' in notes[1]['text']
    store.update_job(job['id'], 'complete')  # a repeated settle must not repeat the note
    assert len(_notes(chat, session['id'])) == 2


def test_task_without_session_reports_to_the_active_conversation(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    session = chat.create('你是谁', {}, tmp_path)
    store.enqueue('generate', {'run_id': 'r1'})  # e.g. the main agent started it
    notes = _notes(chat, session['id'])
    assert len(notes) == 1 and '已开始任务' in notes[0]['text']


def test_internal_execution_sessions_are_neither_listed_nor_targeted(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    visible = chat.create('对话', {}, tmp_path)
    internal = chat.create('生成简报', {}, tmp_path)
    chat.event(internal['id'], 'session/internal', {})
    assert [s['id'] for s in chat.sessions('active')] == [visible['id']]
    store.enqueue('audit_bundle', {})  # no session_id: must not land in the internal session
    assert _notes(chat, visible['id']) and not _notes(chat, internal['id'])
