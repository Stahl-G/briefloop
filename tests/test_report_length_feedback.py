"""Exact length feedback is visible to agents without becoming a save gate."""
import json
from types import SimpleNamespace

import pytest

from briefloop.chat_tools import workspace_action
from briefloop.deliverable_spec import reader_contract_schema, resolve
from briefloop.native_orchestrator import action
from briefloop import review
from briefloop.store import Store, dump


def case(tmp_path):
    store=Store(tmp_path)
    source=store.add_source('Synthetic source','来源摘录不属于报告正文。'*100)
    run=store.create_run({'title':'报告','objective':'写一份400–700字报告。',
                          'target_words':550,'max_words':800},[source['id']])
    spec=resolve(json.loads(run['requirements']))
    contract={'source_fingerprint':reader_contract_schema(spec)['properties']['source_fingerprint']['const'],
              'clauses':[{'requirement_id':item['requirement_id'],'kind':'reader_content',
                          'source_quote':item['text'],'instruction':item['text']} for item in spec['requirement_items']]}
    document={'type':'doc','content':[
        {'type':'heading','attrs':{'level':1},'content':[{'type':'text','text':'报告'}]},
        {'type':'paragraph','content':[{'type':'text','text':'甲'*740},
                                      {'type':'citation','attrs':{'sourceId':source['id']}}]},
        {'type':'paragraph','content':[{'type':'text','text':'Alpha42'}]}]}
    brief=store.publish(run['id'],{'title':'报告','editor_document':document,'reader_contract':contract,
        'citations':[{'source_id':source['id'],'locator':'line 1','excerpt':store.source_text(source['id'])}]})
    return store,run,brief


@pytest.mark.parametrize('entry',['file','native'])
def test_read_and_revision_receipts_expose_count_without_overriding_feedback(tmp_path,entry):
    store,run,base=case(tmp_path)
    view=workspace_action(store,{'action':'read_report','version_id':base['id']})
    stats=view['length_stats']
    assert stats['count']==743 and stats['max_words']==800 and stats['over_limit'] is False
    assert 'max_words' in stats['limit_scope'] and '反馈' in stats['limit_scope']
    assert '400–700' in view['detail']['reader_contract']['clauses'][0]['instruction']
    assert workspace_action(store,{'action':'read_run_report','run_id':run['id']})['length_stats']==stats

    document=view['editor_document']
    document['content'][1]['content'][0]['text']+='乙'*101
    request={'action':'revise_document','base_version':base['id']}
    if entry=='file':
        path=store.root/'revision.json';path.write_text(dump(document),encoding='utf-8')
        saved=workspace_action(store,{**request,'document_file':str(path)})
    else:
        config={'native_role':'chat','permission':'workspace-write','run_id':run['id'],
                'session_id':'fixture','_harness':SimpleNamespace(cancel_requested=lambda _:False)}
        result=action(store,config,{'request':{**request,'editor_document':document}})
        saved=json.loads(result['content'][0]['text'])
    assert saved['parent_id']==base['id'] and saved['author']=='agent'
    assert saved['length_stats']['count']==844
    assert saved['length_stats']['over_limit'] is True and saved['length_stats']['over_by']==44
    assert saved['length_stats']==store.brief_view(saved['id'])['length_stats']
    assert saved['markdown']==store.one('briefs',saved['id'])['markdown']  # No truncation or save rejection.
    assert store.one('briefs',base['id'])==base
    assert store.one('runs',run['id'])['requirements']==run['requirements']
    assert workspace_action(store,{'action':'read_report','version_id':base['id']})['length_stats']==stats


def test_reviewer_packet_exposes_the_same_saved_body_count_and_original_requirement(tmp_path):
    store,run,brief=case(tmp_path)
    _,files=review.build_packet(store,brief['id'],store.root/'review')
    packet=store.root/'review/packet'
    target=json.loads((packet/'target.json').read_text(encoding='utf-8'))
    requirements=json.loads((packet/'requirements.json').read_text(encoding='utf-8'))
    assert target['snapshot_version']==8
    assert target['length_stats']==requirements['length_stats']==store.brief_view(brief['id'])['length_stats']
    assert target['length_stats']['count']==743 and target['length_stats']['over_limit'] is False
    assert target['requirements']['objective']=='写一份400–700字报告。'
    assert target['requirements']['reader_contract']['clauses'][0]['instruction']=='写一份400–700字报告。'
    assert target['requirements_input']['max_words']==800
    # Existing release/de-duplication consumers keep the prior snapshot identity.
    assert review._snapshot(store,brief['id'])['snapshot_version']==7
    assert 'requirements.json' in files
    assert store.one('briefs',brief['id'])==brief and store.one('runs',run['id'])['requirements']==run['requirements']


def test_existing_snapshot_seven_remains_applicable_without_length_metadata(tmp_path,monkeypatch):
    store,run,brief=case(tmp_path)
    snapshot=review._snapshot
    with monkeypatch.context() as patch:
        patch.setattr(review,'_snapshot',lambda store,version_id,snapshot_version=7: snapshot(store,version_id,7))
        fingerprint,files=review.build_packet(store,brief['id'],store.root/'old-review')
    packet=store.root/'old-review/packet'
    assert 'length_stats' not in json.loads((packet/'target.json').read_text(encoding='utf-8'))
    assert 'length_stats' not in json.loads((packet/'requirements.json').read_text(encoding='utf-8'))
    before={name:(packet/name).read_bytes() for name in files}
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('old_review',brief['id'],None,fingerprint,'running',
             dump({'packet_path':'old-review/packet','files':files}),None,'2026','2026'))
    assert review.validate_applicable_review(store,'old_review',brief['id'])['fingerprint']==fingerprint
    from review_checks import for_version
    review.accept_review(store,'old_review',{'fingerprint':fingerprint,'version_id':brief['id'],
        'status':'complete','summary':'Existing version seven remains usable','coverage_scan_complete':True,
        'requirement_checks':for_version(store,brief['id']),'findings':[]})
    assert review.get_review(store,'old_review')['status']=='complete'
    review.validate_applicable_review(store,'old_review',brief['id'])
    assert {name:(packet/name).read_bytes() for name in files}==before


def test_pending_release_keeps_its_version_seven_input_after_new_review_diagnostics(tmp_path,monkeypatch):
    from test_release import reviewed_report
    from briefloop.release import eligibility,enqueue_release,get_release
    snapshot=review._snapshot
    with monkeypatch.context() as patch:
        patch.setattr(review,'_snapshot',lambda store,version_id,snapshot_version=7: snapshot(store,version_id,7))
        store,source,brief,*_=reviewed_report(tmp_path)
        queued=enqueue_release(store,brief['id'])
        frozen=get_release(store,queued['release']['id'])['data']
    assert frozen['snapshot']['snapshot_version']==7 and 'length_stats' not in frozen['snapshot']
    expected={key:value for key,value in frozen.items() if key not in ('notices','previous_id','change_type','change_reason')}
    assert eligibility(store,brief['id'])['input']==expected
