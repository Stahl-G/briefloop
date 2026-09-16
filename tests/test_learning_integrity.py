"""Learning conditions and interrupted initialization are durable, not inferred."""
import json
from pathlib import Path
import pytest
from briefloop.store import Store,dump
from briefloop import learning,document_workflows
from briefloop.review_learning import source_snapshot
from wikiskill import feedback_loop,product


def test_trial_keeps_original_method_after_installed_method_changes(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');source=store.add_source('Facts','Synthetic project A completed three files.')
    case=store.create_run({'title':'Synthetic','objective':'Summarize','allow_web':False},[source['id']])
    original=json.loads(case['requirements'])['workflow_snapshot']
    freeze=document_workflows.freeze_workflow
    def changed(selection):
        value=freeze(selection);value['role_instructions']['writing']='Changed installed method'
        import hashlib
        value.pop('content_hash');value['content_hash']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return value
    monkeypatch.setattr(document_workflows,'freeze_workflow',changed)
    class Worker:
        def __init__(self,store):self.store=store
        def generate(self,job,score):
            rid=json.loads(job['payload'])['run_id'];run=self.store.one('runs',rid)
            assert json.loads(run['requirements'])['workflow_snapshot']==original
            brief=self.store.publish(rid,{'title':'Synthetic','markdown':'Three files.'},version_id='brief_'+job['id'][4:])
            return {'version_id':brief['id'],'source_snapshot':source_snapshot(self.store,rid)}
    monkeypatch.setattr(learning,'Worker',Worker)
    learning._generate_trial(store,{'id':'job_learning_case','payload':dump({'runtime':store.runtime_config(),'role_models':store.role_model_config(),'agent_backend':'codex'}),'_runtime':object()},case,None,tmp_path/'trial','baseline')
    # Public requests still freeze installed methods, ignoring a supplied snapshot.
    external=store.create_run({**json.loads(case['requirements']),'workflow_snapshot':original},[source['id']])
    assert json.loads(external['requirements'])['workflow_snapshot']!=original


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


def test_all_answer_only_cases_skip_without_model_or_adoption(tmp_path):
    import threading
    store=Store(tmp_path/'workspace');answer=store.add_source('Revision answer','Only a user rewrite')
    case=store.create_run({'title':'Synthetic','objective':'Summarize'},[answer['id']])
    (store.root/'sources'/(answer['id']+'.provenance.json')).write_text(dump({'usage':'revision_feedback'}))
    job=store.enqueue('learn',{'feedback_ids':[],'skill_id':None,'targets':['analyst'],'k':1})
    folder=store.root/'jobs'/job['id'];folder.mkdir()
    (folder/'context.json').write_text(dump({'feedback':[],'cases':[case['id']]}))
    class Runtime:
        cancelled=threading.Event()
        def execute(self,*args,**kwargs):raise AssertionError('All-empty batch called a model')
    result=learning.learn(store,Runtime(),job)
    assert result['comparison_skipped'] and result['history']==[] and result['active_skill'] is None
    assert not store.rows("SELECT id FROM jobs WHERE kind='generate'")
    valid=store.add_source('Actual evidence','Synthetic source fact')
    other=store.create_run({'title':'Valid','objective':'Summarize'},[valid['id']])
    eligible,skipped=learning._eligible_cases(store,[case['id'],other['id']])
    assert [row['id'] for row in eligible]==[other['id']] and skipped[0]['case_id']==case['id']


def test_internal_clone_rejects_forged_token_and_corrupt_saved_snapshot(tmp_path):
    store=Store(tmp_path);source=store.add_source('Facts','Synthetic')
    req={'title':'Report','objective':'Summarize'}
    with pytest.raises(ValueError,match='internal learning'):store.create_run(req,[source['id']],_learning_clone=['token','run'])
    run=store.create_run(req,[source['id']]);saved=json.loads(run['requirements']);saved['workflow_snapshot']['role_instructions']['writing']='tampered'
    with store.tx() as c:c.execute('UPDATE runs SET requirements=? WHERE id=?',(dump(saved),run['id']))
    with pytest.raises(ValueError,match='哈希'):store._create_learning_run(run['id'],[source['id']])


def test_legacy_method_anchors_both_arms_and_condition_drift_rejects(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');source=store.add_source('Facts','Synthetic')
    case=store.create_run({'title':'Legacy','objective':'Summarize'},[source['id']])
    req=json.loads(case['requirements']);req.pop('workflow_snapshot')
    with store.tx() as c:c.execute('UPDATE runs SET requirements=? WHERE id=?',(dump(req),case['id']))
    case=store.one('runs',case['id']);case['learning_source_ids']=[source['id']]
    payload={'runtime':store.runtime_config(),'role_models':store.role_model_config(),'agent_backend':'codex'}
    prepared=learning._prepare_case(store,case,payload,tmp_path/'case')
    snapshot=json.loads(prepared['requirements'])['workflow_snapshot']
    assert prepared['learning_origin_id']!=case['id']
    monkeypatch.setattr(document_workflows,'freeze_workflow',lambda _:(_ for _ in ()).throw(AssertionError('refroze installed method')))
    for arm in ('baseline','candidate'):
        trial=store._create_learning_run(prepared['learning_origin_id'],[source['id']])
        assert json.loads(trial['requirements'])['workflow_snapshot']==snapshot
    assert learning._prepare_case(store,case,payload,tmp_path/'case')==prepared
    with pytest.raises(ValueError,match='比较条件已变化'):
        learning._prepare_case(store,case,{**payload,'runtime':{'model':'different'}},tmp_path/'case')
