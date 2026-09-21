import json
from briefloop.store import Store
from briefloop.task_progress import summary


def test_saved_sources_are_not_read_claims_and_failed_requests_are_not_success(tmp_path):
    store=Store(tmp_path)
    source=store.add_source('Official document','One source.')
    run=store.create_run({'title':'日报','objective':'Summarize'},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']});store.update_job(job['id'],'running')
    store.set_meta('research_requests:'+run['id'],{'a':{'operation':'search','status':'completed'},'b':{'operation':'search','status':'failed'}})
    store.event(job['id'],'runtime_progress',{'stage':'读取来源','agents':[{'role':'Scout','status':'failed'}],
        'stages':[{'id':'research','label':'研究检索','status':'done'}], 'message':'hidden raw command', 'pid':123})
    before=store.rows('SELECT * FROM meta')
    p=summary(store,job['id'])
    assert p['source_count']==1 and p['search_counts']=={'completed':1,'failed':1,'reserved':0}
    assert p['stages'][0]['status']=='active' and not p['version_id']
    assert 'hidden raw command' not in json.dumps(p) and 'pid' not in p
    assert before==store.rows('SELECT * FROM meta')
    store.update_job(job['id'],'cancelled')
    assert summary(store,job['id'])['stage']=='任务已停止'
    assert summary(store,job['id'])['stages'][0]['status']=='paused'
    store.event(job['id'],'runtime_progress',{'stages':[{'id':'evaluate','label':'独立评分','status':'active'}]})
    store.update_job(job['id'],'complete')
    assert summary(store,job['id'])['stages'][0]['status']!='done'


def test_child_phase_and_saved_draft_are_bound_to_report(tmp_path):
    store=Store(tmp_path)
    run=store.create_run({'title':'日报','objective':'Summarize','allow_web':True},[])
    other=store.create_run({'title':'Other','objective':'Other','allow_web':True},[])
    store.publish(other['id'],{'title':'Other','markdown':'Other'})
    job=store.enqueue('generate',{'run_id':run['id']});store.update_job(job['id'],'running')
    assert summary(store,job['id'])['version_id'] is None
    brief=store.publish(run['id'],{'title':'日报','markdown':'Saved'})
    child=store.enqueue('fact_check',{'run_id':run['id'],'version_id':brief['id'],'parent_job_id':job['id']})
    store.update_job(child['id'],'running')
    p=summary(store,job['id'])
    assert p['version_id']==brief['id'] and p['stage']=='独立事实核查进行中'
    assert p['stages']==[{'id':'fact_check','label':'独立事实核查','status':'active'}]
    store.update_job(job['id'],'interrupted')
    assert summary(store,job['id'])['stage']=='任务已中断'


def test_review_card_stays_on_its_input_version(tmp_path):
    store=Store(tmp_path)
    source=store.add_source('Source','Original text')
    run=store.create_run({'title':'Report','objective':'Summarize'},[source['id']])
    original=store.publish(run['id'],{'title':'Report','markdown':'Original'})
    job=store.enqueue('review',{'version_id':original['id']})
    store.publish(run['id'],{'title':'Report','markdown':'Later'},parent_id=original['id'])
    assert summary(store,job['id'])['version_id']==original['id']


def test_progress_http_is_read_only_and_source_is_openable(tmp_path):
    import threading
    import urllib.request
    from briefloop.server import make_server
    server=make_server(tmp_path,port=0,paused=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        store=server.store
        source=store.add_source('Material','Saved source text')
        run=store.create_run({'title':'Report','objective':'Summarize'},[source['id']])
        job=store.enqueue('generate',{'run_id':run['id']})
        with urllib.request.urlopen(base+'/api/task-progress?job='+job['id']) as response: result=json.load(response)
        assert result['status']=='queued' and result['source_count']==1
        with urllib.request.urlopen(base+'/api/source?id='+result['sources'][0]['id']) as response: assert response.status==200
        assert store.one('jobs',job['id'])['status']=='queued'
        assert not server.worker.thread.is_alive()
    finally:
        server.shutdown();thread.join();server.harness.close();server.server_close();server.workspace_lock.close()


def test_progress_metering_follows_all_frozen_managed_channels(tmp_path):
    store=Store(tmp_path)
    for primary,supplemental,expected in [('bocha',[],True),('zhipu',[],True),('native',['tavily'],True),('native',[],False)]:
        run=store.create_run({'title':'搜索进度','objective':'核对','allow_web':True,
            'search_policy':{'primary_provider':primary,'supplemental_providers':supplemental}},[])
        job=store.enqueue('generate',{'run_id':run['id']})
        progress=summary(store,job['id'])
        assert progress['search_metered'] is expected
        assert progress['search_counts']=={'completed':0,'failed':0,'reserved':0}
