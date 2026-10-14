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


def test_evidence_child_fact_check_sources_are_admitted_without_accepting_unrelated_sources(tmp_path, monkeypatch):
    from briefloop import research_plan, tavily
    from briefloop.draft_completion import binding, accept_stage_sources, require_fact_check_settled
    store = Store(tmp_path)
    run = store.create_run({'title': 'R', 'objective': 'Public update', 'allow_web': True, 'fact_check': True,
        'completion_mode': 'direct', 'search_policy': {'primary_provider': 'tavily', 'coverage_mode': 'primary_only'},
        'research_budget': {'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2}}, [], research_protocol='quality_v1')
    research_plan.freeze(store, run['id'])
    research_plan.finish_round(store, run['id'])
    stage = research_plan.admit_fact_check(store, run['id'], {'kind': 'task_reserve',
        'limits': {'search_requests': 2, 'candidate_urls': 10, 'source_pages': 2}})
    base = store.publish(run['id'], {'title': 'R', 'markdown': 'A public update.'})
    parent = store.enqueue('assess', {'run_id': run['id'], 'version_id': base['id'],
        'runtime': {'model': 'synthetic-model'}, 'agent_backend': 'codex',
        'continuation_of': 'job_original', 'fast_evidence': True, 'checks_input': binding(store, base['id'])})
    child = store.publish(run['id'], {'title': 'R', 'markdown': base['markdown'],
        'editor_document': json.loads(base['editor_document']) if base.get('editor_document') else None},
        version_id='brief_' + parent['id'][4:] + '_evidence', parent_id=base['id'])
    check = store.enqueue('fact_check', {'run_id': run['id'], 'version_id': child['id'],
        'runtime': {'model': 'synthetic-model'}, 'agent_backend': 'codex',
        'parent_job_id': parent['id'], 'stage_id': stage['stage_id']})
    response = {'request_id': 'public-check', 'results': [{'url': 'https://example.test/update', 'raw_content': 'Public update.'}]}
    monkeypatch.setattr(tavily, '_post', lambda *a, **k: (response, json.dumps(response).encode()))
    extracted = tavily.extract(store, [response['results'][0]['url']], run_id=run['id'])
    with pytest.raises(ValueError, match='联网事实核查未完成'):
        require_fact_check_settled(store, parent)
    store.update_job(check['id'], 'complete')
    research_plan.finish_fact_check(store, run['id'], status='completed')
    accept_stage_sources(store, parent)
    require_fact_check_settled(store, parent)
    assert extracted['sources'][0]['id'] in {item['id'] for item in binding(store, base['id'])['sources']}
    accept_stage_sources(store, parent)  # A resume reuses the admitted snapshot.
    outsider = store.add_source('Unrelated', 'Unrelated source.')
    store.attach_source(run['id'], outsider['id'])
    with pytest.raises(Conflict, match='不属于本次事实核查'):
        accept_stage_sources(store, parent)



def test_unlocatable_evaluator_quote_gets_one_repair_then_is_dropped_not_fatal(tmp_path,monkeypatch):
    # A paraphrased report_quote used to fail the whole check job (two real
    # direct-mode samples); now one repair turn, then the quote is dropped.
    from briefloop.runtime import stage_job
    monkeypatch.setattr('briefloop.review_capability.review_available',lambda *a,**k:False)
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'auto_learn':False,'company_context_enabled':False,
                               'model':'fixture-model','model_selection_required':False})
    source=store.add_source('Synthetic source','Public synthetic material only.')
    run=store.create_run({'title':'Synthetic','objective':'Explain the source','completion_mode':'draft_first',
                          'writing_mode':'general','fact_check':False},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex','auto_revision':False})
    runtime=ControlledRuntime(store);worker=Worker(store,runtime)
    brief=store.one('briefs',generate(store,job,worker)['version_id'])
    original=runtime.execute;prompts=[]
    def paraphrasing(job, prompt, folder, on_tick=lambda:None, **kwargs):
        result=original(job,prompt,folder,on_tick,**kwargs)
        prompts.append(prompt);value=json.loads((folder/'assessment.json').read_text())
        value['findings']=[{'dimension':'evidence','severity':'minor','description':'Needs a date',
                            'report_quote':'A paraphrase that is not in the report'},
                           {'dimension':'expression','severity':'minor','description':'Wrong block','block_ids':['blk_missing']}]
        (folder/'assessment.json').write_text(dump(value))
        return result
    runtime.execute=paraphrasing
    folder=tmp_path/'evaluation';folder.mkdir()
    assess=store.enqueue('assess',{'version_id':brief['id'],'agent_backend':'codex'})
    worker.assess_version(stage_job(store,store.one('jobs',assess['id']),'evaluator',mode='single'),brief,folder,'codex')
    assert len(prompts)==2 and 'report_quote' in prompts[1]
    saved=json.loads(store.rows('SELECT data FROM assessments WHERE version_id=?',(brief['id'],))[0]['data'])
    assert saved['findings'][0]['description']=='Needs a date' and not saved['findings'][0].get('report_quote')
    assert saved['findings'][1]['description']=='Wrong block' and not saved['findings'][1].get('block_ids')
    assert store.rows("SELECT * FROM events WHERE kind='assessment_quotes_dropped'")
