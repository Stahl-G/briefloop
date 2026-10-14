from copy import deepcopy
from io import BytesIO
import json
import pytest
from openpyxl import Workbook
from briefloop.store import Store
from briefloop.evidence import create_span, create_claim, bind_claim, inspect_bindings
from briefloop.sources import upload


def test_fact_check_candidates_reject_caller_marked_public_upload(tmp_path):
    from briefloop.evidence import register_fact_check_candidates
    store = Store(tmp_path)
    source = store.add_source('Private input', 'Internal revenue is 12.', url='https://www.python.org/')
    run = store.create_run({'title': 'Report', 'objective': 'Revenue changes'}, [source['id']])
    quote = 'Internal revenue is 12.'
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': quote})
    result = register_fact_check_candidates(store, brief['id'], [{'report_quote': quote, 'kind': 'fact',
        'source_id': source['id'], 'excerpt': quote, 'public': True}])
    assert not result['registered'] and result['reason']
    assert not store.rows('SELECT id FROM claims WHERE run_id=?', (run['id'],))
    assert store.one('briefs', brief['id'])['markdown'] == quote


def test_fact_check_candidate_registration_survives_empty_failed_public_read(tmp_path, monkeypatch):
    from briefloop import sources, tavily
    from briefloop.evidence import register_fact_check_candidates
    # Offline protocol fixture; the separate acceptance retains real Python HTTP originals.
    monkeypatch.setattr(sources, '_fetch_bytes', lambda *a, **k: (b'Public release date: 2024-10-07.', 'text/plain', 'utf-8'))
    addresses = []
    def public_addresses(host, port):
        if host == '127.0.0.1':
            raise ValueError('private address')
        addresses.append((host, port))
        return ['1.1.1.1']
    monkeypatch.setattr(sources, '_checked_addresses', public_addresses)
    store = Store(tmp_path)
    source = sources.fetch(store, 'https://www.python.org/release/')
    failed = store.add_source('Unavailable public page', '', url='https://www.python.org/unavailable/', error='HTTP 404')
    run = store.create_run({'title': 'Report', 'objective': 'Release changes', 'allow_web': True,
        'search_policy': {'primary_provider': 'tavily', 'coverage_mode': 'primary_only'}}, [source['id'], failed['id']])
    quote = 'The release date was 2024-10-07.'
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': quote})
    item = {'report_quote': quote, 'kind': 'fact', 'source_id': source['id'], 'excerpt': 'Public release date: 2024-10-07.'}
    result = register_fact_check_candidates(store, brief['id'], [item, {**item, 'source_id': failed['id']}])
    assert len(result['registered']) == 1 and len(result['rejected']) == 1
    assert len(addresses) == 1  # Same-source candidates do not repeat network eligibility checks.
    assert inspect_bindings(store, brief['id'])['bindings'][0]['quote'] == quote
    assert register_fact_check_candidates(store, brief['id'], [item])['registered'] == result['registered']
    assert len(store.rows('SELECT id FROM claims WHERE run_id=?', (run['id'],))) == 1
    assert store.one('briefs', brief['id'])['markdown'] == quote
    response = {'request_id': 'public-extract', 'results': [{'url': 'https://www.python.org/provider-release/',
        'raw_content': 'Public release date: 2024-10-07.'}]}
    monkeypatch.setattr(tavily, '_post', lambda *a, **k: (response, json.dumps(response).encode()))
    extracted = tavily.extract(store, [response['results'][0]['url'], 'https://www.python.org/provider-unavailable/'], run_id=run['id'])
    public = extracted['sources'][0]
    unavailable = extracted['sources'][1]
    assert unavailable['status'] == 'failed' and store.source_text(unavailable['id']) == ''
    assert unavailable['provenance']['original_kind'] == 'provider_response'
    result = register_fact_check_candidates(store, brief['id'], [{**item, 'source_id': public['id']}])
    assert len(result['registered']) == 1 and not result['rejected']
    with store.tx() as connection:
        connection.execute('UPDATE sources SET url=? WHERE id=?', ('http://127.0.0.1/private/', unavailable['id']))
    assert not register_fact_check_candidates(store, brief['id'], [item])['registered']
    with store.tx() as connection:
        connection.execute('UPDATE sources SET url=? WHERE id=?', (unavailable['url'], unavailable['id']))
    from briefloop.store import content_hash
    (store.root / unavailable['path']).write_text('Private failure detail.')
    with store.tx() as connection:
        connection.execute('UPDATE sources SET hash=? WHERE id=?', (content_hash('Private failure detail.'), unavailable['id']))
    assert not register_fact_check_candidates(store, brief['id'], [item])['registered']
    (store.root / unavailable['path']).write_text('')
    with store.tx() as connection:
        connection.execute('UPDATE sources SET hash=? WHERE id=?', (content_hash(''), unavailable['id']))
    from pathlib import Path
    receipt_path = Path(extracted['request_record_path'])
    saved = receipt_path.read_text()
    receipt = json.loads(saved)
    receipt['admitted_urls'] = []
    receipt_path.write_text(json.dumps(receipt))
    assert not register_fact_check_candidates(store, brief['id'], [item])['registered']
    receipt_path.write_text(saved)
    # A private replacement with a self-consistent source hash is not the provider's captured text.
    (store.root / public['path']).write_text('Private revenue 12.')
    with store.tx() as connection:
        connection.execute('UPDATE sources SET hash=? WHERE id=?', (content_hash('Private revenue 12.'), public['id']))
    assert not register_fact_check_candidates(store, brief['id'], [item])['registered']
    (store.root / public['path']).write_text(response['results'][0]['raw_content'])
    with store.tx() as connection:
        connection.execute('UPDATE sources SET hash=? WHERE id=?', (content_hash(response['results'][0]['raw_content']), public['id']))
    private = store.add_source('Private input', 'Internal revenue 12.')
    store.attach_source(run['id'], private['id'])
    assert not register_fact_check_candidates(store, brief['id'], [item])['registered']


