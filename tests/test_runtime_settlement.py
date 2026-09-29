from briefloop.document_model import markdown_document
import json
import threading
import time

import pytest

from briefloop.store import Store,dump,now
from briefloop.runtime import Worker
from briefloop.review import build_packet,enqueue_review


class NoModel:
    def __init__(self):self.cancelled=threading.Event()
    def cancel(self):self.cancelled.set()
    def execute(self,*args,**kwargs):raise AssertionError('Cached result must not call a model')


def report(store):
    store.set_meta('settings',{**store.settings(),'company_context_enabled':False,'auto_learn':False})
    source=store.add_source('Synthetic source','Revenue was USD 12 million.')
    run=store.create_run({'title':'Synthetic','objective':'Explain revenue','writing_mode':'internal_report'},[source['id']])
    brief=store.publish(run['id'],{'title':'Synthetic','markdown':'Revenue was USD 12 million.'})
    return run,source,brief


class WaitingRuntime(NoModel):
    """Hold a real Worker stage after it has admitted its synthetic draft."""
    def __init__(self,write_draft=False):
        super().__init__();self.write_draft=write_draft;self.entered=threading.Event()
    def execute(self,job,prompt,folder,on_tick=lambda:None,**kwargs):
        if self.write_draft:
            schema=json.loads((folder/'reader_contract.schema.json').read_text(encoding='utf-8'))
            spec=json.loads((folder/'input.json').read_text(encoding='utf-8'))['deliverable_spec']
            contract={'source_fingerprint':schema['properties']['source_fingerprint']['const'],
                      'clauses':[{'requirement_id':item['requirement_id'],'kind':'reader_content',
                                  'source_quote':item['text'],'instruction':item['text']} for item in spec['requirement_items']]}
            (folder/'plan.json').write_text(dump({'reader_contract':contract}),encoding='utf-8')
            (folder/'draft.json').write_text(dump({'title':'Synthetic','markdown':'Revenue was USD 12 million.'}),encoding='utf-8')
            on_tick()
        self.entered.set()
        if not self.cancelled.wait(10):raise AssertionError('Test did not stop its waiting runtime')
        raise InterruptedError('Synthetic model turn stopped')


def wait_for(check):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if check():return
        time.sleep(.01)
    assert check()
