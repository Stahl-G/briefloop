"""Connection tests stay accessible without crowding normal conversations."""
import pytest
from briefloop.chat_store import ChatStore
from briefloop.store import Store
from briefloop.harness import HarnessManager
from test_harness import RPC, until


@pytest.mark.parametrize('with_message_id',[False,True])
def test_probe_message_is_identified_without_reclassifying_user_offline_choices(tmp_path,with_message_id):
    chat=ChatStore(Store(tmp_path))
    sid=chat.create('连接测试',{},tmp_path)['id']
    probe=chat.message(sid,'Reply with OK only. Do not use tools.',allow_web=False)
    chat.event(sid,'runtime/test',{'kind':'short_model_call',**({'message_id':probe['id']} if with_message_id else {})})
    user=chat.message(sid,'只用已有材料',allow_web=False)
    snapshot=chat.snapshot(sid,after=999)
    assert snapshot['messages'][0]['purpose']=='runtime_test'
    assert snapshot['messages'][1]['id']==user['id']
    assert 'purpose' not in snapshot['messages'][1]
    assert snapshot['messages'][1]['allow_web']==0


def test_purpose_filter_preserves_errors_and_archive_restore(tmp_path):
    chat=ChatStore(Store(tmp_path))
    normal=chat.create('测试产品方案',{},tmp_path)['id']
    test=chat.create('连接测试',{},tmp_path)['id']
    chat.event(test,'runtime/test',{'kind':'short_model_call'})
    chat.event(test,'error',{'message':'HTTP 503: provider unavailable'})
    chat.update(test,status='failed')
    assert [s['id'] for s in chat.sessions()]==[normal]
    assert [s['id'] for s in chat.sessions('tests')]==[test]
    assert chat.snapshot(test)['events'][-1]['data']['message'].startswith('HTTP 503')
    chat.set_lifecycle(test,'archived')
    assert not chat.sessions('tests')
    assert [s['id'] for s in chat.sessions('archived')]==[test]
    chat.set_lifecycle(test,'active')
    assert [s['id'] for s in chat.sessions('tests')]==[test]
    assert [s['id'] for s in chat.sessions()]==[normal]


def test_filtered_running_test_remains_busy_and_cancellable(tmp_path):
    manager=HarnessManager(Store(tmp_path),RPC)
    sid=manager.create_session()['id']
    manager.chat.event(sid,'runtime/test',{'kind':'short_model_call'})
    try:
        manager.send(sid,'connection check')
        until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
        assert not manager.list_sessions()
        assert manager.list_sessions('tests')[0]['busy'] is True
        with pytest.raises(ValueError):manager.archive(sid)
        manager.cancel(sid)
        assert any(method=='turn/interrupt' for method,_ in manager.client.calls)
        manager.handle_notification({'method':'turn/completed','params':{
            'threadId':'t1','turn':{'id':'turn1','status':'interrupted'}}})
        until(lambda:not manager.snapshot(sid)['session']['busy'])
        assert manager.snapshot(sid)['messages']
        assert [s['id'] for s in manager.list_sessions('tests')]==[sid]
    finally:manager.close()