def case(tmp_path):
    store=Store(tmp_path);source=store.add_source('Company update','Revenue 12 million USD in H1.\nOther revenue 120 million USD.')
    run=store.create_run({'title':'Report','objective':'Revenue changes'},[source['id']])
    brief=store.publish(run['id'],{'title':'Report','editor_document':{'type':'doc','content':[
        {'type':'paragraph','content':[{'type':'text','text':'Revenue was 12 million USD in H1.'}]}]}})
    return store,source,run,brief


def test_exact_location_version_binding_and_format_change(tmp_path):
    store,source,run,brief=case(tmp_path)
    request={'source_id':source['id'],'locator':{'kind':'text','start_line':1,'end_line':1},'excerpt':'Revenue 12 million USD in H1.'}
    span=create_span(store,request)
    with pytest.raises(ValueError,match='指定位置'):create_span(store,{**request,'excerpt':'120 million USD'})
    claim=create_claim(store,run['id'],{'statement':'Revenue was 12 million USD in H1.','kind':'fact','entity':'Company','metric':'revenue','period':'H1',
        'supports':[{'span_id':span['id'],'supports_quote':'12 million USD in H1.'}]})
    doc=json.loads(brief['editor_document']);bid=doc['content'][0]['attrs']['blockId']
    bind_claim(store,brief['id'],claim['id'],bid,'12 million USD')
    assert inspect_bindings(store,brief['id'])['bindings'][0]['status']=='unreviewed'
    colored=deepcopy(doc);colored['content'][0]['content'][0]['marks']=[{'type':'textStyle','attrs':{'color':'#ff0000'}}]
    formatted=store.revise(brief['id'],editor_document=colored)
    assert inspect_bindings(store,formatted['id'])['bindings'][0]['status']=='unreviewed'
    changed=deepcopy(colored);changed['content'][0]['content'][0]['text']='Revenue was 120 million USD in H1.'
    wrong=store.revise(formatted['id'],editor_document=changed)
    assert inspect_bindings(store,wrong['id'])['bindings'][0]['status']=='needs_review'
    assert inspect_bindings(store,brief['id'])['bindings'][0]['status']=='unreviewed'
    (store.root/source['path']).write_text('Revenue changed outside application')
    assert inspect_bindings(store,brief['id'])['bindings'][0]['status']=='source_changed'


