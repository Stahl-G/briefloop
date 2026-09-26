"""v2 port behavior; fixtures follow the official 2.0.14 OpenAPI."""
import copy
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest

from briefloop.backends.opencode_server import OpencodeServerClient, OpencodeError, executable_version
from briefloop.backends.opencode_v2 import permission_rules
from briefloop.opencode_harness import OpencodeHarness, _permission_rules


class API:
    def __init__(self, directory):
        self.directory = str(directory.resolve())
        self.calls = []
        self.session = None
        self.instructions = {}
        self.pages = []
        self.variant = 'high'
        self.users = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        url = urlsplit(path); route = url.path; query = parse_qs(url.query)
        if route == '/api/provider':
            return {'location': {'directory': self.directory}, 'data': [{'id': 'fixture', 'name': 'Fixture', 'package': '@opencode/ai/providers/openai-compatible'}]}
        if route == '/api/model':
            return {'location': {'directory': self.directory}, 'data': [{'id': 'tiny', 'providerID': 'fixture', 'enabled': True,
                'variants': [{'id': self.variant}], 'capabilities': {'tools': True, 'input': ['text', 'image'], 'output': ['text']},
                'limit': {'context': 1000, 'output': 100}}]}
        if method == 'POST' and route == '/api/session':
            self.session = {'id': 'ses_test', **copy.deepcopy(body)}
            self.users = []
            return {'data': copy.deepcopy(self.session)}
        if route == '/api/session/ses_test' and method == 'GET':
            if self.session is None:
                raise OpencodeError('missing', status=404)
            return {'data': copy.deepcopy(self.session)}
        if route == '/api/session/ses_test/model':
            self.session['model'] = copy.deepcopy(body['model']); return None
        if route == '/api/session/ses_test/agent':
            self.session['agent'] = body['agent']; return None
        if route == '/api/experimental/session/ses_test/instructions/entries':
            return {'data':[{'key':key,'value':value} for key,value in self.instructions.items()]}
        if route == '/api/experimental/session/ses_test/instructions/entries/briefloop.system':
            if method == 'PUT':
                self.instructions['briefloop.system'] = body['value']
            elif method == 'DELETE':
                self.instructions.pop('briefloop.system', None)
            return None
        if route == '/api/session/ses_test/prompt':
            assert 'model' not in body and 'system' not in body and 'parts' not in body
            self.users.append({'type':'user','id':'msg_admitted','text':body['text']})
            return {'data': {'id': 'msg_admitted', 'text': body['text']}}
        if route == '/api/session/ses_test/message':
            if query.get('type')==['user']:
                return {'data':self.users[:1], 'cursor':{'next':None}}
            if self.pages:
                return self.pages.pop(0)
            return {'data': [], 'cursor': {'next': None, 'previous': None}}
        if route == '/api/session/active':
            return {'data': {'ses_test': {'type': 'running'}}}
        if route == '/api/session' and method == 'GET':
            assert query['parentID'] == ['ses_test']
            return {'data': [{'id': 'ses_child', 'parentID': 'ses_test'}], 'cursor': {'next': None}}
        if route == '/api/session/ses_test/interrupt':
            return {'interrupted': True}
        if route == '/api/location':
            return {'directory': self.directory, 'project': {'directory': str(Path(self.directory).parent)}}
        raise AssertionError((method, path, body))


def client(tmp_path):
    c = OpencodeServerClient.__new__(OpencodeServerClient); c.major = 2
    api = API(tmp_path); c._request = api
    return c, api


def create(c, root):
    return c.create_session('中文测试', directory=root, model='fixture/tiny', permission=[
        {'permission': 'bash', 'action': 'deny', 'pattern': '*'},
        {'permission': 'edit', 'action': 'deny', 'pattern': '*'}], require_permissions=True)


def test_v2_preserves_frozen_settings_contract_files_and_real_routes(tmp_path):
    c, api = client(tmp_path); value = create(c, tmp_path)
    assert value['location'] == {'directory': str(tmp_path.resolve())}
    assert value['permissions'] == [{'action': 'shell', 'resource': '*', 'effect': 'deny'}, {'action': 'edit', 'resource': '*', 'effect': 'deny'},
                                   {'action': 'opencode_session_move', 'resource': '*', 'effect': 'deny'}]
    c.prompt_async(value['id'], '正文', model={'providerID': 'fixture', 'modelID': 'tiny'}, variant='high',
                   files=[{'mime': 'image/png', 'url': 'data:image/png;base64,YQ==', 'filename': '图.png'}], system='合同一', directory=tmp_path)
    assert api.session['model'] == {'providerID': 'fixture', 'id': 'tiny', 'variant': 'high'}
    assert api.instructions == {'briefloop.system': '合同一'}
    prompt = next(body for method, path, body in reversed(api.calls) if path.endswith('/prompt'))
    assert prompt == {'text': '正文', 'files': [{'uri': 'data:image/png;base64,YQ==', 'name': '图.png'}]}
    c.prompt_async(value['id'], '后续', system='合同一', directory=tmp_path)
    assert api.instructions == {'briefloop.system': '合同一'}
    assert c.paths(tmp_path)['worktree'] == str(tmp_path.resolve()), 'v2 read resources use session cwd, not Git root'
    assert c.session_status(value['id'], directory=tmp_path) == {'type': 'busy'}
    assert c.children(value['id'], directory=tmp_path)[0]['id'] == 'ses_child'
    assert c.abort(value['id'], directory=tmp_path) == {'interrupted': True}
    assert not any(path.startswith('/session') or path.startswith('/global') for _, path, _ in api.calls)


