from queue import Queue
import threading
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

def test_subagent_activity_public_projection_is_private_and_not_turn_liveness(tmp_path):
    from briefloop.interactive_runtime import InteractiveRuntime
    manager=HarnessManager(Store(tmp_path/'store'),RPC)
    sid=manager.create_session()['id']
    try:
        manager.send(sid,'synthetic task')
        until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
        activity={'id':'activity','type':'subAgentActivity','agentThreadId':'activity-child',
                  'kind':'started','agentPath':'PRIVATE PATH','agentRole':'PRIVATE ROLE',
                  'command':'PRIVATE COMMAND','prompt':'PRIVATE PROMPT'}
        manager.handle_notification({'method':'item/started','params':{
            'threadId':'t1','turnId':'turn1','startedAtMs':1,'item':activity}})
        assert manager._children['activity-child']==sid
        assert 'activity-child' not in manager._active_children
        manager.handle_notification({'method':'turn/started','params':{
            'threadId':'activity-child','turn':{'id':'child-turn'}}})
        manager.handle_notification({'method':'item/completed','params':{
            'threadId':'t1','turnId':'turn1','completedAtMs':2,'item':{**activity,'kind':'completed'}}})
        assert 'activity-child' in manager._active_children
        assert manager.snapshot(sid)['session']['turn_id']=='turn1'
        for tool,status in [('spawnAgent','running'),('wait','completed')]:
            manager.handle_notification({'method':'item/completed','params':{
                'threadId':'t1','turnId':'turn1','item':{'id':tool,'type':'collabAgentToolCall',
                'tool':tool,'status':'completed','receiverThreadIds':['legacy-child'],
                'agentsStates':{'legacy-child':{'status':status}}}}})
        for total in (100,130):
            manager.handle_notification({'method':'thread/tokenUsage/updated','params':{
                'threadId':'t1','turnId':'turn1','tokenUsage':{'total':{'totalTokens':total}}}})
        snapshot=manager.snapshot(sid)
        log=tmp_path/'public.jsonl'
        InteractiveRuntime._project(snapshot,log,0,set())
        events=InteractiveRuntime._public_events(log)
        activities=[event['item'] for event in events if event.get('item',{}).get('type')=='subagent_activity']
        assert activities==[{'id':'activity','type':'subagent_activity','agentThreadId':'activity-child','kind':kind}
                            for kind in ('started','completed')]
        collab=[event['item'] for event in events if event.get('item',{}).get('type')=='collab_tool_call']
        assert [(item['tool'],item['receiverThreadIds'],item['agents_states']) for item in collab]==[
            ('spawnAgent',['legacy-child'],{'legacy-child':{'status':'running'}}),
            ('wait',['legacy-child'],{'legacy-child':{'status':'completed'}})]
        result=InteractiveRuntime._usage_diagnostics(log,'codex')['usage_by_thread']
        rows={row['threadId']:row for row in result['threads']}
        assert rows['activity-child']=={'threadId':'activity-child','kind':'child',
            'parentThreadId':None,'agentRole':None,'turnId':None,'tokenUsage':None}
        assert rows['t1']['tokenUsage']['total']['totalTokens']==130
        assert result['aggregation']=='none' and result['coverage']['completeness']=='unknown'
        assert set(result['coverage']['threads_without_usage'])=={'activity-child','legacy-child'}
        assert 'PRIVATE' not in str(snapshot)+log.read_text(encoding='utf-8')+str(result)
    finally:
        manager.close()


def test_subagent_activity_respects_ownership_and_waits_for_explicit_parent_metadata(tmp_path):
    from briefloop.interactive_runtime import InteractiveRuntime
    manager=HarnessManager(Store(tmp_path/'store'),RPC)
    sid=manager.create_session()['id'];other=manager.create_session()['id']
    try:
        manager.send(sid,'synthetic task')
        until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
        manager.send(other,'unrelated task')
        until(lambda:manager.snapshot(other)['session']['turn_id']=='turn2')
        def activity(outer,target):
            manager.handle_notification({'method':'item/completed','params':{
                'threadId':outer,'turnId':'turn1','completedAtMs':1,
                'item':{'id':'activity-'+target,'type':'subAgentActivity','agentThreadId':target,
                        'agentPath':'PRIVATE PATH','kind':'interacted'}}})
        activity('t2','other-child')
        before=manager.snapshot(sid)['events']
        activity('unknown-outer','unknown-child')
        for target in ('t1','t2','other-child'):
            activity('t1',target)
        assert manager.snapshot(sid)['events']==before
        assert 'unknown-child' not in manager._children
        assert manager._children['other-child']==other
        activity('t1','child')
        activity('child','grandchild')
        log=tmp_path/'public.jsonl'
        cursor=InteractiveRuntime._project(manager.snapshot(sid),log,0,set())
        result=InteractiveRuntime._usage_diagnostics(log,'codex')['usage_by_thread']
        rows={row['threadId']:row for row in result['threads']}
        assert set(rows)=={'t1','child','grandchild'}
        assert all(rows[tid]['parentThreadId'] is None and rows[tid]['agentRole'] is None
                   for tid in ('child','grandchild'))
        manager.handle_notification({'method':'thread/started','params':{'thread':{
            'id':'grandchild','agentRole':'analyst','source':{'subAgent':{'thread_spawn':{
                'parent_thread_id':'child','depth':2}}}}}})
        InteractiveRuntime._project(manager.snapshot(sid),log,cursor,set())
        result=InteractiveRuntime._usage_diagnostics(log,'codex')['usage_by_thread']
        grandchild=next(row for row in result['threads'] if row['threadId']=='grandchild')
        assert grandchild['parentThreadId']=='child' and grandchild['agentRole']=='analyst'
        assert grandchild['tokenUsage'] is None
        assert not manager._active_children
        assert all(text not in log.read_text(encoding='utf-8') for text in ('PRIVATE','other-child','unknown-child'))
    finally:
        manager.close()


