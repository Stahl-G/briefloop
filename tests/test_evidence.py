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
