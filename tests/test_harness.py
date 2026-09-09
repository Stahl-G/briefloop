from queue import Queue
import time
from briefloop.store import Store
from briefloop.harness import HarnessManager

class RPC:
    def __init__(self,*args,**kwargs):
        assert not kwargs  # transport construction never binds a model/provider
        self.notifications=Queue();self.server_requests=Queue();self.calls=[];self.count=0;self.threads=0
    def request(self,method,params):
        self.calls.append((method,params))
        if method=='thread/start':
            self.threads+=1;return {'thread':{'id':'t'+str(self.threads)}}
        if method=='turn/start':
            self.count+=1;return {'turn':{'id':'turn'+str(self.count)}}
        return {}
    def interrupt(self,t,u):self.calls.append(('turn/interrupt',{'threadId':t,'turnId':u}))
    def answer(self,i,result):self.calls.append(('answer',{'id':i,'result':result}))
    def close(self):pass

def until(check):
    end=time.monotonic()+3
    while time.monotonic()<end:
        if check():return
        time.sleep(.01)
    assert check()

def test_queue_steering_and_public_stream(tmp_path):
    manager=HarnessManager(Store(tmp_path),RPC)
    sid=manager.create_session(runtime={'permission':'read-only'})['id']
    assert manager.client is None
    one=manager.send(sid,'first',message_id='first',allow_web=True)
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
    assert manager.send(sid,'duplicate',message_id='first')['id']==one['id']
    first_turn=next(params for method,params in manager.client.calls if method=='turn/start')
    assert first_turn['sandboxPolicy']=={'type':'readOnly','networkAccess':True}
    assert next(params for method,params in manager.client.calls if method=='thread/start')['config']['web_search']=='live'
    manager.send(sid,'next',runtime={'permission':'workspace-write'})
    steer=manager.send(sid,'change direction',mode='steer',runtime={'permission':'read-only'})
    until(lambda:any(m['id']==steer['id'] and m['status']=='delivered' for m in manager.snapshot(sid)['messages']))
    assert len([c for c in manager.client.calls if c[0]=='turn/start'])==1
    manager.handle_notification({'method':'item/reasoning/textDelta','params':{'threadId':'t1','delta':'private reasoning'}})
    manager.handle_notification({'method':'item/agentMessage/delta','params':{'threadId':'t1','turnId':'turn1','itemId':'i','delta':'hello'}})
    assert manager.snapshot(sid)['messages'][-1]['text']=='hello'
    assert 'private reasoning' not in str(manager.snapshot(sid))
    manager.handle_notification({'method':'item/completed','params':{'threadId':'t1','turnId':'turn1','item':{'id':'spawn','type':'collabAgentToolCall','tool':'spawnAgent','status':'completed','receiverThreadIds':['child'],'agentsStates':{'child':{'status':'running'}}}}})
    manager.handle_notification({'method':'turn/completed','params':{'threadId':'child','turn':{'id':'childturn','status':'completed'}}})
    assert manager.snapshot(sid)['session']['turn_id']=='turn1'
    manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn1','status':'completed'}}})
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn2')
    starts=[params for method,params in manager.client.calls if method=='turn/start']
    assert starts[1]['sandboxPolicy']['type']=='workspaceWrite'
    usage={'last':{'totalTokens':123},'total':{'totalTokens':456},'modelContextWindow':1000}
    manager.handle_notification({'method':'thread/tokenUsage/updated','params':{'threadId':'t1','tokenUsage':usage}})
    assert manager.snapshot(sid)['token_usage']==usage
    manager.client.server_requests.put({'id':42,'method':'item/tool/requestUserInput','params':{'threadId':'t1','turnId':'turn2','questions':[{'id':'q','question':'Which?','options':[]}]}})
    until(lambda:len(manager.snapshot(sid)['requests'])==1)
    question=manager.snapshot(sid)['requests'][0]
    assert question['status']=='pending'
    manager.answer(sid,question['id'],{'q':{'answers':['answer']}})
    assert manager.client.calls[-1]==('answer',{'id':42,'result':{'answers':{'q':{'answers':['answer']}}}})
    manager.cancel(sid)
    assert manager.client.calls[-1]==('turn/interrupt',{'threadId':'t1','turnId':'turn2'})
    manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn2','status':'interrupted'}}})
    manager.send(sid,'custom provider',runtime={'model':'vendor/my-custom-model','model_provider':'responses-local','effort':'none'})
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn3')
    thread_calls=[params for method,params in manager.client.calls if method=='thread/start']
    assert thread_calls[-1]['model']=='vendor/my-custom-model'
    assert thread_calls[-1]['modelProvider']=='responses-local'
    turn_calls=[params for method,params in manager.client.calls if method=='turn/start']
    assert 'effort' not in turn_calls[-1]
    assert manager.snapshot(sid)['session']['thread_id']=='t2'
    assert manager.snapshot(sid)['token_usage'] is None
    manager.handle_notification({'method':'turn/completed','params':{'threadId':'t2','turn':{'id':'turn3','status':'completed'}}})
    manager.send(sid,'back to configured default',runtime={'model_provider':None})
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn4')
    thread_calls=[params for method,params in manager.client.calls if method=='thread/start']
    assert 'modelProvider' not in thread_calls[-1]
    assert manager.snapshot(sid)['session']['thread_id']=='t3'
    assert any(e['kind']=='thread/providerChanged' for e in manager.snapshot(sid)['events'])
    manager.close()

