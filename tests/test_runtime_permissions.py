import hashlib
import json
import pytest
from briefloop import runtime_permissions as permissions
from briefloop.bridge_harness import normalize_bridge_usage
from briefloop.models import Settings


def test_scoped_native_rule_preserves_other_settings_and_rejects_stale_write(tmp_path, monkeypatch):
    path=tmp_path/'settings.json'
    original={'auth':{'fixture':'never-return-this'},'permissions':{'deny':['command(rm *)']},'theme':'dark'}
    path.write_text(json.dumps(original));monkeypatch.setattr(permissions,'antigravity_settings',lambda:path)
    before=path.read_bytes();view=permissions.catalog('antigravity',tmp_path,None)
    assert 'auth' not in view and 'never-return-this' not in json.dumps(view)
    rule='read_file('+str(tmp_path/'fixture.txt')+')'
    permissions.change_antigravity({'revision':view['revision'],'decision':'allow','rule':rule,'operation':'add'})
    result=json.loads(path.read_text());assert result['auth']==original['auth']
    assert result['permissions']['deny']==original['permissions']['deny']
    assert result['permissions']['allow']==[rule]
    with pytest.raises(ValueError,match='已变化'):
        permissions.change_antigravity({'revision':hashlib.sha256(before).hexdigest(),'decision':'allow','rule':'command(*)','operation':'add'})
    revision=permissions.catalog('antigravity',tmp_path,None)['revision']
    permissions.change_antigravity({'revision':revision,'decision':'allow','rule':rule,'operation':'remove'})
    assert not json.loads(path.read_text())['permissions']['allow']


def test_invalid_modes_and_symlink_never_change_native_settings(tmp_path, monkeypatch):
    for backend,mode in [('pi','bypass'),('claude','bypassPermissions'),('codex','read'),('antigravity','yolo')]:
        with pytest.raises(ValueError):permissions.validate_options(backend,{'mode':mode})
    target=tmp_path/'target';target.write_text('{}');link=tmp_path/'link';link.symlink_to(target)
    monkeypatch.setattr(permissions,'antigravity_settings',lambda:link)
    with pytest.raises(ValueError,match='符号链接'):permissions.catalog('antigravity',tmp_path,None)
    assert target.read_text()=='{}'


def test_pi_usage_includes_cached_prompt_without_double_counting_other_hosts():
    raw={'input':164,'output':52,'cacheRead':8000,'cacheWrite':300,'model_context_window':1000000}
    usage=normalize_bridge_usage(raw,'pi')
    assert usage['last']=={'inputTokens':8464,'outputTokens':52,'cachedInputTokens':8000}
    assert normalize_bridge_usage({'prompt_tokens':8464,'prompt_cache_hit_tokens':8000},'codebuddy')['last']['inputTokens']==8464
    assert usage['modelContextWindow']==1000000
    assert usage['raw']==raw
    assert Settings().chat_allow_web is True
    assert Settings(chat_allow_web=False).chat_allow_web is False


def test_queued_antigravity_turn_refuses_changed_native_policy(tmp_path,monkeypatch):
    from briefloop.store import Store
    from briefloop.bridge_harness import BridgeHarness
    from test_bridge_harness import BridgeFixture
    path=tmp_path/'native.json';path.write_text('{}')
    monkeypatch.setattr(permissions,'antigravity_settings',lambda:path)
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path/'workspace'),bridge,'antigravity')
    h._schedule=lambda sid:None
    sid=h.create_session('frozen permissions',{'model':'default'})['id']
    h.send(sid,'read fixture',message_id='frozen',allow_web=False)
    path.write_text('{"permissions":{"allow":["read_file(/fixture)"]}}')
    h._dispatch(sid,0)
    snap=h.snapshot(sid)
    assert not bridge.starts
    assert snap['messages'][0]['status']=='failed'
    assert not snap['messages'][0]['allow_web']
    assert any('原生权限已变化' in e['data'].get('message','') for e in snap['events'])


def test_permission_answers_use_native_ids_not_ambiguous_labels(tmp_path):
    from briefloop.store import Store
    from briefloop.bridge_harness import BridgeHarness
    from test_bridge_harness import BridgeFixture
    bridge=BridgeFixture();h=BridgeHarness(Store(tmp_path),bridge,'claude')
    sid=h.create_session('permissions',{'model':'default'})['id'];sent=[]
    bridge.call=lambda method,params,**kwargs:sent.append((method,params)) or {}
    rid=h.chat.add_request(sid,{'execution_id':'run','request_id':'native-request'},
        {'questions':[{'id':'permission'}],'native_options':[{'optionId':'once','name':'允许'},{'optionId':'always','name':'允许'}]})
    with pytest.raises(ValueError,match='明确'):
        h.answer(sid,rid,{'permission':{'answers':['允许']}})
    assert not sent
    h.answer(sid,rid,{'permission':{'answers':['once']}})
    assert sent[-1][1]['option_id']=='once'


def test_antigravity_presets_preserve_rules_and_change_queue_digest(tmp_path,monkeypatch):
    path=tmp_path/'settings.json'
    original={'permissions':{'deny':['read_file(/private)']},'unrelated':'keep','enableTerminalSandbox':True}
    path.write_text(json.dumps(original));monkeypatch.setattr(permissions,'antigravity_settings',lambda:path)
    before=permissions.permission_digest()
    for preset in ('full-machine','turbo','default'):
        view=permissions.catalog('antigravity',tmp_path,None)
        permissions.change_antigravity({'revision':view['revision'],'operation':'preset','preset':preset})
        current=json.loads(path.read_text())
        assert current['permissions']==original['permissions']
        assert current['unrelated']=='keep'
        assert current['enableTerminalSandbox'] is True
        assert permissions.catalog('antigravity',tmp_path,None)['preset']==preset
        if preset!='default':assert permissions.permission_digest()!=before
    assert permissions.permission_digest()==before
