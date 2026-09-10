import json
import threading
import urllib.request
from contextlib import contextmanager
import pytest
from briefloop.server import make_server


@contextmanager
def running(server):
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        token=json.load(urllib.request.urlopen(base+'/api/session'))['token']
        def post(path,body):
            request=urllib.request.Request(base+path,data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','X-BriefLoop-Token':token,'Origin':base})
            return json.load(urllib.request.urlopen(request))
        yield post
    finally:
        server.shutdown();thread.join();server.harness.close();server.opencode_harness.close()
        for harness in server.bridge_harnesses.values():harness.close()
        server.runtime_bridge.close();server.server_close();server.workspace_lock.close()


def pending_claude_workspace(server):
    server.store.set_meta('settings',{**server.store.settings(),'agent_backend':'claude',
        'model':'default','model_selection_required':True})


def test_chat_runtime_choice_clears_the_pending_model_gate(tmp_path):
    server=make_server(tmp_path,port=0,paused=True)
    manager=server.bridge_harnesses['claude']
    manager._schedule=lambda sid:None  # Admission test; no model invocation.
    pending_claude_workspace(server)
    runtime={'backend':'claude','model':'default','permission':'runtime-native'}
    sid=manager.create_session('hello',runtime)['id']
    with running(server) as post:
        sent=post('/api/harness/message',{'session_id':sid,'text':'hello','runtime':runtime,'message_id':'one-message'})
        assert sent['status']=='queued'
        assert sent['id']=='one-message'
        assert len(manager.snapshot(sid)['messages'])==1
        settings=server.store.settings()
        assert settings['model_selection_required'] is False
        assert (settings['model'],settings['agent_backend'])==('default','claude')
        assert server.store.runtime_config()['model']=='default'


def test_saving_a_model_clears_the_pending_model_gate(tmp_path):
    server=make_server(tmp_path,port=0,paused=True)
    pending_claude_workspace(server)
    with running(server) as post:
        assert post('/api/settings',{})['model_selection_required'] is True
        saved=post('/api/settings',{'agent_backend':'codex','model':'gpt-5.6-luna'})
        assert saved['model_selection_required'] is False
        assert server.store.runtime_config()['model']=='gpt-5.6-luna'


def test_switching_runtime_still_asks_for_a_model(tmp_path):
    server=make_server(tmp_path,port=0,paused=True)
    with running(server) as post:
        # The runtime switch clears the model and keeps the gate until a model is saved.
        switched=post('/api/settings',{'agent_backend':'claude','model':'','model_selection_required':True})
        assert switched['model_selection_required'] is True
        with pytest.raises(ValueError,match='报告和学习'):
            server.store.runtime_config()