def test_second_support_quote_error_points_to_statement_without_saving_claim(tmp_path):
    from briefloop.chat_tools import workspace_action
    store,source,run,brief=case(tmp_path)
    spans=[create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':line,'end_line':line}})
           for line in (1,2)]
    claim={'statement':'Revenue was 12 million USD; other revenue was 120 million USD.','kind':'fact',
        'supports':[{'span_id':spans[0]['id'],'supports_quote':'12 million USD'},
                    {'span_id':spans[1]['id'],'supports_quote':spans[1]['data']['excerpt']}]}
    original=deepcopy(claim)
    with pytest.raises(ValueError) as error:
        workspace_action(store,{'action':'claim_create','run_id':run['id'],'claim':claim})
    assert 'supports[1].supports_quote' in str(error.value)
    assert 'statement' in str(error.value) and '连续逐字' in str(error.value) and 'excerpt' in str(error.value)
    assert not store.rows('SELECT id FROM claims') and claim==original
    claim['supports'][1]['supports_quote']='other revenue was 120 million USD'
    accepted=workspace_action(store,{'action':'claim_create','run_id':run['id'],'claim':claim})
    assert len(store.rows('SELECT id FROM claims'))==1 and accepted['data']['review_status']=='unreviewed'
    assert [s['supports_quote'] for s in accepted['data']['supports']]==[s['supports_quote'] for s in claim['supports']]


def test_workbook_cell_and_inference_are_traceable_not_automatically_verified(tmp_path):
    store,source,run,brief=case(tmp_path)
    book=Workbook();sheet=book.active;sheet.title='Operating';sheet['A1']='Revenue';sheet['B1']=12;sheet['B2']='=B1*10'
    stream=BytesIO();book.save(stream);uploaded=upload(store,'model.xlsx',stream.getvalue());store.attach_source(run['id'],uploaded['id'])
    evidence=create_span(store,{'source_id':uploaded['id'],'locator':{'kind':'xlsx','sheet':'Operating','cells':'A1:B2'}})
    assert evidence['data']['location_status']=='missing_formula_cache'
    assert evidence['data']['cells'][-1]['formula']=='=B1*10'
    assert evidence['data']['cells'][-1]['value'] is None
    claim=create_claim(store,run['id'],{'statement':'Growth may increase working capital needs.','kind':'inference',
        'supports':[{'span_id':evidence['id'],'supports_quote':'working capital needs.'}],
        'reasoning':'Higher activity may require more inventory.','assumptions':['Payment terms remain unchanged.']})
    assert claim['data']['review_status']=='unreviewed'
    with pytest.raises(ValueError,match='推理说明'):
        create_claim(store,run['id'],{'statement':'Growth is certain.','kind':'inference'})


def test_agent_revisions_do_not_become_user_learning_feedback(tmp_path):
    store,source,run,brief=case(tmp_path)
    doc=json.loads(brief['editor_document']);doc['content'][0]['content'][0]['text']='Agent revision'
    from briefloop.chat_tools import workspace_action
    path=store.root/'agent-revision.json';path.write_text(json.dumps(doc))
    result=workspace_action(store,{'action':'revise_document','base_version':brief['id'],'document_file':str(path)})
    assert result['author']=='agent'
    assert not store.rows('SELECT id FROM feedback')


def test_link_identity_and_broken_premise_propagate(tmp_path):
    store,source,run,brief=case(tmp_path)
    span=create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':1,'end_line':1}})
    fact=create_claim(store,run['id'],{'statement':'Revenue 12 million USD.','kind':'fact','supports':[{'span_id':span['id'],'supports_quote':'12 million USD'}]})
    inference=create_claim(store,run['id'],{'statement':'Working capital may increase.','kind':'inference','premise_claim_ids':[fact['id']],'reasoning':'Higher activity may increase inventory.'})
    doc=json.loads(brief['editor_document']);doc['content'][0]['content'][0]['marks']=[{'type':'link','attrs':{'href':'#source-'+source['id']}}]
    v=store.revise(brief['id'],editor_document=doc);bid=doc['content'][0]['attrs']['blockId'];bind_claim(store,v['id'],fact['id'],bid,'12 million USD')
    another=store.add_source('Another','Other period');store.attach_source(run['id'],another['id'])
    doc['content'][0]['content'][0]['marks'][0]['attrs']['href']='#source-'+another['id']
    changed=store.revise(v['id'],editor_document=doc)
    assert inspect_bindings(store,changed['id'])['bindings'][0]['status']=='needs_review'
    doc['content'][0]['content'][0]['text']='Working capital may increase.'
    updated=store.revise(changed['id'],editor_document=doc);bind_claim(store,updated['id'],inference['id'],bid,'Working capital may increase.')
    (store.root/source['path']).write_text('Altered source')
    row=next(x for x in inspect_bindings(store,updated['id'])['bindings'] if x['claim_id']==inference['id'])
    assert row['status']=='premise_changed' and row['evidence']==[]
    assert row['premises'][0]['evidence'][0]['intact'] is False


