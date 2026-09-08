import hashlib
from io import BytesIO
import json
import os
import urllib.error
import pytest
from briefloop import tavily
from briefloop.store import Store


def test_key_storage_search_and_sanitized_errors(tmp_path,monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    keyfile=tmp_path/'config'/'tavily.key'
    assert tavily.key_status(key_file=keyfile)=={'configured':False,'source':None}
    assert tavily.save_key('tvly-test-secret',key_file=keyfile)=={'configured':True,'source':'file'}
    assert keyfile.stat().st_mode & 0o777==0o600
    requests=[]
    def respond(request,timeout):
        requests.append((request,json.loads(request.data)))
        return BytesIO(json.dumps({'results':[{'title':'Source','url':'https://example.test','content':'Snippet'}],'usage':{'credits':1}}).encode())
    monkeypatch.setattr(tavily.urllib.request,'urlopen',respond)
    result=tavily.search('solar',topic='news',start_date='2026-06-01',end_date='2026-06-30',include_domains=['example.test'],max_results=3,key_file=keyfile)
    request,payload=requests[0]
    assert request.full_url=='https://api.tavily.com/search'
    assert request.get_header('Authorization')=='Bearer tvly-test-secret'
    assert payload['include_answer'] is False and payload['auto_parameters'] is False
    assert payload['search_depth']=='basic' and payload['include_raw_content'] is False
    assert payload['include_domains']==['example.test']
    assert result['results'][0]['kind']=='search_snippet'
    assert 'tvly-test-secret' not in str(result)
    def failure(*args,**kwargs):raise urllib.error.HTTPError('https://api.tavily.com/search',401,'echo tvly-test-secret',{},BytesIO(b'secret'))
    monkeypatch.setattr(tavily.urllib.request,'urlopen',failure)
    with pytest.raises(tavily.TavilyError) as error:tavily.search('test',key_file=keyfile)
    assert '401' in str(error.value) and 'tvly-test-secret' not in str(error.value)
    monkeypatch.setenv('TAVILY_API_KEY','tvly-environment')
    assert tavily.key_status(key_file=keyfile)['source']=='environment'
    assert tavily.delete_key(key_file=keyfile)=={'configured':True,'source':'environment'}
    assert not keyfile.exists()


def test_extract_is_full_provider_text_and_failed_sources_remain_visible(tmp_path,monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    keyfile=tmp_path/'key';tavily.save_key('tvly-test-secret',key_file=keyfile)
    store=Store(tmp_path/'workspace');requests=[]
    payload={'results':[{'url':'https://example.test/good','raw_content':'# Source\nAmount 123; capacity 45 MW.\n'+('Full body. '*1000)}],'failed_results':[{'url':'https://example.test/bad','error':'unreadable'}],'request_id':'test-id'}
    raw=json.dumps(payload).encode()
    def respond(request,timeout):requests.append(json.loads(request.data));return BytesIO(raw)
    monkeypatch.setattr(tavily.urllib.request,'urlopen',respond)
    result=tavily.extract(store,['https://example.test/good','https://example.test/bad'],key_file=keyfile)
    assert 'query' not in requests[0] and 'chunks_per_source' not in requests[0]
    good,bad=result['sources']
    assert store.source_text(good['id'])==payload['results'][0]['raw_content']
    assert good['provenance']['extractor']=='tavily.extract'
    assert good['provenance']['original_kind']=='provider_response'
    assert good['provenance']['raw_sha256']==hashlib.sha256(raw).hexdigest()
    assert (store.root/good['provenance']['original_path']).read_bytes()==raw
    assert bad['status']=='failed' and store.source_text(bad['id'])==''
    assert 'tvly-test-secret' not in str(result)
