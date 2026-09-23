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
    job=store.enqueue('learn',{'feedback_ids':[],'skill_id':None,'targets':['analyst'],'k':1,
        'authorization':{'kind':'manual','rounds':2,'fingerprint':'x'*64,'max_trial_generations':12}})
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


@pytest.mark.parametrize('drift',['old_fact_check','old_code_hash'])
def test_saved_learning_conditions_are_not_rewritten_on_drift(tmp_path,drift):
    store=Store(tmp_path/'workspace');source=store.add_source('Facts','Synthetic evidence')
    if drift=='old_fact_check':
        store.set_meta('settings',{**store.settings(),'agent_backend':'opencode',
                                   'model':'synthetic/model','model_selection_required':False})
    case=store.create_run({'title':'Report','objective':'Summarize',
                           'allow_web':drift=='old_fact_check',
                           'fact_check':drift=='old_fact_check'},[source['id']])
    payload={'runtime':store.runtime_config(),'role_models':store.role_model_config(),
             'agent_backend':store.settings()['agent_backend']}
    folder=tmp_path/'case';folder.mkdir()
    frozen=learning._conditions(store,case,payload)
    if drift=='old_fact_check':frozen['requirements']['fact_check']=True
    else:frozen['common_code']['store.py']='0'*64
    record=folder/'conditions.json'
    record.write_text(dump({'origin_run_id':case['id'],'conditions':frozen}))
    before=record.read_bytes();run_count=len(store.rows('SELECT id FROM runs'))
    with pytest.raises(ValueError,match='比较条件已变化'):
        learning._prepare_case(store,{**case,'learning_source_ids':[source['id']]},payload,folder)
    assert record.read_bytes()==before
    assert len(store.rows('SELECT id FROM runs'))==run_count


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
