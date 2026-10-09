"""Fast web path uses real metering/storage with controlled external transports."""
import json
import pytest
from briefloop import duckduckgo, sources, research_budget, fast_research
from briefloop.store import Store, dump
from briefloop.runtime import Worker
from test_fast_reports import Writer, finish
from test_websearch import ddg_response


class WebWriter(Writer):
    selection=['C1','C2']
    query='public quarterly results'
    def execute(self, job, prompt, folder, *args, **kwargs):
        if folder.name in ('query-plan','selection'):
            self.calls.append(folder.name)
            assert job['allow_web'] is False
            if folder.name=='query-plan':
                assert 'LOCAL_PRIVATE_BODY' not in prompt
                value={'queries':[{'provider':'duckduckgo','query':self.query,'reason':'primary disclosure'}]}
            else:value={'candidate_ids':self.selection,'gaps':['The cause of growth is not disclosed.']}
            (folder/'response.txt').write_text(dump(value))
            return {'status':'complete'}
        if folder.name=='fast-writing':assert 'Snippet 0' not in prompt
        return super().execute(job,prompt,folder,*args,**kwargs)


def setup(tmp_path,monkeypatch,*,invalid_selection=False):
    store=Store(tmp_path)
    req={'title':'Public report','objective':'Explain reported quarterly revenue','completion_mode':'fast_web',
         'fact_check':False,'search_policy':{'primary_provider':'duckduckgo','native_search_enabled':False}}
    run=store.create_run(req,[],research_protocol='quality_v1')
    job=store.enqueue('generate',{'run_id':run['id'],'agent_backend':'codex','runtime':{'model':'synthetic-no-call'}})
    store.update_job(job['id'],'running')
    runtime=WebWriter(store);worker=Worker(store,runtime)
    if invalid_selection:runtime.selection=['https://unselected.invalid']
    searches=[];reads=[]
    html,raw=ddg_response(['https://example.test/disclosure','https://example.test/unavailable'])
    monkeypatch.setattr(duckduckgo,'_post',lambda form:(searches.append(form) or (html,raw)))
    def fetch(url,**kwargs):
        reads.append(url)
        if url.endswith('unavailable'):raise ValueError('Source unavailable')
        return '<html><title>Public disclosure</title><p>收入 120 万元，同比增长 20%</p></html>'.encode(),'text/html','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',fetch)
    return store,run,job,runtime,worker,searches,reads


def test_fast_queries_use_the_selected_provider_constraints(tmp_path,monkeypatch):
    store,run,job,runtime,worker,searches,reads=setup(tmp_path,monkeypatch)
    runtime.query='enterprise AI agent deployment governance official 2026-09-28..2026-10-04'
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    result=fast_research.collect(worker,job,run,folder)
    assert searches[0]['q']==runtime.query  # 73 characters reach DDG unchanged.
    assert result['source_ids'] and len(reads)==2
    assert (folder/'fast-web/research.json').exists()
    assert runtime.calls==['query-plan','selection']
    # The provider boundary still rejects this same query for Zhipu before
    # any search reservation or network request. No credentials are needed.
    from briefloop import zhipu
    with pytest.raises(ValueError,match='70'):
        zhipu.validate_search(runtime.query,dict(topic='general',max_results=5,
            search_depth='basic',time_range=None,start_date=None,end_date=None,
            include_domains=[],exclude_domains=[]))
