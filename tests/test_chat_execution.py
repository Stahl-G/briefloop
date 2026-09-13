from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from briefloop.chat_execution import ChatExecution
from briefloop.chat_store import ChatStore
from briefloop.store import Store


def setup_chat(tmp_path):
    chat = ChatStore(Store(tmp_path))
    sid = chat.create('交接检查', {'backend': 'opencode', 'model': 'a'}, tmp_path)['id']
    return chat, ChatExecution(chat), sid


def queue(chat, sid, mid, backend, text, model='a'):
    return chat.message(sid, text, mid=mid, runtime={'backend': backend, 'model': model})


def finish(chat, sid, mid, text):
    chat.patch_message(mid, status='completed')
    response = chat.message(sid, text, role='assistant', status='completed', turn_id=mid)
    chat.update(sid, status='idle', turn_id=None)
    return response


def test_queue_switch_back_native_ownership_and_durable_public_history(tmp_path):
    chat, execution, sid = setup_chat(tmp_path)
    queue(chat, sid, 'a1', 'opencode', '材料：订单是 17 件')
    first = execution.admit(sid, 'a1')
    assert execution.bind(sid, 'a1', 'native-a')
    queue(chat, sid, 'b1', 'codebuddy', '按前面的订单数继续')
    queue(chat, sid, 'a2', 'opencode', '回到 A，使用 B 的结论')
    assert execution.admit(sid, 'b1') is None  # Still reserved by A.
    assert chat.session(sid)['runtime']['backend'] == 'opencode'
    reply = finish(chat, sid, 'a1', '已记住订单 17 件')
    chat.patch_message(reply['id'], reasoning='HIDDEN_REASONING')
    with chat.store.tx() as c:
        c.execute('UPDATE chat_messages SET prompt=? WHERE id=?', ('PRIVATE_SYSTEM_ENVELOPE', 'a1'))
    assert execution.admit(sid, 'a2') is None  # FIFO, not merely idle.
    second = execution.admit(sid, 'b1')
    assert second['id'] != first['id'] and second['send_handoff']
    assert chat.session(sid)['thread_id'] is None
    assert '订单 17 件' in second['handoff']
    assert 'HIDDEN_REASONING' not in second['handoff']
    assert 'PRIVATE_SYSTEM_ENVELOPE' not in second['handoff']
    assert '回到 A' not in second['handoff']
    assert execution.bind(sid, 'a1', 'late-native-a') is False
    assert execution.bind(sid, 'b1', 'native-b')
    finish(chat, sid, 'b1', '交付分两批：9 件与 8 件')
    # Reopen the journal: native ownership and handoff must not depend on RAM.
    execution = ChatExecution(ChatStore(Store(tmp_path)))
    third = execution.admit(sid, 'a2')
    assert third['id'] not in (first['id'], second['id'])
    assert third['native_session_id'] is None
    assert '订单 17 件' in third['handoff'] and '9 件与 8 件' in third['handoff']
    assert execution.bind(sid, 'b1', 'late-native-b') is False
    assert execution.bind(sid, 'a2', 'fresh-native-a')
    assert [s['native_session_id'] for s in execution.segments(sid)] == ['native-a', 'native-b', 'fresh-native-a']
    assert execution.admit(sid, 'a2') is None
    assert len([e for e in chat.snapshot(sid)['events'] if e['kind'] == 'runtime/switch']) == 2


def test_atomic_admission_frozen_tasks_and_handoff_failure_preserve_queue(tmp_path):
    chat, execution, sid = setup_chat(tmp_path)
    queue(chat, sid, 'one', 'opencode', 'hello')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execution.admit(sid, 'one'), range(2)))
    assert sum(result is not None for result in results) == 1
    finish(chat, sid, 'one', '材料分析' * 50)
    queue(chat, sid, 'two', 'codebuddy', 'switch')
    with pytest.raises(ValueError, match='历史过大'):
        execution.admit(sid, 'two', max_handoff_bytes=10)
    assert len(execution.segments(sid)) == 1
    assert chat.session(sid)['runtime']['backend'] == 'opencode'
    assert chat.snapshot(sid)['messages'][-1]['status'] == 'queued'
    chat.event(sid, 'session/internal', {})
    with pytest.raises(ValueError, match='冻结宿主'):
        execution.admit(sid, 'two')
    assert chat.snapshot(sid)['messages'][-1]['status'] == 'queued'


def test_same_host_resume_and_model_change_reseed_without_credentials(tmp_path):
    chat, execution, sid = setup_chat(tmp_path)
    queue(chat, sid, 'one', 'opencode', 'API_KEY="fake-secret-for-test"')
    first = execution.admit(sid, 'one')
    execution.bind(sid, 'one', 'native-a')
    finish(chat, sid, 'one', '完成第一轮')
    queue(chat, sid, 'two', 'opencode', 'same model')
    second = execution.admit(sid, 'two')
    assert second['id'] == first['id'] and second['native_session_id'] == 'native-a'
    assert second['send_handoff'] is False
    finish(chat, sid, 'two', '完成第二轮')
    queue(chat, sid, 'three', 'opencode', 'new model', model='different')
    third = execution.admit(sid, 'three')
    assert third['id'] != first['id'] and third['native_session_id'] is None
    assert 'fake-secret-for-test' not in third['handoff']
    assert '[credential omitted]' in third['handoff']
    payload = json.loads(third['handoff'].split('\n', 1)[1])
    assert [m['role'] for m in payload['conversation_history']] == ['user', 'assistant', 'user', 'assistant']
