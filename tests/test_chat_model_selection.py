import json
import threading
import urllib.request
import pytest
from briefloop.server import make_server


def test_explicit_chat_model_is_independent_of_pending_report_default(tmp_path):
    server=make_server(tmp_path,port=0,paused=True)
    manager=server.bridge_harnesses['claude']
    manager._schedule=lambda sid:None  # Admission test; no model invocation.
    server.store.set_meta('settings',{**server.store.settings(),'agent_backend':'claude',
        'model':'default','model_selection_required':True})
    runtime={'backend':'claude','model':'default','permission':'runtime-native'}
    sid=manager.create_session('hello',runtime)['id']
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        token=json.load(urllib.request.urlopen(base+'/api/session'))['token']
        body={'session_id':sid,'text':'hello','runtime':runtime,'message_id':'one-message'}
        def send():
            request=urllib.request.Request(base+'/api/harness/message',data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','X-BriefLoop-Token':token,'Origin':base})
            return json.load(urllib.request.urlopen(request))
        assert send()['status']=='queued'
        assert send()['id']=='one-message'
        assert len(manager.snapshot(sid)['messages'])==1
        assert server.store.settings()['model_selection_required'] is True
        with pytest.raises(ValueError,match='报告和学习'):
            server.store.runtime_config()
    finally:
        server.shutdown();thread.join();server.harness.close();server.opencode_harness.close()
        for harness in server.bridge_harnesses.values():harness.close()
        server.runtime_bridge.close();server.server_close();server.workspace_lock.close()
