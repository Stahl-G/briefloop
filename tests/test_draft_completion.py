"""Real Store/Worker stages with finite controlled runtimes; zero model calls."""
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from briefloop.store import Store, dump, Conflict
from briefloop.runtime import Worker
from briefloop.draft_completion import status, enqueue
from briefloop.document_model import markdown_document


class ControlledRuntime:
    def __init__(self, store, *, revise=False):
        self.store=store;self.cancelled=threading.Event();self.calls=[];self.revise=revise
        self.after_score=None;self.fail_score=False;self.tick=None

    def cancel(self):
        self.cancelled.set()

    def execute(self, job, prompt, folder, on_tick=lambda:None, **kwargs):
        self.calls.append(folder.name)
        if job.get('review_id'):
            from briefloop.review import get_review
            from briefloop.deliverable_spec import clause_items
            row=get_review(self.store,job['review_id'])
            target=json.loads((folder/'packet/target.json').read_text())
            context=json.loads((folder/'packet/assessment-context.json').read_text())
            value={'version_id':target['version_id'],'fingerprint':row['fingerprint'],'status':'complete',
                   'summary':'Controlled review fixture, not a semantic quality measurement','coverage_scan_complete':True,
                   'clause_checks':[{'clause_id':item['clause_id'],'status':'covered','reason':'Synthetic check'} for item in clause_items(target['requirements'])],
                   'requirement_checks':[{'requirement_id':item['requirement_id'],'status':'covered','reason':'Synthetic check'} for item in target['requirements']['requirement_items']],
                   'assessment':{'brief_hash':target['brief_hash'],'status':'complete','summary':'Synthetic review',
                                 'overall':'达到要求','evidence':3,'coverage':3,'analysis':3,'expression':3,
                                 'checks':[{'id':item['id'],'status':'passed','reason':'Synthetic check'} for item in context['assessment_checks']]}}
            (folder/'review.json').write_text(dump(value))
        elif folder.name in ('evaluation','revision-evaluation'):
            if self.fail_score:
                self.fail_score=False
                raise InterruptedError('controlled interruption')
            packet=json.loads((folder/'input.json').read_text())
            brief=packet['brief']
            value={'brief_hash':brief['hash'],'status':'complete','summary':'Synthetic source comparison',
                   'overall':'建议修改' if self.revise else '达到要求',
                   'evidence':3,'coverage':3,'analysis':3,'expression':3,
                   'checks':[{'id':item['id'],'status':'passed','reason':'Synthetic checked requirement'} for item in packet['assessment_checks']]}
            (folder/'assessment.json').write_text(dump(value))
            if self.after_score:self.after_score(brief)
        elif folder.name=='revision':
            (folder/'draft.json').write_text(dump({'title':'Synthetic revised','markdown':'Revised explanation of the supplied source.'}))
            (folder/'responses.json').write_text('[]')
        else:
            schema=json.loads((folder/'reader_contract.schema.json').read_text())
            spec=json.loads((folder/'input.json').read_text())['deliverable_spec']
            contract={'source_fingerprint':schema['properties']['source_fingerprint']['const'],
                      'clauses':[{'requirement_id':item['requirement_id'],'kind':'reader_content',
                                  'source_quote':item['text'],'instruction':item['text']} for item in spec['requirement_items']]}
            (folder/'plan.json').write_text(dump({'reader_contract':contract}))
            (folder/'draft.json').write_text(dump({'title':'Synthetic draft','markdown':'A saved explanation of the supplied source.'}))
            if self.tick:self.tick()
            on_tick()
        return {'status':'complete','controlled_runtime':True}


def setup(tmp_path, *, internal=False, revise=False, fact_check=False):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'auto_learn':False,'company_context_enabled':False})
    source=store.add_source('Synthetic source','Public synthetic material only.')
    run=store.create_run({'title':'Synthetic','objective':'Explain the source','completion_mode':'draft_first',
                          'writing_mode':'internal_report' if internal else 'general',
                          'allow_web':fact_check,'fact_check':fact_check},[source['id']],**({'research_protocol':'quality_v1'} if fact_check else {}))
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex','auto_revision':True})
    runtime=ControlledRuntime(store,revise=revise);worker=Worker(store,runtime)
    return store,source,run,job,runtime,worker


