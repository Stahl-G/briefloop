"""In-flight managed search reuse, with real SQLite contention and fake providers."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import threading
import time

import pytest

from briefloop import duckduckgo, research_budget as budget, research_plan as plan, tavily, websearch
from briefloop.store import Store


LIMITS={'search_requests':2,'candidate_urls':8,'source_pages':2}


def make_run(tmp_path,*,provider='tavily',limits=None,quality=False):
    store=Store(tmp_path/'workspace')
    store.set_meta('settings',{**store.settings(),'search_provider':provider})
    run=store.create_run({'title':'test','objective':'research','allow_web':True,
                          'research_budget':limits or LIMITS},[],
                         **({'research_protocol':'quality_v1'} if quality else {}))['id']
    store.enqueue('generate',{'run_id':run})
    if quality:plan.freeze(store,run)
    return store,run


def _response(urls):
    value={'results':[{'url':url,'title':'Title','content':'snippet'} for url in urls],
           'request_id':'provider-1'}
    return value,json.dumps(value).encode()


def _ddg_html():
    html='<div class="result"><h2 class="result__title"><a class="result__a" href="https://example.test/a">A</a></h2></div>'
    return html,html.encode()


def _process_search(workspace,run_id,entered,release,waiting,count,outputs,second):
    from briefloop import duckduckgo as child_ddg, research_budget as child_budget, websearch as child_search
    from briefloop.store import Store as ChildStore
    if second:
        original=child_budget.wait_for_search_claim
        def observe(*args,**kwargs):
            waiting.set()
            return original(*args,**kwargs)
        child_budget.wait_for_search_claim=observe
    def provider(_form):
        with count.get_lock():count.value+=1
        entered.set()
        if not release.wait(10):raise RuntimeError('synthetic provider timed out')
        return _ddg_html()
    child_ddg._post=provider
    try:outputs.put(child_search.search('same query',store=ChildStore(workspace),run_id=run_id))
    except Exception as exc:outputs.put({'error':repr(exc)})


