"""Message-level chat actions: revert truncates, fork copies history."""
from briefloop.chat_store import ChatStore
from briefloop.store import Store


def _seed(chat):
    session = chat.create('对话', {'model': 'x', 'backend': 'codex'}, '/tmp')
    first = chat.message(session['id'], '你好', role='user', status='completed')
    reply = chat.message(session['id'], '你好，我是 BriefLoop', role='assistant', status='completed')
    third = chat.message(session['id'], '第二个问题', role='user', status='completed')
    return session, first, reply, third


def test_truncate_drops_a_message_and_everything_after(tmp_path):
    chat = ChatStore(Store(tmp_path))
    session, _, reply, _ = _seed(chat)
    chat.truncate(session['id'], reply['id'])
    assert [m['text'] for m in chat.snapshot(session['id'])['messages']] == ['你好']


def test_fork_copies_history_and_links_parent(tmp_path):
    store = Store(tmp_path)
    chat = ChatStore(store)
    session, first, reply, _ = _seed(chat)
    forked = chat.fork(session['id'], reply['id'], title='分支')
    snapshot = chat.snapshot(forked['id'])
    assert [m['text'] for m in snapshot['messages']] == ['你好', '你好，我是 BriefLoop']
    assert all(m['id'] not in (first['id'], reply['id']) for m in snapshot['messages'])
    events = store.rows('SELECT kind,data FROM chat_events WHERE session_id=?', (forked['id'],))
    assert any(e['kind'] == 'session/forked' and 'parent_session_id' in e['data'] for e in events)