def test_v2_never_retries_after_permission_contract_or_directory_rejection(tmp_path):
    c, api = client(tmp_path)
    def rejects(method, path, body=None):
        if method == 'POST' and path == '/api/session':
            raise OpencodeError('permissions rejected', status=422)
        return api(method, path, body)
    c._request = rejects
    with pytest.raises(OpencodeError):create(c, tmp_path)
    assert not any(path.endswith('/prompt') for _, path, _ in api.calls)
    c._request = api; create(c, tmp_path)
    with pytest.raises(OpencodeError, match='目录'):
        c.prompt_async('ses_test', 'unsafe', directory=tmp_path/'different')
    def rejects_system(method, path, body=None):
        if '/instructions/' in path:
            raise OpencodeError('instructions rejected', status=400)
        return api(method, path, body)
    c._request = rejects_system
    with pytest.raises(OpencodeError):c.prompt_async('ses_test', 'unsafe', system='must keep', directory=tmp_path)
    assert not any(path.endswith('/prompt') for _, path, _ in api.calls)


def test_v2_model_effort_and_missing_native_session_fail_without_default_or_recreate(tmp_path):
    c, api = client(tmp_path); create(c, tmp_path)
    for model, variant in [('fixture/absent', None), ('fixture/tiny', 'invented')]:
        with pytest.raises(OpencodeError):c.prompt_async('ses_test', 'x', model=model, variant=variant, directory=tmp_path)
    assert not any(path.endswith('/prompt') for _, path, _ in api.calls)
    api.session = None; before = len([x for x in api.calls if x[:2] == ('POST','/api/session')])
    with pytest.raises(OpencodeError, match='迁移'):
        c.prompt_async('ses_test', 'old conversation', directory=tmp_path)
    assert len([x for x in api.calls if x[:2] == ('POST','/api/session')]) == before


def test_v2_contract_change_rejected_before_mutation_after_first_user_turn(tmp_path):
    c,api=client(tmp_path);create(c,tmp_path)
    c.prompt_async('ses_test','first',system='contract A',directory=tmp_path)
    for replacement in ('contract B',None):
        api.calls.clear()
        with pytest.raises(OpencodeError,match='新建对话'):
            c.prompt_async('ses_test','never sent',system=replacement,model='fixture/tiny',variant='high',agent='plan',directory=tmp_path)
        assert all(method=='GET' for method,_,_ in api.calls)
        assert api.instructions=={'briefloop.system':'contract A'}
        assert api.session['agent']=='build' and api.session['model']=={'providerID':'fixture','id':'tiny'}
    c.prompt_async('ses_test','same contract continues',system='contract A',directory=tmp_path)
    assert len(api.users)==2
    # Before the first admitted turn an orphaned entry may still be cleared.
    c2,api2=client(tmp_path);create(c2,tmp_path);api2.instructions={'briefloop.system':'never used'}
    c2.prompt_async('ses_test','first without system',system=None,directory=tmp_path)
    assert api2.instructions=={} and len(api2.users)==1


def test_v2_pages_project_tools_and_do_not_repeat_old_response_on_failed_new_turn(tmp_path):
    c, api = client(tmp_path); create(c, tmp_path)
    old = {'id':'msg_old','type':'assistant','time':{'created':1,'completed':2},'finish':'stop','content':[{'type':'text','text':'OLD RESPONSE'}]}
    user = {'id':'msg_user','type':'user','time':{'created':3},'text':'new task'}
    tool = {'id':'msg_work','type':'assistant','time':{'created':4,'completed':5},'finish':'tool-calls','content':[
        {'id':'call_1','type':'tool','name':'shell','time':{'created':4,'completed':5},'state':{'status':'error','input':{'command':'blocked'},'error':{'type':'PermissionDenied','message':'denied'},'content':[{'type':'text','text':'Denied by rule'}]}}]}
    idle = {'id':'msg_idle','type':'idle','time':{'created':6},'outcome':'failed'}
    api.pages = [{'data':[old,user,tool], 'cursor':{'next':'opaque +/?'}},{'data':[idle],'cursor':{'next':None}}]
    out = c.messages('ses_test', directory=tmp_path)
    assert len(out) == 4 and out[2]['info']['finish'] == 'tool-calls'
    assert out[2]['parts'][0]['state']['output'] == 'Denied by rule'
    assert out[2]['parts'][0]['callID'] == 'call_1'
    assert out[-1]['info']['finish'] == 'error' and 'OLD RESPONSE' not in str(out[-1])
    message_calls=[p for _,p,_ in api.calls if '/message' in p]
    assert parse_qs(urlsplit(message_calls[-1]).query) == {'cursor':['opaque +/?']}
    api.pages=[{'data':[old,user,idle],'cursor':{'next':None}}]
    assert c.messages('ses_test',directory=tmp_path)[-1]['parts'] == []
    api.pages=[{'data':[],'cursor':{'next':'repeat'}},{'data':[],'cursor':{'next':'repeat'}}]
    with pytest.raises(OpencodeError, match='游标'):
        c.messages('ses_test',directory=tmp_path)

    api.pages=[{'data':[user,{'id':'msg_error','type':'assistant','time':{'created':4},
        'content':[], 'error':{'type':'APIError','message':'quota exhausted','status':429}},idle], 'cursor':{'next':None}}]
    failure=c.messages('ses_test',directory=tmp_path)[-1]['info']['error']
    assert failure['message']=='quota exhausted' and failure['data']['statusCode']==429


