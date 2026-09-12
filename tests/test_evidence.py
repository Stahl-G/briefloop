from copy import deepcopy
from io import BytesIO
import json
import pytest
from openpyxl import Workbook
from briefloop.store import Store
from briefloop.evidence import create_span, create_claim, bind_claim, inspect_bindings
from briefloop.sources import upload


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


def test_scout_and_draft_fields_survive_artifact_check(tmp_path):
    from briefloop.models import ScoutResult, BriefDraft, check_artifact
    scout={'sources':[{'source_id':'s1','locator':'','excerpt':'','facts':[],'conflicts':[],'coverage_status':'ok','claim_ids':['claim_1']}],
           'gaps':['missing'],'search_summary':'7 sources','retrieval_notes':[{'query':'q','outcome':'kept'}]}
    report=check_artifact(scout,ScoutResult)
    assert report['status']=='ok' and report['unknown_fields']==[]
    draft=check_artifact({'title':'T','markdown':'Body','reconciliation_id':'recon_1'},BriefDraft)
    assert draft['status']=='ok' and draft['unknown_fields']==[]


def test_old_saved_draft_republishes_after_new_field(tmp_path):
    store,source,run,brief=case(tmp_path)
    old=json.loads(store.one('briefs',brief['id'])['detail']);old.pop('reconciliation_id',None)
    with store.tx() as c:c.execute('UPDATE briefs SET detail=? WHERE id=?',(json.dumps(old),brief['id']))
    again=store.publish(run['id'],{'title':'Report','editor_document':json.loads(brief['editor_document'])},version_id=brief['id'])
    assert again['id']==brief['id']
