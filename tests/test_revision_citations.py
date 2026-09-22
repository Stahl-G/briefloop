"""Shipping chat revisions keep citation metadata in immutable report versions."""
from io import BytesIO
import json
import threading
from types import SimpleNamespace

from docx import Document
import pytest

from briefloop.chat_tools import workspace_action
from briefloop.export_jobs import enqueue_export, generate_word, output_path
from briefloop.native_orchestrator import action
from briefloop.native_roles import ToolError
from briefloop.review import build_packet, accept_review, review_status, validate_applicable_review
from briefloop.store import Store, Conflict, dump
from review_checks import for_version


def case(tmp_path):
    store=Store(tmp_path)
    source=store.add_source('Synthetic worksheet', 'Revenue 12.\nPeriod H1.')
    style=store.add_source('Style reference', 'Example layout only.')
    run=store.create_run({'title':'Report','objective':'Explain revenue','reference_source_ids':[style['id']]},[source['id']])
    document={'type':'doc','content':[{'type':'paragraph','content':[
        {'type':'text','text':'Revenue 12 in H1. '},{'type':'citation','attrs':{'sourceId':source['id']}}]}]}
    base=store.publish(run['id'],{'title':'Report','editor_document':document,
        'citations':[{'source_id':source['id']}], 'gaps':['Synthetic uncertainty remains']})
    config={'native_role':'chat','permission':'workspace-write','run_id':run['id'],
            'session_id':'fixture','_harness':SimpleNamespace(cancel_requested=lambda _:False)}
    citations=[{'source_id':source['id'],'locator':'Sheet1!A5','excerpt':'Revenue 12.'},
               {'source_id':source['id'],'locator':'Sheet1!B6','excerpt':'Period H1.'}]
    return store,source,style,base,config,citations


def revise_native(store,config,base,document,citations):
    result=action(store,config,{'request':{'action':'revise_document','base_version':base,
                                         'editor_document':document,'citations':citations}})
    return json.loads(result['content'][0]['text'])


def test_citation_only_native_revision_preserves_old_word_and_review_binding(tmp_path):
    store,source,style,base,config,citations=case(tmp_path)
    old_job=enqueue_export(store,base['id'])
    old_result=generate_word(store,old_job,threading.Event())
    store.update_job(old_job['id'],'complete',result=old_result)
    old_word=output_path(store,old_job).read_bytes()
    folder=store.root/'old-review'
    old_fingerprint,files=build_packet(store,base['id'],folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('review_old',base['id'],None,old_fingerprint,'running',dump({'packet_path':'old-review/packet','files':files}),None,'2026','2026'))
    accept_review(store,'review_old',{'fingerprint':old_fingerprint,'version_id':base['id'],
        'status':'complete','summary':'Synthetic review','coverage_scan_complete':True,
        'requirement_checks':for_version(store,base['id']),'findings':[],
        'assessment':{'brief_hash':base['hash'],'summary':'Synthetic review','overall':'达到要求',
                      'evidence':3,'coverage':3,'analysis':3,'expression':3}})

    document=json.loads(base['editor_document'])
    saved=revise_native(store,config,base['id'],document,citations)
    assert saved['id']!=base['id'] and saved['parent_id']==base['id'] and saved['author']=='agent'
    assert saved['hash']==base['hash'] and saved['editor_document']==base['editor_document']
    assert json.loads(saved['detail'])['citations']==citations
    assert json.loads(saved['detail'])['gaps']==json.loads(base['detail'])['gaps']
    assert Store(tmp_path).one('briefs',saved['id'])==saved
    assert store.one('briefs',base['id'])==base and not store.rows('SELECT * FROM feedback')
    assert revise_native(store,config,saved['id'],document,citations)['id']==saved['id']

    new_job=enqueue_export(store,saved['id'])
    assert new_job['id']!=old_job['id']
    assert json.loads(new_job['payload'])['fingerprint']!=json.loads(old_job['payload'])['fingerprint']
    generate_word(store,new_job,threading.Event())
    new_word=output_path(store,new_job).read_bytes()
    def reader_text(blob):
        return '\n'.join(''.join(p._p.xpath('.//w:t/text()')) for p in Document(BytesIO(blob)).paragraphs)
    assert all(item['locator'] in reader_text(new_word) for item in citations)
    assert all(item['locator'] not in reader_text(old_word) for item in citations)
    assert output_path(store,old_job).read_bytes()==old_word
    assert enqueue_export(store,base['id'])['id']==old_job['id']

    new_fingerprint,_=build_packet(store,saved['id'],store.root/'new-review')
    assert new_fingerprint!=old_fingerprint
    with pytest.raises(ValueError,match='未绑定'):
        validate_applicable_review(store,'review_old',saved['id'])
    validate_applicable_review(store,'review_old',base['id'])
    assert review_status(store,saved['id'])['reviews']==[]
    assert not store.rows('SELECT id FROM assessments WHERE version_id=?',(saved['id'],))
    assert len(store.rows('SELECT id FROM assessments WHERE version_id=?',(base['id'],)))==1