def test_v2_catalog_keeps_variants_and_rejects_config_writes_before_any_request(tmp_path):
    c, api = client(tmp_path)
    catalog = c.providers('C:/中文路径 with + & space')
    assert catalog['providers'][0]['models']['tiny']['variants'] == ['high']
    for _,path,_ in api.calls:
        assert parse_qs(urlsplit(path).query) == {'location[directory]':['C:/中文路径 with + & space']}
    api.calls.clear()
    with pytest.raises(ValueError,match='OpenCode 中配置'):
        c.configure_provider(tmp_path,'fixture','tiny','https://example.test','secret-not-saved')
    assert api.calls == []
    with pytest.raises(ValueError,match='旧凭据'):
        c.probe_provider_catalog('fixture')
    assert api.calls == []


def test_v2_ordinary_read_only_denies_shell_edit_and_unknown_rule_fails(tmp_path):
    rules = permission_rules(_permission_rules({'permission':'read-only'},False,tmp_path))
    assert {'action':'shell','resource':'*','effect':'deny'} in rules
    assert {'action':'edit','resource':'*','effect':'deny'} in rules
    assert {'action':'question','resource':'*','effect':'deny'} in rules
    assert rules[-1]=={'action':'opencode_session_move','resource':'*','effect':'deny'}
    with pytest.raises(OpencodeError):
        permission_rules([{'permission':'unknown_future_power','pattern':'*','action':'allow'}])


def test_version_probe_isolated_and_unknown_major_is_rejected(monkeypatch):
    import subprocess
    from briefloop.backends import opencode_server
    calls=[]
    def run(args,**kw):
        calls.append((args,kw));return subprocess.CompletedProcess(args,0,'opencode v2.0.14\n','')
    monkeypatch.setattr(opencode_server.subprocess,'run',run)
    assert executable_version('fixture-binary',{'PATH':'test'}) == '2.0.14'
    assert calls[0][1]['env'] == {'PATH':'test'} and calls[0][1]['stdin'] == subprocess.DEVNULL
    monkeypatch.setattr(opencode_server.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0,'3.0.0',''))
    with pytest.raises(OpencodeError,match='未验证'):executable_version('fixture-binary')


def test_v2_subagent_discovery_and_empty_catalog_are_recoverable(tmp_path):
    from briefloop.opencode_harness import _task_children
    from briefloop.store import Store
    part={'type':'tool','tool':'subagent','state':{'metadata':{'sessionID':'ses_child'},
        'output':'<subagent sessionID="ses_child">done</subagent>'}}
    assert _task_children(part)==['ses_child']
    assert _task_children({'state':{'output':'<task id="ses_old">done</task>'}})==['ses_old']
    c,api=client(tmp_path);c.close=lambda:None
    manager=OpencodeHarness(Store(tmp_path/'workspace'),lambda _:c)
    calls=[]
    def providers(directory):
        calls.append(True)
        return {'providers':[]} if len(calls)==1 else {'providers':[{'id':'fixture','models':{'tiny':{'variants':['high']}}}]}
    c.providers=providers
    try:
        assert manager.list_models()==[]
        assert manager.list_models()[0]['variants']==['high']
        assert len(calls)==2
    finally:manager.close()


def test_v2_reviewer_guard_rejects_before_session_or_prompt(tmp_path):
    import time
    from briefloop.store import Store
    c,api=client(tmp_path);c.close=lambda:None
    manager=OpencodeHarness(Store(tmp_path/'workspace'),lambda _:c)
    try:
        sid=manager.create_session(runtime={'model':'fixture/tiny','permission':'read-only',
                                           'review_root':str(tmp_path),'review_mode':'strict'})['id']
        manager.send(sid,'review')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            snapshot=manager.snapshot(sid)
            if any(m['status']=='failed' for m in snapshot['messages']) and any(e['kind']=='error' for e in snapshot['events']):break
            time.sleep(.01)
        assert any(m['status']=='failed' for m in snapshot['messages'])
        assert api.calls==[], 'restricted review must fail before binding or sending'
        assert any('Reviewer' in str(e) and 'AGENTS' in str(e) for e in snapshot['events'])
    finally:manager.close()