def generate(store,job,worker):
    store.update_job(job['id'],'running')
    result=worker.generate(job)
    store.update_job(job['id'],'complete',result=result)
    return result


def finish(store,job,worker):
    store.update_job(job['id'],'running')
    result=worker.assess(job)
    store.update_job(job['id'],'complete',result=result)
    return result


def test_opt_in_soft_target_and_draft_is_saved_downloadable_without_checks(tmp_path,monkeypatch):
    store,source,run,job,runtime,worker=setup(tmp_path,internal=True)
    req=json.loads(run['requirements'])
    assert req['target_minutes']==10 and req['hard_timeout_minutes']==0
    legacy=store.create_run({'title':'Old quick','objective':'Explain','research_tier':'quick'},[source['id']])
    assert json.loads(legacy['requirements'])['target_minutes']==store.settings()['timeout_minutes']
    assert json.loads(legacy['requirements'])['completion_mode']=='standard'
    custom=store.create_run({'title':'Custom','objective':'Explain','completion_mode':'draft_first','target_minutes':17},[source['id']])
    assert json.loads(custom['requirements'])['target_minutes']==17
    # Deliberately exceed both the old 180-second checkpoint and the soft target.
    clock=[0.0];monkeypatch.setattr('briefloop.runtime.time.monotonic',lambda:clock[0])
    worker.thread=SimpleNamespace(is_alive=lambda:True)
    runtime.tick=lambda:clock.__setitem__(0,701.0)
    worker._admit_fact_check=lambda *args:pytest.fail('draft-first must not admit fact-check')
    result=generate(store,job,worker)
    assert result['checks_state']=='deferred'
    assert len(runtime.calls)==1 and not store.rows("SELECT * FROM jobs WHERE kind IN ('review','fact_check','assess')")
    assert not store.rows('SELECT * FROM assessments')
    assert status(store,result['version_id'])['state']=='deferred'
    from briefloop.export_jobs import enqueue_export,generate_word
    export=enqueue_export(store,result['version_id']);store.update_job(export['id'],'running')
    exported=generate_word(store,export,threading.Event())
    with ZipFile(store.root/exported['path']) as package:
        assert 'word/document.xml' in package.namelist()


def test_continuation_freezes_config_deduplicates_and_revises_once_per_chain(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path,revise=True)
    draft=generate(store,job,worker);original=json.loads(job['payload'])
    store.set_meta('settings',{**store.settings(),'model':'changed-after-draft','auto_revision':False})
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=list(pool.map(lambda _:enqueue(Store(tmp_path),draft['version_id']),range(4)))
    assert len({row['id'] for row in jobs})==1
    check=jobs[0];payload=json.loads(check['payload'])
    assert payload['runtime']==original['runtime'] and payload['auto_revision'] is True
    result=finish(store,check,worker)
    assert result['checks_state']=='complete' and result['revision_status']=='complete'
    assert len(store.rows('SELECT id FROM briefs'))==2
    assert runtime.calls.count('revision')==1
    assert worker.continue_checks(draft['version_id'])['id']==check['id']
    # Reuse the already checked revision. A later user version can be checked,
    # but cannot grant a second automatic rewrite to the same original chain.
    assert worker.continue_checks(result['version_id'])['id']==check['id']
    edited=store.revise(result['version_id'],editor_document=markdown_document('User amendment.'))
    second=worker.continue_checks(edited['id']);next_result=finish(store,second,worker)
    assert next_result['revision_status']=='already_used'
    assert runtime.calls.count('revision')==1
    assert store.one('briefs',draft['version_id'])['markdown']=='A saved explanation of the supplied source.'


