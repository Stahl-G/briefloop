"""Legacy connection probes remain ordinary, recoverable conversations."""
import pytest
from briefloop.chat_store import ChatStore
from briefloop.store import Store


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
