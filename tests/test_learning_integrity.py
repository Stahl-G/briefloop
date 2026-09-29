"""Learning conditions and interrupted initialization are durable, not inferred."""
import json
from pathlib import Path
import pytest
from briefloop.store import Store,OfflineFactCheck,dump
from briefloop import learning,document_workflows
from briefloop.evidence import create_claim
from briefloop.review_learning import source_snapshot
from briefloop.runtime import Worker
from wikiskill import feedback_loop,product


def test_feedback_begin_recovers_after_start_before_feedback(tmp_path,monkeypatch):
    real=product.start
    def interrupted(*args,**kwargs):real(*args,**kwargs);raise InterruptedError('after configuration')
    monkeypatch.setattr(product,'start',interrupted)
    with pytest.raises(InterruptedError):feedback_loop.begin(tmp_path/'study',feedback=[{'text':'Use explicit units','source':'f1'}])
    monkeypatch.setattr(product,'start',real)
    state=feedback_loop.begin(tmp_path/'study',feedback=[{'text':'Use explicit units','source':'f1'}])
    assert state['phase']=='maintainer' and len(state['feedback'])==1
    assert feedback_loop.begin(tmp_path/'study',feedback=[{'text':'Use explicit units','source':'f1'}])==state


@pytest.mark.parametrize('point',['config','feedback','feedback_mode','phase'])
def test_feedback_initialization_each_boundary_is_recoverable(tmp_path,monkeypatch,point):
    root=tmp_path/'study';feedback=[{'text':'Use units','source':'f1'},{'text':'State next action','source':'f2'}]
    write,event=product.write,product._event;fired=[]
    def interrupt_write(path,*args,**kwargs):
        result=write(path,*args,**kwargs)
        if point=='config' and Path(path).name=='config.json' and not fired:fired.append(True);raise InterruptedError(point)
        return result
    def interrupt_event(root,state,kind,*args,**kwargs):
        result=event(root,state,kind,*args,**kwargs)
        if kind==point and not fired:fired.append(True);raise InterruptedError(point)
        return result
    monkeypatch.setattr(product,'write',interrupt_write);monkeypatch.setattr(product,'_event',interrupt_event)
    with pytest.raises(InterruptedError):feedback_loop.begin(root,feedback=feedback)
    state=feedback_loop.begin(root,feedback=feedback)
    assert state['phase']=='maintainer' and [row['source'] for row in state['feedback']]==['f1','f2']
    assert feedback_loop.begin(root,feedback=feedback)==state
    with pytest.raises(ValueError,match='conditions changed'):feedback_loop.begin(root,feedback=[{'text':'Different','source':'f1'}])


def test_internal_clone_rejects_forged_token_and_corrupt_saved_snapshot(tmp_path):
    store=Store(tmp_path);source=store.add_source('Facts','Synthetic')
    req={'title':'Report','objective':'Summarize'}
    with pytest.raises(ValueError,match='internal learning'):store.create_run(req,[source['id']],_learning_clone=['token','run'])
    run=store.create_run(req,[source['id']]);saved=json.loads(run['requirements']);saved['workflow_snapshot']['role_instructions']['writing']='tampered'
    with store.tx() as c:c.execute('UPDATE runs SET requirements=? WHERE id=?',(dump(saved),run['id']))
    with pytest.raises(ValueError,match='哈希'):store._create_learning_run(run['id'],[source['id']])
