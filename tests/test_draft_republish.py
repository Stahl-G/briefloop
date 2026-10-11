"""Same-body metadata saves during writing publish once, at the end of the turn."""
import json
import threading

from briefloop.store import Store, dump
from briefloop.runtime import Worker


class Saver:
    def __init__(self, source_id):
        self.cancelled=threading.Event();self.source_id=source_id

    def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
        schema=json.loads((folder/'reader_contract.schema.json').read_text(encoding='utf-8'))
        spec=json.loads((folder/'input.json').read_text(encoding='utf-8'))['deliverable_spec']
        contract={'source_fingerprint':schema['properties']['source_fingerprint']['const'],
                  'clauses':[{'requirement_id':item['requirement_id'],'kind':'reader_content',
                              'source_quote':item['text'],'instruction':item['text']} for item in spec['requirement_items']]}
        (folder/'plan.json').write_text(dump({'reader_contract':contract}),encoding='utf-8')
        body={'title':'Synthetic','markdown':'Revenue was USD 12 million.'}
        (folder/'draft.json').write_text(dump(body),encoding='utf-8');on_tick()
        for locator in ('line 1','line 1-1','line 1 to 1'):
            (folder/'draft.json').write_text(dump({**body,'citations':[{'source_id':self.source_id,'locator':locator}]}),encoding='utf-8')
            on_tick()
        return {'status':'complete','returncode':0}


def test_metadata_only_rewrites_do_not_spawn_a_version_per_save(tmp_path):
    store=Store(tmp_path)
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False,'auto_learn':False,'model':'synthetic','model_selection_required':False})
    source=store.add_source('Synthetic source','Revenue was USD 12 million.')
    run=store.create_run({'title':'Synthetic','objective':'Explain revenue','allow_web':False},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex'})
    store.update_job(job['id'],'running')
    Worker(store,Saver(source['id'])).generate(job,score=False)
    versions=store.rows('SELECT id,hash,detail FROM briefs WHERE run_id=? ORDER BY rowid',(run['id'],))
    assert len(versions)==2 and versions[0]['hash']==versions[1]['hash']
    assert json.loads(versions[-1]['detail'])['citations'][0]['locator']=='line 1 to 1'