def test_owned_child_metadata_before_and_after_legacy_spawn(tmp_path):
    for metadata_first in (False,True):
        manager=HarnessManager(Store(tmp_path/str(metadata_first)),RPC)
        sid=manager.create_session()['id'];other=manager.create_session()['id']
        try:
            manager.send(sid,'synthetic task')
            until(lambda:manager.snapshot(sid)['session']['turn_id']=='turn1')
            manager.send(other,'unrelated task')
            until(lambda:manager.snapshot(other)['session']['turn_id']=='turn2')
            def started(thread,parent,role=None):
                manager.handle_notification({'method':'thread/started','params':{'thread':{
                    'id':thread,'agentRole':role,'preview':'PRIVATE PREVIEW',
                    'turns':[{'prompt':'PRIVATE PROMPT'}],
                    'source':{'subAgent':{'thread_spawn':{'parent_thread_id':parent,
                        'agent_path':'PRIVATE PATH','agent_role':'not-an-actual-role','depth':1}}}}}})
            def spawn(parent,receivers):
                manager.handle_notification({'method':'item/completed','params':{
                    'threadId':parent,'turnId':'turn1','item':{'id':'spawn','type':'collabAgentToolCall',
                    'tool':'spawnAgent','status':'completed','receiverThreadIds':receivers,
                    'agentsStates':{r:{'status':'running'} for r in receivers}}}})
            started('outsider','unknown','unrelated')
            assert 'outsider' not in manager._children
            if metadata_first:
                started('child','t1','analyst')
                assert 'child' not in manager._active_children  # Identity is not activity.
            spawn('t1',['child'])
            if not metadata_first:started('child','t1','analyst')
            started('grandchild','child')
            started('grandchild','not-yet-known','scout')
            started('other-child','t2','reviewer')
            started('other-child','t1','wrong-owner')
            spawn('t1',['other-child','t2'])
            assert manager._children['other-child']==other
            assert 't2' not in manager._children
            main_usage={'total':{'totalTokens':100}}
            for thread,turn,usage in [('t1','turn1',main_usage),('child','child-turn',{'total':{'totalTokens':21}})]:
                manager.handle_notification({'method':'thread/tokenUsage/updated','params':{
                    'threadId':thread,'turnId':turn,'tokenUsage':usage}})
            snapshot=manager.snapshot(sid)
            identities=[e['data'] for e in snapshot['events'] if e['kind']=='child/thread/started']
            assert identities==[{'threadId':'child','parentThreadId':'t1','agentRole':'analyst'},
                                {'threadId':'grandchild','parentThreadId':'child','agentRole':None},
                                {'threadId':'grandchild','agentRole':'scout'}]
            assert snapshot['token_usage']==main_usage
            usage_events=[e['data'] for e in snapshot['events'] if e['kind'].endswith('tokenUsage/updated')]
            assert [(e['threadId'],e['turnId']) for e in usage_events]==[('t1','turn1'),('child','child-turn')]
            assert all(text not in str(snapshot) for text in ('PRIVATE','other-child','wrong-owner','outsider'))
        finally:
            manager.close()

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
    steer=manager.send(sid,'change direction',mode='steer',runtime={'permission':'read-only'},allow_web=True)
    until(lambda:any(m['id']==steer['id'] and m['status']=='delivered' for m in manager.snapshot(sid)['messages']))
    assert len([c for c in manager.client.calls if c[0]=='turn/start'])==1
    manager.handle_notification({'method':'item/reasoning/textDelta','params':{'threadId':'t1','delta':'private reasoning'}})
    manager.handle_notification({'method':'item/agentMessage/delta','params':{'threadId':'t1','turnId':'turn1','itemId':'i','delta':'hello'}})
    assert manager.snapshot(sid)['messages'][-1]['text']=='hello'
    assert manager.snapshot(sid,reasoning=True)['messages'][-1]['reasoning']=='private reasoning'
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


