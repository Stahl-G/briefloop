import json
import pytest
from briefloop.store import Store, Conflict, SourceTooLarge
from briefloop import source_library_search as library


def test_literal_context_scope_and_existing_filters(tmp_path):
    store=Store(tmp_path)
    phrase='中文长句'*40+' % _ [] .* + ? & # <script>'
    source=store.add_source('local.txt','首行\r\n'+('前'*20000)+phrase+' 尾\r\n')
    web=store.add_source('web.txt','needle',url='https://example.test/report')
    run=store.create_run({'title':'R','objective':'O','allow_web':False},[web['id']])
    store.attach_source(run['id'],source['id'])
    from briefloop.search_policy import record_origins
    record_origins(store,[web['url']],{'provider':'tavily'})
    result=library.search(store,phrase,run_id=run['id'],source_type='file',channel='unrecorded',status='ready')
    assert result['exhausted'] and result['unsearched_count']==0
    item=result['items'][0]
    assert (item['source_id'],item['source_hash'])==(source['id'],source['hash'])
    assert item['hits'][0]['start_line']==2 and phrase in item['hits'][0]['context']
    assert len(item['hits'][0]['context'])<=384
    assert library.search(store,'NEEDLE',channel='tavily')['items'][0]['source_id']==web['id']
    assert not library.search(store,'needle',source_type='file')['items']
    assert library.search(store,'LOCAL.TXT')['items'][0]['match_fields']==['name']
    assert library._hit('ß'*10000+'KEY😀','key',1)['context'].endswith('KEY😀')
    assert len(library._hit('ffi'*256,'ﬃ'.casefold()*256,1)['context'])<=384
    with pytest.raises(ValueError):library.search(store,'中'*257)
    with pytest.raises(ValueError):library.search(store,'needle',run_id='missing')


def test_explicit_continuation_and_per_page_work_limit(tmp_path,monkeypatch):
    store=Store(tmp_path)
    sources=[store.add_source(f'{i:02d}.txt','nothing' if i<40 else 'needle') for i in range(41)]
    calls=[];original=store.source_text
    monkeypatch.setattr(store,'source_text',lambda sid,**kw:(calls.append(sid),original(sid,**kw))[1])
    first=library.search(store,'needle')
    assert first['scanned_candidates']==40 and not first['exhausted'] and first['items']==[]
    assert len(calls)==40
    second=library.search(store,'needle',cursor=first['next_cursor'])
    assert second['exhausted'] and second['items'][0]['source_id']==sources[-1]['id']
    (store.root/'sources'/(sources[-1]['id']+'.provenance.json')).write_text('{"needs_visual":true}')
    with pytest.raises(Conflict):library.search(store,'needle',cursor=first['next_cursor'])
    first=library.search(store,'needle')
    store.add_source('new.txt','needle')
    with pytest.raises(Conflict):library.search(store,'needle',cursor=first['next_cursor'])


def test_unread_files_and_budget_continue_without_losing_candidate(tmp_path,monkeypatch):
    store=Store(tmp_path)
    monkeypatch.setattr(library,'MAX_SOURCE_BYTES',32)
    monkeypatch.setattr(library,'MAX_PAGE_BYTES',64)
    large=store.add_source('large.txt','x'*100)
    changed=store.add_source('changed.txt','needle')
    (store.root/changed['path']).write_text('changed')
    valid=store.add_source('valid.txt','needle')
    first=library.search(store,'needle')
    assert first['scanned_candidates']==2 and first['unsearched_count']==2
    assert {x['reason'] for x in first['unsearched']}=={'too_large','changed'}
    second=library.search(store,'needle',cursor=first['next_cursor'])
    assert second['exhausted'] and second['unsearched_count']==0
    assert second['items'][0]['source_id']==valid['id']
    with pytest.raises(SourceTooLarge):store.source_text(large['id'],max_bytes=32)
    assert store.source_text(large['id'])=='x'*100
    failed=store.add_source('failed.txt','needle',error='read failed')
    assert library.search(store,'needle',status='failed')['items']==[]
    assert library.search(store,'failed.txt',status='failed')['items'][0]['body_status']=='failed'
    (store.root/'sources'/(valid['id']+'.provenance.json')).write_text(json.dumps({'needs_visual':True}))
    visual=library.search(store,'needle',status='visual')
    assert visual['items'][0]['source_id']==valid['id'] and visual['unsearched'][0]['reason']=='visual_partial'
    (store.root/'sources'/(valid['id']+'.provenance.json')).write_text('{broken json')
    broken=library.search(store,'needle',status='visual')
    assert broken['exhausted'] and broken['items']==[] and broken['unsearched_count']==1
    assert broken['unsearched'][0]['source_id']==valid['id']
    assert broken['unsearched'][0]['reason']=='metadata_unreadable'


def test_source_search_http_and_source_read_still_verify_hash(tmp_path):
    import http.client
    import threading
    from urllib.parse import quote
    from briefloop.server import make_server, _close_service
    server=make_server(tmp_path/'workspace',port=0,paused=True)
    server.worker.start()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        source=server.store.add_source('plain.txt','首行\n正文独有 <script> %_[]')
        def get(path):
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET',path);response=client.getresponse()
            answer=response.status,json.loads(response.read());client.close();return answer
        status,result=get('/api/source-search?q='+quote('正文独有 <script> %_[]'))
        assert status==200 and result['items'][0]['hits'][0]['start_line']==2
        assert get('/api/source?id='+source['id'])[0]==200
        (server.store.root/source['path']).write_text('changed')
        assert get('/api/source?id='+source['id'])[0]==409
        status,result=get('/api/source-search?q='+quote('正文独有'))
        assert status==200 and result['exhausted'] and result['unsearched_count']==1
        assert result['unsearched'][0]['reason']=='changed' and not result['items']
    finally:
        server.shutdown();thread.join();assert _close_service(server)==[]
