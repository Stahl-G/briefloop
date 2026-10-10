import hashlib
import json
from pathlib import Path
import pytest
from briefloop import runtime_permissions as permissions
from briefloop.bridge_harness import normalize_bridge_usage


class ModeBridge:
    def __init__(self, result): self.result=result; self.calls=[]
    def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if isinstance(self.result, Exception):raise self.result
        return self.result


def test_permission_directory_uses_current_host_names_order_and_model():
    bridge=ModeBridge({'modes':[{'id':'plan','name':'Plan from host','description':'Native description'},
                               {'id':'auto','name':'Auto from host'}],
                       'default_mode':'auto','auto_available':True,
                       'source':{'kind':'runtime','label':'Claude --help'}})
    found=permissions.catalog('claude',Path('/tmp'),bridge,model='chosen-model')
    assert found['modes']==bridge.result['modes']
    assert found['default_mode']=='auto' and found['auto_available']
    assert found['inherit_mode']['id']=='native'
    assert bridge.calls[-1][1]['model']=='chosen-model'
    # Upgrading the host changes its catalog without changing product fixtures.
    bridge.result={'modes':[{'id':'newMode','name':'newMode'}],'source':{'kind':'runtime','label':'Claude --help'}}
    assert permissions.catalog('claude',Path('/tmp'),bridge)['modes']==bridge.result['modes']
    assert permissions.validate_options('claude',None)=={}
    assert permissions.validate_options('claude',{})=={}
    for mode in ('auto','native','plan','manual','dontAsk'):
        assert permissions.validate_options('claude',{'mode':mode})=={'mode':mode}
    with pytest.raises(ValueError):permissions.validate_options('claude',{'mode':'bypassPermissions'})


@pytest.mark.parametrize('backend',['claude','zcode','codex','kimi','hermes','reasonix','codebuddy','kilo','kiro','vibe','deepseek-harness','mimo','pi'])
def test_failed_runtime_lookup_never_recreates_a_static_permission_list(backend):
    result=permissions.catalog(backend,Path('/tmp'),ModeBridge(RuntimeError('unavailable')))
    assert result['modes']==[]
    assert result['source']['kind']=='unavailable'
    assert result['auto_available'] is False
    assert 'unavailable' in result['diagnostic']


def test_adapter_scopes_are_not_named_auto_or_claimed_as_host_modes():
    for backend in ('opencode','briefloop-native'):
        result=permissions.catalog(backend,Path('/tmp'),None)
        assert result['source']['kind']=='adapter'
        assert [mode['id'] for mode in result['modes']]==list(permissions.WORKSPACE_SCOPES)
        assert all(mode['name']==mode['id'] for mode in result['modes'])
        assert result['auto_available'] is False
    bridge=ModeBridge({'modes':[{'id':'read-only','name':'read-only'},
                               {'id':'danger-full-access','name':'danger-full-access'},
                               {'id':'workspace-write','name':'workspace-write'}],
                       'source':{'kind':'runtime','label':'Codex --sandbox'}})
    result=permissions.catalog('codex',Path('/tmp'),bridge)
    assert result['modes'][1]['disabled'] is True
    assert result['modes'][1]['name']=='danger-full-access'
    assert result['source']['kind']=='runtime' and result['default_source']=='adapter'


def test_scoped_native_rule_preserves_other_settings_and_rejects_stale_write(tmp_path, monkeypatch):
    path=tmp_path/'settings.json'
    original={'auth':{'fixture':'never-return-this'},'permissions':{'deny':['command(rm *)']},'theme':'dark'}
    path.write_text(json.dumps(original));monkeypatch.setattr(permissions,'antigravity_settings',lambda:path)
    before=path.read_bytes();view=permissions.catalog('antigravity',tmp_path,None)
    assert 'auth' not in view and 'never-return-this' not in json.dumps(view)
    rule='read_file('+str(tmp_path/'fixture.txt')+')'
    permissions.change_antigravity({'revision':view['revision'],'decision':'allow','rule':rule,'operation':'add'})
    result=json.loads(path.read_text());assert result['auth']==original['auth']
    assert path.stat().st_mode & 0o777 == 0o600
    assert result['permissions']['deny']==original['permissions']['deny']
    assert result['permissions']['allow']==[rule]
    with pytest.raises(ValueError,match='已变化'):
        permissions.change_antigravity({'revision':hashlib.sha256(before).hexdigest(),'decision':'allow','rule':'command(*)','operation':'add'})
    revision=permissions.catalog('antigravity',tmp_path,None)['revision']
    permissions.change_antigravity({'revision':revision,'decision':'allow','rule':rule,'operation':'remove'})
    assert not json.loads(path.read_text())['permissions']['allow']