def test_source_change_invalidates_queue_and_old_score_is_not_reused(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    other=store.add_source('Second source','New scope');store.attach_source(run['id'],other['id'])
    before=len(runtime.calls)
    with pytest.raises(Conflict,match='来源'):worker.assess(check)
    assert len(runtime.calls)==before
    store.update_job(check['id'],'failed',error='Source scope changed')
    new=worker.continue_checks(draft['version_id']);finish(store,new,worker)
    third=store.add_source('Third source','Another scope');store.attach_source(run['id'],third['id'])
    newest=worker.continue_checks(draft['version_id']);finish(store,newest,worker)
    assert runtime.calls.count('evaluation')==2


def test_interrupted_check_resumes_same_job_and_user_edit_is_preserved(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path,revise=True)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    runtime.fail_score=True
    with pytest.raises(InterruptedError):worker.assess(check)
    store.update_job(check['id'],'interrupted',error='Synthetic interruption')
    assert worker.continue_checks(draft['version_id'])['id']==check['id']
    resumed=worker.resume(check['id'])
    runtime.after_score=lambda brief:store.revise(brief['id'],editor_document=markdown_document('User edit wins.'))
    result=finish(store,resumed,worker)
    assert result['revision_status']=='user_edit'
    assert 'revision' not in runtime.calls
    assert store.rows('SELECT * FROM briefs ORDER BY rowid DESC')[0]['markdown']=='User edit wins.'


def test_stop_unwind_and_cross_stage_resume_do_not_overlap(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path)
    draft=generate(store,job,worker)
    worker.current=job['id']
    with pytest.raises(ValueError,match='仍在停止'):worker.continue_checks(draft['version_id'])
    worker.current=None
    check=worker.continue_checks(draft['version_id'])
    store.update_job(job['id'],'cancelled')
    with pytest.raises(ValueError,match='完整检查仍在执行'):worker.resume(job['id'])
    store.update_job(check['id'],'running');worker.current=check['id']
    worker.stop_job(check['id'])
    with pytest.raises(ValueError,match='仍在停止'):worker.resume(check['id'])
    worker._settle_job(check['id'],'complete',result={'late':True},runtime=runtime)
    assert store.one('jobs',check['id'])['status']=='cancelled'
    assert store.one('briefs',draft['version_id'])['markdown']


@pytest.mark.parametrize('count',[1,2])
def test_fact_check_receipts_expand_only_owned_sources_and_survive_resume(tmp_path,count):
    from briefloop import research_plan,research_budget
    from briefloop.sources import PendingSources
    from briefloop.draft_completion import accept_stage_sources,check_binding,verify_input
    store,source,run,job,runtime,worker=setup(tmp_path,fact_check=True)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    research_plan.finish_round(store,run['id'])
    stage=research_plan.admit_fact_check(store,run['id'],{'kind':'task_reserve','limits':{'search_requests':1,'candidate_urls':3,'source_pages':3}})
    child=store.enqueue('fact_check',{'run_id':run['id'],'version_id':draft['version_id'],
                        'parent_job_id':check['id'],'stage_id':stage['stage_id']})
    # Single direct fetch and multi-source provider extraction both admit through
    # this real transaction. Reserve only; no HTTP/client/model is invoked.
    urls=[f'https://example.invalid/synthetic-{n}' for n in range(count)]
    claim=research_budget.claim_pages(store,run['id'],urls)
    pending=PendingSources(store)
    added=[pending.add_source('Synthetic retrieved source','New evidence only.',url=url) for url in urls]
    assert pending.admit(run['id'],claim['reservation'],claim_owner=claim['owner'],claimed_urls=urls)
    receipt=research_plan.pending_requests(store,run['id'])[claim['reservation']['request_id']]
    assert {row['id'] for row in receipt['admitted_sources']}=={row['id'] for row in added}
    research_plan.finish_fact_check(store,run['id'],status='completed',summary='Controlled receipt fixture')
    store.update_job(child['id'],'complete',result={'version_id':draft['version_id'],'stage_id':stage['stage_id']})
    # Simulate interruption after material admission, before binding expansion.
    store.update_job(check['id'],'interrupted',error='Synthetic interrupted after acquisition')
    resumed=worker.resume(check['id']);result=finish(store,resumed,worker)
    assert result['checks_state']=='complete'
    assert len(check_binding(store,check)['sources'])==count+1
    assert worker.continue_checks(draft['version_id'])['id']==check['id']
    before=(worker.folder(check)/'checks-input.json').read_bytes()
    accept_stage_sources(store,check);verify_input(store,check)
    assert (worker.folder(check)/'checks-input.json').read_bytes()==before
    # A concurrent manual/imported source is not this child's metered receipt.
    unrelated=store.add_source('Unrelated upload','Not retrieved by the check stage.')
    store.attach_source(run['id'],unrelated['id'])
    with pytest.raises(Conflict,match='不属于本次'):accept_stage_sources(store,check)
    assert (worker.folder(check)/'checks-input.json').read_bytes()==before


def test_owned_source_receipt_cannot_hide_mutation_of_original_material(tmp_path):
    from briefloop.draft_completion import accept_stage_sources
    store,source,run,job,runtime,worker=setup(tmp_path)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    (store.root/source['path']).write_text('Changed outside the admitted source version.')
    with pytest.raises(Conflict,match='原来源'):accept_stage_sources(store,check)


@pytest.mark.parametrize('interruption',['failed','cancelled'])
def test_failed_fact_check_cannot_be_hidden_by_review_and_resumes_same_child(tmp_path,interruption):
    from briefloop import research_plan
    from briefloop.evidence import create_span,create_claim
    store,source,run,job,runtime,worker=setup(tmp_path,fact_check=True)
    draft=generate(store,job,worker)
    research_plan.finish_round(store,run['id'])
    span=create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':1,'end_line':1}})
    create_claim(store,run['id'],{'statement':'Public synthetic material only.','kind':'fact',
                  'supports':[{'span_id':span['id'],'supports_quote':'Public synthetic material only.'}]})
    check=worker.continue_checks(draft['version_id']);attempts=[]
    def controlled_fact_stage(child):
        attempts.append(child['id'])
        if len(attempts)==1:
            if interruption=='cancelled':
                research_plan.finish_fact_check(store,run['id'],status='cancelled',summary='Synthetic user stop')
                raise InterruptedError('controlled stop')
            raise RuntimeError('controlled network failure, no network invoked')
        research_plan.finish_fact_check(store,run['id'],status='completed',summary='Controlled stage completion, no semantic claim')
        return {'version_id':draft['version_id']}
    worker.run_fact_check=controlled_fact_stage
    with pytest.raises((ValueError,InterruptedError)):finish(store,check,worker)
    store.update_job(check['id'],interruption,error='Controlled incomplete stage')
    assert status(store,draft['version_id'])['state']==interruption
    assert not store.rows('SELECT * FROM assessments')
    assert len(attempts)==1
    result=finish(store,worker.resume(check['id']),worker)
    assert result['checks_state']=='complete'
    assert attempts==[attempts[0],attempts[0]]
    stage=research_plan.frozen(store,run['id'])['fact_check']
    if interruption=='cancelled':
        assert stage['interrupted_closures'][0]['status']=='cancelled'


@pytest.mark.parametrize('complete_on_retry',[True,False])
def test_explicit_resume_continues_incomplete_interactive_evaluation_only_once(tmp_path,complete_on_retry):
    from tests.test_interactive_runtime import FakeHarness
    from briefloop.interactive_runtime import InteractiveRuntime
    store,source,run,job,runtime,worker=setup(tmp_path)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    class Harness(FakeHarness):
        def start_internal(self,text,**kwargs):
            super().start_internal(text,**kwargs)
            folder=Path(kwargs['cwd']);packet=json.loads((folder/'input.json').read_text());brief=packet['brief']
            value={'brief_hash':brief['hash'],'status':'incomplete','overall':'评估未完成','summary':'Synthetic incomplete'}
            if complete_on_retry and len(self.starts)>1:
                value.update(status='complete',overall='达到要求',evidence=3,coverage=3,analysis=3,expression=3,
                             checks=[{'id':item['id'],'status':'passed','reason':'Synthetic check'} for item in packet['assessment_checks']])
            (folder/'assessment.json').write_text(dump(value));self.finish(kwargs['session_id'])
    harness=Harness();worker.runtime=InteractiveRuntime(store,harness)
    def invoke(current):
        try:return finish(store,current,worker)
        except ValueError as exc:store.update_job(check['id'],'failed',error=str(exc));return None
    invoke(check);assert len(harness.starts)==1
    original=(worker.folder(check)/'evaluation/assessment.json').read_bytes()
    resumed=worker.resume(check['id']);result=invoke(resumed)
    assert len(harness.starts)==2 and len(harness.sessions)==1
    assert any(path.read_bytes()==original for path in (worker.folder(check)/'evaluation/attempts').glob('*.json'))
    if complete_on_retry:
        assert result['checks_state']=='complete'
    else:
        invoke(resumed);assert len(harness.starts)==2
        invoke(worker.resume(check['id']));assert len(harness.starts)==3


def test_incomplete_review_is_immutable_and_same_attempt_does_not_relaunch(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path,internal=True)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    original=runtime.execute;seen=[]
    def incomplete(stage,prompt,folder,on_tick=lambda:None,**kwargs):
        result=original(stage,prompt,folder,on_tick,**kwargs)
        if stage.get('review_id'):
            seen.append(stage['review_id']);path=folder/'review.json'
            value=json.loads(path.read_text());value.update(status='incomplete',summary='Synthetic incomplete')
            value['assessment'].update(status='incomplete',overall='评估未完成');path.write_text(dump(value))
        return result
    runtime.execute=incomplete
    def invoke(current):
        with pytest.raises(ValueError):finish(store,current,worker)
        store.update_job(check['id'],'failed',error='Synthetic incomplete review')
    invoke(check);old=store.rows('SELECT result FROM reviews WHERE id=?',(seen[0],))[0]['result']
    resumed=worker.resume(check['id']);invoke(resumed);invoke(resumed)
    assert len(seen)==2 and seen[0]!=seen[1]
    assert store.rows('SELECT result FROM reviews WHERE id=?',(seen[0],))[0]['result']==old


def test_incomplete_revision_assessment_resumes_without_rewriting_body(tmp_path):
    store,source,run,job,runtime,worker=setup(tmp_path,revise=True)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id'])
    original=runtime.execute;seen=[]
    def incomplete_once(stage,prompt,folder,on_tick=lambda:None,**kwargs):
        result=original(stage,prompt,folder,on_tick,**kwargs)
        if folder.name=='revision-evaluation':
            seen.append(folder)
            if len(seen)==1:
                path=folder/'assessment.json';value=json.loads(path.read_text())
                value.update(status='incomplete',overall='评估未完成');path.write_text(dump(value))
        return result
    runtime.execute=incomplete_once
    with pytest.raises(ValueError):finish(store,check,worker)
    store.update_job(check['id'],'failed',error='Synthetic incomplete recheck')
    result=finish(store,worker.resume(check['id']),worker)
    assert result['checks_state']=='complete'
    assert len(seen)==2 and runtime.calls.count('revision')==1
    assert len(store.rows('SELECT id FROM briefs'))==2


def test_status_reads_saved_metadata_without_opening_source_bodies(tmp_path,monkeypatch):
    store,source,run,job,runtime,worker=setup(tmp_path)
    draft=generate(store,job,worker);check=worker.continue_checks(draft['version_id']);finish(store,check,worker)
    monkeypatch.setattr(store,'source_text',lambda *args,**kwargs:pytest.fail('polling must not re-read sources'))
    monkeypatch.setattr('briefloop.media.source_files',lambda *args,**kwargs:pytest.fail('polling must not open originals'))
    assert status(store,draft['version_id'])['state']=='complete'
