"""Author coverage statements are checked against search/page receipts, not trusted."""
import json

from briefloop.store import Store, dump
from briefloop.coverage_record import split, bind


def test_coverage_block_is_split_and_checks_bind_to_receipts(tmp_path):
    store=Store(tmp_path)
    ready=store.add_source('Official','收入 120 万元',url='https://example.com/ir')
    run=store.create_run({'title':'T','objective':'O','allow_web':False,'key_questions':['收入多少？','有无诉讼？','评级？']},[ready['id']])
    folder=store.root/'discovery'/run['id'];folder.mkdir(parents=True)
    (folder/'q1.request.json').write_text(dump({'query':'Example  litigation 2026','local_request_id':'req_1'}))
    text=('# 报告\n\n收入 120 万元。[@'+ready['id']+']\n\n```briefloop-coverage\n'
          +json.dumps({'questions':[{'question':1,'status':'answered','sources':[ready['id'],'src_fake'],'checked':['https://example.com/ir']},
                                     {'question':2,'status':'checked_not_found','checked':['example litigation 2026','我记得没有']},
                                     {'question':2,'status':'answered'}]},ensure_ascii=False)+'\n```\n')
    body,items,error=split(text)
    assert 'briefloop-coverage' not in body and error is None
    record=bind(store,run['id'],['收入多少？','有无诉讼？','评级？'],items)
    q1,q2,q3=record['items']
    assert q1['status']=='answered' and q1['sources']==[ready['id']] and 'unknown_sources_dropped' in q1['flags']
    assert q1['checked'][0]['receipt']=='page'
    assert [c['receipt'] for c in q2['checked']]==['metered_search','self_reported'] and 'check_self_reported' not in q2['flags']
    assert q3['status']=='not_checked' and q3['recorded'] is False
    bad=bind(store,run['id'],['收入多少？'],[{'question':1,'status':'answered','sources':[]}])
    assert 'no_readable_source' in bad['items'][0]['flags']
