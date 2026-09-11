"""Background tasks report their start and end into the conversation that began them."""
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


def test_task_without_session_does_not_guess_a_conversation(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    other = chat.create('另一个对话', {}, tmp_path)
    store.enqueue('generate', {'run_id': 'r1'})
    assert _notes(chat, other['id']) == []
