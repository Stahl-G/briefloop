"""No server/model launch: verify identity, bounded discovery and separate data."""
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from briefloop.store import Store, dump
from briefloop.workspaces import list_workspaces, open_workspace


def test_workspace_open_checks_identity_and_launches_paused_without_touching_old_service(tmp_path, monkeypatch):
    current=Store(tmp_path/'nearby'/'current')
    sibling=Store(tmp_path/'nearby'/'sibling')
    hidden=Store(tmp_path/'unregistered'/'deep'/'workspace')
    current.add_source('kept','原工作区材料')
    target=tmp_path/'elsewhere'/'new'
    target.mkdir(parents=True)
    # A stale marker points at the current workspace, not the requested target.
    (target/'server.json').write_text(dump({'pid':os.getpid(),'url':'http://127.0.0.1:19001'}))
    state={'http://127.0.0.1:19001':current}
    starts=[];signals=[]
    def api(url,endpoint):
        store=state[url]
        if endpoint=='/api/runtime':return {'server_pid':os.getpid(),'paused':url.endswith('19002')}
        if endpoint=='/api/workspaces':return {'current':{'path':str(store.root),'workspace_id':store.meta('workspace_id')}}
        return {'workspace_id':store.meta('workspace_id')}
    def launch(command,**kwargs):
        starts.append(command)
        assert command[1:4]==['-m','briefloop','start']
        assert command[-3:]==['--port','0','--paused']
        root=Path(command[command.index('--workspace')+1]);opened=Store(root)
        state['http://127.0.0.1:19002']=opened
        (root/'server.json').write_text(dump({'pid':os.getpid(),'url':'http://127.0.0.1:19002','workspace_id':opened.meta('workspace_id')}))
        return SimpleNamespace(returncode=0,stdout='started',stderr='')
    monkeypatch.setattr('briefloop.workspaces._read_api',api)
    monkeypatch.setattr('briefloop.workspaces.subprocess.run',launch)
    monkeypatch.setattr('briefloop.workspaces.os.kill',lambda pid,sig:signals.append((pid,sig)))
    before=list_workspaces(current)
    assert {row['path'] for row in before['workspaces']}=={str(current.root),str(sibling.root)}
    assert str(hidden.root) not in str(before)
    opened=open_workspace(current,str(target))
    assert opened['path']==str(target) and not opened['reused'] and opened['paused']
    assert opened['workspace_id']!=current.meta('workspace_id')
    assert len(current.rows('SELECT * FROM sources'))==1
    assert not Store(target).rows('SELECT * FROM sources')
    assert len(starts)==1 and all(sig==0 for _,sig in signals)
    # A matching live service is reused without a second launch.
    assert open_workspace(current,str(target))['reused']
    assert len(starts)==1
    assert str(target) in {row['path'] for row in list_workspaces(current)['workspaces']}
    assert str(current.root) in {row['path'] for row in list_workspaces(Store(target))['workspaces']}
    with pytest.raises(ValueError,match='不存在'):
        open_workspace(current,'not-created')
    created=open_workspace(current,'relative-created',create=True)
    assert created['path']==str(current.root.parent/'relative-created')
    assert (Path(created['path'])/'briefloop.db').is_file()


def test_workspace_create_only_creates_siblings(tmp_path):
    current=Store(tmp_path/'nearby'/'current')
    outside=tmp_path/'nearby'/'nested'/'new'
    with pytest.raises(ValueError,match='同级'):
        open_workspace(current,str(outside),create=True)
    assert not outside.exists()
    assert current.root.parent==(tmp_path/'nearby').resolve()
