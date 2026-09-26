import json
from briefloop.store import Store, dump
from briefloop.report_browsing import reports, versions, HOT_VERSIONS


def seed(store, count=75, history=3):
    with store.tx() as connection:
        for number in range(count):
            rid=f'run_{number}'
            req={'title':f'Report {number}','objective':'Bounded report','target_words':1,'max_words':100,'private_long_requirement':'x'*5000}
            connection.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,dump(req),'[]',None,'2026-09-26T00:00:00Z','normal'))
            for version in range(history):
                vid=f'brief_{number}_{version}'
                detail={'title':req['title'],'citations':[{'source_id':'not-a-real-source','locator':'x'*5000}]}
                markdown=('Archived needle uniquely 0 0' if number==version==0 else 'Long body ')*200
                connection.execute('INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)',(vid,rid,f'brief_{number}_{version-1}' if version else None,'user' if version else 'agent',markdown,vid,dump(detail),dump({'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':markdown}]}]}),'2026-09-26T00:00:00Z'))
                connection.execute('INSERT INTO assessments VALUES(?,?,?,?)',(f'a_{number}_{version}',vid,dump({'status':'complete','overall':4,'findings':[{'description':'z'*5000}]}),'2026-09-26T00:00:00Z'))


def test_state_bounds_history_and_detail_and_preserves_pins(tmp_path):
    store=Store(tmp_path);seed(store)
    small=store.snapshot();assert len(small['briefs'])<=HOT_VERSIONS
    assert len(small['assessments'])<=HOT_VERSIONS
    assert len(small['runs'])<=HOT_VERSIONS
    assert all('source_ids' not in run and 'all_source_ids' not in run for run in small['runs'])
    assert all(len(brief['detail'])<500 and 'markdown' not in brief and 'editor_document' not in brief for brief in small['briefs'])
    assert all('findings' not in json.loads(a['data']) for a in small['assessments'])
    state=store.snapshot(run_id='run_0',version_id='brief_0_0')
    assert {'brief_0_0','brief_0_2'}<={b['id'] for b in state['briefs']}
    assert state['report_catalog']['total']==75
    full=store.brief_view('brief_0_0')
    assert full['markdown'].startswith('Archived needle') and full['editor_document']
    assert full['context']['latest']['id']=='brief_0_2'
    assert full['context']['run']['source_ids']=='[]'
    assert json.loads(full['context']['assessments'][0]['data'])['findings']


def test_report_and_history_pagination_search_hidden_old_bodies(tmp_path):
    store=Store(tmp_path);seed(store,count=37,history=5)
    first=reports(store,limit=10);second=reports(store,limit=10,cursor=first['next_cursor'])
    assert len(first['items'])==10 and len(second['items'])==10
    assert not {b['run_id'] for b in first['items']}&{b['run_id'] for b in second['items']}
    seen=[];page=reports(store,limit=7)
    while True:
        seen.extend(b['run_id'] for b in page['items'])
        if not page['next_cursor']:break
        page=reports(store,limit=7,cursor=page['next_cursor'])
    assert len(seen)==len(set(seen))==37
    found=reports(store,q='Archived needle uniquely 0 0')
    assert [b['id'] for b in found['items']]==['brief_0_4']
    history=versions(store,'run_0',limit=2)
    assert [b['id'] for b in history['items']]==['brief_0_4','brief_0_3']
    older=versions(store,'run_0',cursor=history['next_cursor'],limit=2)
    assert [b['id'] for b in older['items']]==['brief_0_2','brief_0_1']
    assert [b['id'] for b in versions(store,'run_0',before='brief_0_2')['items']]==['brief_0_1','brief_0_0']
    seen=[];page=versions(store,'run_0',limit=2)
    while True:
        seen.extend(b['id'] for b in page['items'])
        if not page['next_cursor']:break
        page=versions(store,'run_0',limit=2,cursor=page['next_cursor'])
    assert len(seen)==len(set(seen))==5
    store.delete_report('brief_0_4')
    assert reports(store,q='Archived needle')['items']==[]
    assert versions(store,'run_0')['items']==[]