def test_invalid_modes_and_symlink_never_change_native_settings(tmp_path, monkeypatch):
    for backend,mode in [('pi','bypass'),('claude','bypassPermissions'),('codex','read'),('antigravity','yolo'),('zcode','--mode=invalid')]:
        with pytest.raises(ValueError):permissions.validate_options(backend,{'mode':mode})
    target=tmp_path/'target';target.write_text('{}');link=tmp_path/'link';link.symlink_to(target)
    monkeypatch.setattr(permissions,'antigravity_settings',lambda:link)
    with pytest.raises(ValueError,match='符号链接'):permissions.catalog('antigravity',tmp_path,None)
    assert target.read_text()=='{}'


def test_failed_temporary_file_protection_keeps_existing_host_settings(tmp_path, monkeypatch):
    path=tmp_path/'settings.json'
    path.write_text('{"permissions":{},"auth":{"fixture":"unchanged"}}')
    monkeypatch.setattr(permissions,'antigravity_settings',lambda:path)
    revision=permissions.catalog('antigravity',tmp_path,None)['revision']
    before=path.read_bytes()
    def fail_before_write(fd,name,existing):
        assert existing==path and Path(name).read_bytes()==b''
        raise OSError('synthetic protection failure')
    monkeypatch.setattr(permissions,'_protect_temp_file',fail_before_write)
    with pytest.raises(OSError,match='synthetic protection failure'):
        permissions.change_antigravity({'revision':revision,'operation':'preset','preset':'turbo'})
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('.briefloop-permissions-*'))


def test_zcode_offers_its_own_modes_without_claiming_interactive_approval():
    """ZCode headless has no permission channel: a blocked action just fails."""
    bridge=ModeBridge({'modes':[{'id':mode,'name':mode} for mode in ('build','edit','plan','yolo')],
                       'default_mode':'build','default_source':'adapter','native_default_mode':'yolo',
                       'source':{'kind':'runtime','label':'ZCode --mode'}})
    found=permissions.catalog('zcode',Path('/tmp'),bridge)
    assert [m['id'] for m in found['modes']]==['build','edit','plan','yolo']
    assert found['default_mode']=='build'
    assert found['interactive'] is False
    assert found['native_default_mode']=='yolo' and found['default_source']=='adapter'
    assert permissions.validate_options('zcode',{'mode':'plan'})=={'mode':'plan'}
    assert permissions.validate_options('zcode',{})=={'mode':'build'}
    assert permissions.validate_options('zcode',{'mode':'native'})=={'mode':'build'}


def test_pi_usage_includes_cached_prompt_without_double_counting_other_hosts():
    raw={'input':164,'output':52,'cacheRead':8000,'cacheWrite':300,'model_context_window':1000000}
    usage=normalize_bridge_usage(raw,'pi')
    assert usage['last']=={'inputTokens':8464,'outputTokens':52,'cachedInputTokens':8000}
    assert normalize_bridge_usage({'prompt_tokens':8464,'prompt_cache_hit_tokens':8000},'codebuddy')['last']['inputTokens']==8464
    assert usage['modelContextWindow']==1000000
    assert usage['raw']==raw


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
        assert permissions.preset_for(current)==preset
        if preset!='default':assert permissions.permission_digest()!=before
    assert permissions.permission_digest()==before
