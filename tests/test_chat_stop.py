"""Chat stops use the live worker rather than a second queue or DB-only update."""
import json
import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from briefloop.store import Store


def test_chat_and_cli_stop_live_worker_children_without_touching_other_jobs(tmp_path):
    from briefloop._entrypoint import command
    from briefloop.chat_tools import workspace_action
    from briefloop.native_roles import run_tool
    from briefloop.server import make_server, _close_service
    store=Store(tmp_path)
    store.update_settings({'agent_backend':'briefloop-native','model':'fixture/model','model_selection_required':False})
    source=store.add_source('Synthetic','Local source')
    run=store.create_run({'title':'Synthetic','objective':'Test cancellation','allow_web':False,'fact_check':False},[source['id']])
    server=make_server(tmp_path,port=0,paused=True)
    # A real heartbeat makes service discovery ready without starting paid queues.
    worker=server.worker
    worker.thread=threading.Thread(target=worker.stopping.wait,daemon=True)
    worker.thread.start()
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    (tmp_path/'server.json').write_text(json.dumps({'pid':os.getpid(),'url':f'http://127.0.0.1:{server.server_port}',
                                                'workspace_id':store.meta('workspace_id')}))
    parent=store.enqueue('generate',{'run_id':run['id']})
    child=store.enqueue('generate',{'run_id':run['id'],'parent_job_id':parent['id']})
    unrelated=store.enqueue('generate',{'run_id':run['id']})
    store.update_job(parent['id'],'running');store.update_job(child['id'],'running')
    cancelled=[]
    for job in (parent,child):
        worker._generation_jobs[job['id']]=(SimpleNamespace(join=lambda **kwargs:None),
            SimpleNamespace(cancel=lambda jid=job['id']:cancelled.append(jid)))
    config={'native_role':'chat','permission':'read-only','session_id':'synthetic-chat',
            '_harness':SimpleNamespace(cancel_requested=lambda sid:False)}
    request={'request':{'action':'stop_job','job_id':parent['id']}}
    try:
        assert not run_tool(store,config,'workspace_action',request)['ok']
        config['permission']='workspace-write'
        report={**config,'native_role':'orchestrator','task_kind':'generate'}
        assert not run_tool(store,report,'workspace_action',request)['ok']
        result=run_tool(store,config,'workspace_action',request)
        assert result['ok'],result
        assert json.loads(result['content'][0]['text'])=={'ok':True,'job_id':parent['id'],'status':'cancelled'}
        assert set(cancelled)=={parent['id'],child['id']}
        assert store.one('jobs',child['id'])['status']=='cancelled'
        assert store.one('jobs',unrelated['id'])['status']=='queued'
        # Shell-hosted chats use the same authenticated server and worker path.
        path=tmp_path/'stop.json';path.write_text(json.dumps({'action':'stop_job','job_id':unrelated['id']}))
        reply=subprocess.run(command('tool','--workspace',tmp_path,'workspace-action','--request',path),
                             capture_output=True,text=True,timeout=20)
        assert reply.returncode==0,reply.stderr
        assert json.loads(reply.stdout)['status']=='cancelled'
        assert workspace_action(store,{'action':'stop_job','job_id':parent['id']})['status']=='cancelled'
        finished=store.enqueue('generate',{'run_id':run['id']});store.update_job(finished['id'],'complete')
        assert workspace_action(store,{'action':'stop_job','job_id':finished['id']})['status']=='complete'
        assert 'stop_job' in workspace_action(store,{'action':'capabilities'})['actions']
        with pytest.raises(ValueError,match='job_id'):
            workspace_action(store,{'action':'stop_job'})
        with pytest.raises(ValueError):
            workspace_action(store,{'action':'stop_job','job_id':'job-missing'})
    finally:
        worker._generation_jobs.clear()
        server.shutdown();serving.join();_close_service(server)
