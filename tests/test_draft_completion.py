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