def test_sources_persist_and_failed_delivery_not_replayed(tmp_path):
    class Broken(RPC):
        def request(self,method,params):
            if method=='turn/start':raise RuntimeError('disconnected')
            return super().request(method,params)
    store=Store(tmp_path);source=store.add_source('test','original')
    manager=HarnessManager(store,Broken);sid=manager.create_session()['id']
    manager.send(sid,'read',source_ids=[source['id']])
    until(lambda:manager.snapshot(sid)['session']['status']=='failed')
    manager.start_internal('private long prompt',display_text='生成简报',session_id=sid,message_id='private')
    until(lambda:any(m['id']=='private' and m['status']=='failed' for m in manager.snapshot(sid)['messages']))
    assert 'private long prompt' not in str(manager.snapshot(sid))
    manager.close()
    restored=HarnessManager(store,RPC)
    snap=restored.snapshot(sid)
    assert snap['messages'][0]['status']=='failed'
    assert snap['messages'][0]['source_ids']==[source['id']]
    assert restored.client is None
    task_cwd=store.root/'jobs'/'internal-check'
    task_cwd.mkdir()
    task=restored.start_internal('Write research output',cwd=task_cwd,display_text='Research',allow_web=True,search_provider='tavily')
    until(lambda:restored.snapshot(task.session_id)['session']['turn_id'] is not None)
    actual=next(params for method,params in restored.client.calls if method=='turn/start')
    assert set(actual['sandboxPolicy']['writableRoots'])=={str(store.root),str(task_cwd)}
    assert str(store.root.parent) not in actual['sandboxPolicy']['writableRoots']
    assert actual['sandboxPolicy']['networkAccess'] is True
    start=next(params for method,params in restored.client.calls if method=='thread/start')
    assert start['config']['web_search']=='disabled'
    assert restored.snapshot(task.session_id)['messages'][0]['runtime']['search_provider']=='tavily'
    restored.close()

def test_workspace_tool_inspects_and_enqueues_real_store(tmp_path,monkeypatch,capsys):
    import json
    from briefloop.cli import main
    from briefloop.chat_tools import workspace_action,chat_instructions
    store=Store(tmp_path/'workspace');source=store.add_source('memo','Source evidence')
    index=workspace_action(store,{'action':'inspect'})
    assert index['sources'][0]['id']==source['id']
    assert 'Source evidence' not in str(index)
    request=tmp_path/'request.json'
    request.write_text(json.dumps({'action':'generate','requirements':{'title':'简报','objective':'Summarize source'},'source_ids':[source['id']]}))
    monkeypatch.setattr('sys.argv',['briefloop','tool','--workspace',str(store.root),'workspace-action','--request',str(request)])
    main()
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='queued'
    job=store.one('jobs',result['job_id'])
    assert job['kind']=='generate'
    assert json.loads(job['payload'])['run_id']==result['run_id']
    assert not store.rows('SELECT id FROM briefs')
    instructions=chat_instructions(store,{'model':'gpt-5.6-luna','effort':'high'})
    assert 'workspace-action' in instructions and str(store.root) in instructions
    assert '避免递归入队' in chat_instructions(store,{},internal=True)

def test_session_lifecycle_keeps_reports_and_never_replays(tmp_path):
    import pytest
    store=Store(tmp_path);source=store.add_source('kept source','original')
    run=store.create_run({'title':'kept','objective':'preserve'},[source['id']])
    brief=store.publish(run['id'],{'title':'kept','markdown':'# Kept report'})
    manager=HarnessManager(store,RPC);sid=manager.create_session()['id']
    manager.send(sid,'hello')
    until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
    with pytest.raises(ValueError):manager.archive(sid)
    with pytest.raises(ValueError):manager.delete(sid)
    manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn1','status':'completed'}}})
    until(lambda:sid not in manager._busy)
    manager.archive(sid)
    assert not manager.list_sessions()
    assert manager.list_sessions('archived')[0]['id']==sid
    calls=len(manager.client.calls)
    with pytest.raises(ValueError):manager.send(sid,'must not run')
    manager.restore(sid)
    assert len(manager.client.calls)==calls
    manager.delete(sid)
    assert manager.snapshot(sid)['session']['lifecycle']=='deleted'
    assert store.one('briefs',brief['id'])['markdown']=='# Kept report'
    assert store.source_text(source['id'])=='original'
    manager.restore(sid)
    empty=manager.create_session()['id']
    queued=manager.create_session()['id']
    manager.chat.message(queued,'waiting')
    assert manager.archive_completed()=={'count':1}
    assert {s['id'] for s in manager.list_sessions()}=={empty,queued}
    assert len(manager.client.calls)==calls
    manager.close()