def test_claim_replacement_only_supersedes_same_block(tmp_path):
    store,source,run,brief=case(tmp_path)
    doc=json.loads(brief['editor_document']);doc['content'].append({'type':'paragraph','attrs':{'blockId':'other-block'},'content':[{'type':'text','text':'Revenue was 12 million USD in H1.'}]})
    base=store.revise(brief['id'],editor_document=doc)
    fact=create_claim(store,run['id'],{'statement':'Revenue was 12 million USD in H1.','kind':'fact'})
    first=doc['content'][0]['attrs']['blockId']
    for block in [first,'other-block']:bind_claim(store,base['id'],fact['id'],block,'12 million USD')
    revised=create_claim(store,run['id'],{'statement':'Revenue was 12 million USD in H1.','kind':'fact'},previous_id=fact['id'])
    bind_claim(store,base['id'],revised['id'],first,'12 million USD')
    current=inspect_bindings(store,base['id'])['bindings']
    assert {(x['claim_id'],x['block_id']) for x in current}=={(revised['id'],first),(fact['id'],'other-block')}


def _span(store,source):
    return create_span(store,{'source_id':source['id'],'locator':{'kind':'text','start_line':1,'end_line':1}})


def test_source_statement_needs_span_and_cannot_be_adopted_implicitly(tmp_path):
    store,source,run,brief=case(tmp_path)
    span=_span(store,source)
    source_claim=create_claim(store,run['id'],{'statement':'Revenue 12 million USD in H1.','kind':'fact',
        'claim_role':'source_statement','attribution':'Company disclosure',
        'supports':[{'span_id':span['id'],'supports_quote':'12 million USD'}]})
    assert source_claim['data']['claim_role']=='source_statement'
    assert source_claim['data']['attribution']=='Company disclosure'
    with pytest.raises(ValueError,match='来源陈述必须引用'):
        create_claim(store,run['id'],{'statement':'Something unbacked.','kind':'fact','claim_role':'source_statement'})
    with pytest.raises(ValueError,match='不能作为论证前提'):
        create_claim(store,run['id'],{'statement':'Growth may increase working capital.','kind':'inference',
            'premise_claim_ids':[source_claim['id']],'reasoning':'Higher activity may require inventory.'})
    doc=json.loads(brief['editor_document']);bid=doc['content'][0]['attrs']['blockId']
    with pytest.raises(ValueError,match='不能直接绑定正文'):
        bind_claim(store,brief['id'],source_claim['id'],bid,'12 million USD')
    report=create_claim(store,run['id'],{'statement':'Revenue was 12 million USD in H1.','kind':'fact',
        'supports':[{'span_id':span['id'],'supports_quote':'12 million USD'}]})
    assert report['data']['claim_role']=='report_statement'
    bind_claim(store,brief['id'],report['id'],bid,'12 million USD')


def test_old_claim_without_role_keeps_old_binding_semantics(tmp_path):
    store,source,run,brief=case(tmp_path)
    span=_span(store,source)
    data={'statement':'Revenue was 12 million USD in H1.','kind':'fact','importance':'core','entity':'Company','metric':'revenue',
          'period':'H1','scope':'','requirement_ids':[],'supports':[{'span_id':span['id'],'supports_quote':'12 million USD','rationale':''}],
          'premise_claim_ids':[],'reasoning':'','assumptions':[],'figure_ids':[],'figures':[],'review_status':'unreviewed'}
    from briefloop.store import dump
    with store.tx() as c:c.execute('INSERT INTO claims VALUES(?,?,?,?,?)',('claim_legacy_1',run['id'],None,dump(data),'2026-01-01T00:00:00Z'))
    doc=json.loads(brief['editor_document']);bid=doc['content'][0]['attrs']['blockId']
    bind_claim(store,brief['id'],'claim_legacy_1',bid,'12 million USD')
    assert inspect_bindings(store,brief['id'])['bindings'][-1]['status']=='unreviewed'


def test_old_saved_draft_republishes_after_new_field(tmp_path):
    store,source,run,brief=case(tmp_path)
    old=json.loads(store.one('briefs',brief['id'])['detail']);old.pop('reconciliation_id',None)
    with store.tx() as c:c.execute('UPDATE briefs SET detail=? WHERE id=?',(json.dumps(old),brief['id']))
    again=store.publish(run['id'],{'title':'Report','editor_document':json.loads(brief['editor_document'])},version_id=brief['id'])
    assert again['id']==brief['id']
