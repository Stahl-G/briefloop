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


def test_online_fact_checked_case_has_matching_offline_learning_arms(tmp_path,monkeypatch):
    store=Store(tmp_path/'workspace');source=store.add_source('Facts','Synthetic evidence')
    store.set_meta('settings',{**store.settings(),'agent_backend':'opencode',
                               'model':'synthetic/model','model_selection_required':False})
    original=store.create_run({'title':'Report','objective':'Summarize','allow_web':True,
                               'fact_check':True,'research_budget':{'search_requests':3,
                               'candidate_urls':6,'source_pages':3}},[source['id']])
    original_requirements=json.loads(original['requirements'])
    original_sources=json.loads(original['source_ids'])
    case={**original,'learning_source_ids':[source['id']]}
    payload={'runtime':store.runtime_config(),'role_models':store.role_model_config(),
             'agent_backend':'opencode'}
    folder=tmp_path/'case';prepared=learning._prepare_case(store,case,payload,folder)
    expected=prepared['learning_conditions']
    assert expected['requirements']['allow_web'] is False
    assert expected['requirements']['fact_check'] is False
    assert expected['requirements']['research_budget']==original_requirements['research_budget']
    seen=[]
    class NoModelWorker:
        def __init__(self,store):self.store=store
        def generate(self,job,score):
            assert score is False
            run=self.store.one('runs',json.loads(job['payload'])['run_id'])
            seen.append((run,job))
            brief=self.store.publish(run['id'],{'title':'Trial','markdown':'Synthetic evidence.'},
                                     version_id='brief_'+job['id'][4:])
            return {'version_id':brief['id'],'source_snapshot':source_snapshot(self.store,run['id'])}
    monkeypatch.setattr(learning,'Worker',NoModelWorker)
    parent={'id':'job_learning_fact_check','payload':dump(payload),'_runtime':object()}
    baseline=learning._generate_trial(store,parent,prepared,None,folder/'baseline','baseline')
    candidate=learning._generate_trial(store,parent,prepared,{'id':'candidate_test','content':'Synthetic'},
                                       folder/'candidate','candidate')
    assert len(seen)==2 and baseline['run_id']!=candidate['run_id']
    for run,job in seen:
        requirements=json.loads(run['requirements'])
        assert run['mode']=='trial' and requirements==expected['requirements']
        assert json.loads(run['source_ids'])==original_sources
        assert json.loads(job['payload'])['single_evaluation'] is False
        create_claim(store,run['id'],{'statement':'Synthetic evidence.','kind':'fact'})
        assert Worker(store,object())._admit_fact_check(job,store.one('briefs',
               baseline['id'] if run['id']==baseline['run_id'] else candidate['id'])) is None
        assert store.meta('research_budget:'+run['id']) is None
        assert store.meta('fact_check_grant:'+run['id']) is None
        assert store.meta('research_requests:'+run['id']) is None
    assert [row['kind'] for row in store.rows('SELECT kind FROM jobs')]==['generate','generate']
    assert json.loads(store.one('runs',original['id'])['requirements'])==original_requirements
    assert store.source_ids(original['id'])==original_sources
    with pytest.raises(OfflineFactCheck):
        store.create_run({'title':'Offline','objective':'Summarize','allow_web':False,
                          'fact_check':True},[source['id']])