def test_host_default_model_is_not_sent_as_a_literal_api_model(tmp_path):
    manager=HarnessManager(Store(tmp_path),RPC)
    try:
        sid=manager.create_session(runtime={'model':'default'})['id']
        manager.send(sid,'hello')
        until(lambda:manager.client is not None and any(method=='turn/start' for method,_ in manager.client.calls))
        starts=[params for method,params in manager.client.calls if method in ('thread/start','turn/start')]
        assert all('model' not in params for params in starts)
    finally:manager.close()


def test_turn_start_reply_stall_does_not_freeze_other_sessions(tmp_path):
    """Phase 0 mitigation: the turn/start wait must not hold the manager lock."""
    class StalledStart(RPC):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.pending=threading.Event();self.release=threading.Event()
        def request(self,method,params):
            if method=='turn/start':
                self.pending.set();assert self.release.wait(5)
            return super().request(method,params)
    manager=HarnessManager(Store(tmp_path),StalledStart)
    try:
        deep=manager.create_session()['id']
        manager.send(deep,'round 1')
        until(lambda:manager.client is not None and manager.client.pending.is_set())
        sent=threading.Event()
        def unrelated_session_send():
            manager.send(manager.create_session()['id'],'unrelated session');sent.set()
        worker=threading.Thread(target=unrelated_session_send);worker.start()
        # With the lock held during the stall this send would block ~5s and fail.
        assert sent.wait(2)
        worker.join(5)
    finally:
        if manager.client is not None:manager.client.release.set()
        manager.close()


def test_cancel_during_turn_start_gap_is_compensated_after_reply(tmp_path):
    """A cancel landing while turn/start is in flight still interrupts the turn."""
    class StalledStart(RPC):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.pending=threading.Event();self.release=threading.Event()
        def request(self,method,params):
            if method=='turn/start':
                self.pending.set();assert self.release.wait(5)
            return super().request(method,params)
    manager=HarnessManager(Store(tmp_path),StalledStart)
    try:
        sid=manager.create_session()['id']
        manager.send(sid,'long research round',message_id='r1')
        until(lambda:manager.client is not None and manager.client.pending.is_set())
        # cancel() sees no published turn yet: it only records the request.
        cancelled=threading.Event()
        def cancel_while_reply_outstanding():
            manager.cancel(sid);cancelled.set()
        worker=threading.Thread(target=cancel_while_reply_outstanding);worker.start()
        assert cancelled.wait(2)
        worker.join(5)
        manager.client.release.set()
        # After the reply the turn is published and the recorded cancel is
        # compensated with a real interrupt instead of leaving it running.
        until(lambda:manager.chat.session(sid)['turn_id']=='turn1')
        until(lambda:('turn/interrupt',{'threadId':'t1','turnId':'turn1'}) in manager.client.calls)
        assert manager.chat.session(sid)['status']=='stopping'
        manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn1','status':'interrupted'}}})
        until(lambda:next(m for m in manager.snapshot(sid)['messages'] if m['id']=='r1')['status']=='interrupted')
        assert manager.chat.session(sid)['turn_id'] is None
    finally:
        if manager.client is not None:manager.client.release.set()
        manager.close()


def test_turn_completing_before_publish_does_not_stick_in_running(tmp_path, monkeypatch):
    """A turn that finishes inside the in-flight window adopts its terminal
    state instead of being published as a running turn nobody completes."""
    class StalledStart(RPC):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.pending=threading.Event();self.release=threading.Event()
        def request(self,method,params):
            if method=='turn/start':
                self.pending.set();assert self.release.wait(5)
            return super().request(method,params)
    manager=HarnessManager(Store(tmp_path),StalledStart)
    original_patch=manager.chat.patch_message
    def checked_patch(mid, **fields):
        if mid=='f1' and fields.get('status')=='failed':
            assert manager.chat.session(sid)['turn_id'] is None
        return original_patch(mid, **fields)
    monkeypatch.setattr(manager.chat,'patch_message',checked_patch)
    try:
        sid=manager.create_session()['id']
        manager.send(sid,'instantly failing turn',message_id='f1')
        until(lambda:manager.client is not None and manager.client.pending.is_set())
        # The host completes the turn before the dispatcher's reply is processed.
        manager.handle_notification({'method':'turn/completed','params':{'threadId':'t1','turn':{'id':'turn1','status':'failed'}}})
        manager.client.release.set()
        until(lambda:next(m for m in manager.snapshot(sid)['messages'] if m['id']=='f1')['status']=='failed')
        assert manager.chat.session(sid)['turn_id'] is None
        assert manager.chat.session(sid)['status']=='failed'
    finally:
        if manager.client is not None:manager.client.release.set()
        manager.close()