def test_file_action_updates_document_and_citations_atomically_and_omission_preserves_them(tmp_path):
    store,source,style,base,config,citations=case(tmp_path)
    document=json.loads(base['editor_document'])
    document['content'][0]['content'][0]['text']='收入12，期间H1。'
    path=store.root/'revision.json'
    path.write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
    request={'action':'revise_document','base_version':base['id'],'document_file':str(path),'citations':citations}
    saved=workspace_action(store,request)
    assert '收入12' in saved['markdown'] and json.loads(saved['detail'])['citations']==citations
    assert saved['hash']!=base['hash'] and saved['parent_id']==base['id']
    assert workspace_action(store,{**request,'base_version':saved['id']})['id']==saved['id']
    document['content'][0]['content'][0]['text']='收入12，期间H1；需继续核对。'
    path.write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
    omitted=workspace_action(store,{'action':'revise_document','base_version':saved['id'],'document_file':str(path)})
    assert json.loads(omitted['detail'])['citations']==citations and omitted['author']=='agent'
    assert not store.rows('SELECT * FROM feedback')
    with pytest.raises(Conflict,match='已有更新'):workspace_action(store,request)
    assert len(store.rows('SELECT id FROM briefs'))==3
    projection=store.revise(omitted['id'],omitted['markdown'],citations=citations[:1])
    assert projection['parent_id']==omitted['id'] and projection['hash']==omitted['hash']
    assert projection['editor_document']==omitted['editor_document']
    schema=workspace_action(store,{'action':'capabilities'})['schemas']['revise_document.citations']
    assert schema['type']=='array' and set(schema['items']['properties'])=={'source_id','locator','excerpt'}
    assert '不代表已通过' in schema['description']


def test_unchanged_automatic_citations_still_follow_their_body_nodes(tmp_path):
    store,source,style,base,config,citations=case(tmp_path)
    extra=store.add_source('Other selected source','Additional synthetic context.')
    store.attach_source(base['run_id'],extra['id'])
    document=json.loads(base['editor_document'])
    document['content'][0]['content'].append({'type':'citation','attrs':{'sourceId':extra['id']}})
    with_extra=store.revise(base['id'],editor_document=document)
    references=json.loads(with_extra['detail'])['citations']
    references[0]=citations[0]
    updated=revise_native(store,config,with_extra['id'],document,references)
    document['content'][0]['content'].pop()
    removed=store.revise(updated['id'],editor_document=document)
    assert json.loads(removed['detail'])['citations']==[citations[0]]
    assert json.loads(updated['detail'])['content_citations']==[references[1]]


def test_citation_revision_rejects_bad_scope_types_or_source_hash_without_partial_save(tmp_path):
    store,source,style,base,config,citations=case(tmp_path)
    outsider=store.add_source('Other task source','Not selected.')
    document=json.loads(base['editor_document'])
    document['content'][0]['content'][0]['text']='Unsaved revision.'
    before_sources=store.source_ids(base['run_id'])
    for bad,match in [
        ([citations[0],{'source_id':outsider['id'],'locator':'line 1'}],'未登记'),
        ([citations[0],{'source_id':style['id'],'locator':'line 1'}],'风格参考'),
        ([citations[0],{'source_id':source['id'],'locator':{'cell':'B6'}}],'locator'),
    ]:
        with pytest.raises(ValueError,match=match):revise_native(store,config,base['id'],document,bad)
        assert store.rows('SELECT id FROM briefs')==[{'id':base['id']}]
        assert store.one('briefs',base['id'])==base and store.source_ids(base['run_id'])==before_sources
    (store.root/source['path']).write_bytes(b'Changed outside the application.')
    with pytest.raises(Conflict,match='Source changed'):
        revise_native(store,config,base['id'],document,citations)
    assert store.rows('SELECT id FROM briefs')==[{'id':base['id']}]
    assert not store.rows('SELECT * FROM feedback')


def test_native_citation_revision_keeps_read_only_and_report_scope(tmp_path):
    store,source,style,base,config,citations=case(tmp_path)
    document=json.loads(base['editor_document'])
    with pytest.raises(ToolError,match='只读'):
        revise_native(store,{**config,'permission':'read-only'},base['id'],document,citations)
    other_run=store.create_run({'title':'Other','objective':'Separate report'},[source['id']])
    other=store.publish(other_run['id'],{'title':'Other','editor_document':document})
    with pytest.raises(ToolError,match='不属于本轮报告'):
        revise_native(store,config,other['id'],document,citations)
    assert len(store.rows('SELECT id FROM briefs'))==2
    assert store.one('briefs',base['id'])==base and store.one('briefs',other['id'])==other