def test_old_live_report_is_pinned_past_recent_history(tmp_path):
    store=Store(tmp_path);seed(store)
    with store.tx() as connection:
        connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',('live','generate','running',dump({'run_id':'run_0'}),None,None,'2020','2020'))
        for n in range(45):connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(f'done_{n}','source_refresh','complete','{}','{}',None,'2026','2026'))
    state=store.snapshot()
    assert any(j['id']=='live' for j in state['jobs'])
    assert any(b['id']=='brief_0_2' for b in state['briefs'])
    assert any(r['id']=='run_0' for r in state['runs'])


def test_hot_run_retains_progress_counts_and_policy_without_full_requirements(tmp_path):
    store=Store(tmp_path);seed(store,count=1)
    source=store.add_source('initial','public sample')
    with store.tx() as connection:
        requirements=json.loads(store.one('runs','run_0')['requirements'])
        requirements.update(allow_web=True,target_minutes=10,hard_timeout_minutes=0)
        connection.execute('UPDATE runs SET source_ids=?,requirements=? WHERE id=?',(dump([source['id']]),dump(requirements),'run_0'))
    run=store.snapshot()['runs'][0]
    assert run['initial_source_count']==run['source_count']==1
    assert 'source_ids' not in run
    req=json.loads(run['requirements'])
    assert req['allow_web']==1 and req['target_minutes']==10 and req['hard_timeout_minutes']==0
    assert 'private_long_requirement' not in req


def test_trial_draft_remains_readable_but_not_in_public_browsing(tmp_path):
    store=Store(tmp_path);seed(store,count=1,history=3)
    with store.tx() as connection:connection.execute("UPDATE runs SET mode='trial' WHERE id='run_0'")
    assert reports(store)['items']==versions(store,'run_0')['items']==[]
    full=store.brief_view('brief_0_1')
    assert full['markdown'] and full['context']['run']['mode']=='trial'
    assert full['context']['latest']['id']=='brief_0_2'
    assert full['context']['original']['id']=='brief_0_0'


def test_source_usage_keeps_archived_report_references_without_run_lists(tmp_path):
    store=Store(tmp_path);seed(store,count=37)
    source=store.add_source('shared source','text')
    for n in range(37):store.attach_source(f'run_{n}',source['id'])
    snapshot=store.snapshot()
    assert snapshot['source_report_counts'][source['id']]==37
    seen=[];page=reports(store,source_id=source['id'],limit=10)
    while True:
        seen.extend(b['run_id'] for b in page['items'])
        if not page['next_cursor']:break
        page=reports(store,source_id=source['id'],limit=10,cursor=page['next_cursor'])
    assert len(seen)==len(set(seen))==37
    assert 'run_0' in seen


def test_http_browsing_opens_old_versions_outside_polled_state(tmp_path):
    import http.client
    import threading
    from briefloop.server import make_server
    server=make_server(tmp_path,port=0,paused=True)
    seed(server.store,count=37,history=3)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(route):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        connection.request('GET','/api/'+route)
        response=connection.getresponse();payload=json.loads(response.read());connection.close()
        assert response.status==200,payload
        return payload
    try:
        snapshot=get('state');assert not any(b['id']=='brief_0_0' for b in snapshot['briefs'])
        page=get('reports?q=Archived%20needle');assert page['items'][0]['id']=='brief_0_2'
        history=get('report-history?run_id=run_0&limit=1');assert history['next_cursor']
        earlier=get('report-history?run_id=run_0&limit=1&cursor='+history['next_cursor'])
        assert earlier['items'][0]['id']=='brief_0_1'
        full=get('brief?id=brief_0_0');assert full['markdown'] and full['context']['assessments']
        details=get('report-context?version_id=brief_0_0');assert details['latest']['id']=='brief_0_2'
        pinned=get('state?run_id=run_0&version_id=brief_0_0')
        assert any(b['id']=='brief_0_0' for b in pinned['briefs'])
    finally:server.shutdown();server.server_close()
