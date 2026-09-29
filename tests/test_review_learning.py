"""Learning consumes accepted historical packets and like-for-like sources."""
import json

import pytest

from briefloop.learning import _baseline_for_attempt, _experience, _generate_trial, _conditions
from briefloop.review import accept_review, build_packet, respond, review_status
from briefloop.review_learning import record_verified_corrections, source_snapshot
from briefloop.store import Store, dump, now


def attempt(tmp_path):
    store=Store(tmp_path);source=store.add_source('Facts','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain revenue','allow_web':False},[source['id']])
    job=store.enqueue('generate',{'run_id':run['id']})
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 million USD.'},version_id='brief_'+job['id'][4:])
    payload={**json.loads(job['payload']),'skill_id':None}
    folder=store.root/'jobs'/job['id'];folder.mkdir(exist_ok=True)
    (folder/'input.json').write_text(dump({'requirements':json.loads(run['requirements'])}),encoding='utf-8')
    return store,source,run,job,brief,payload


def save_review(store,brief,identity):
    folder=store.root/identity;fingerprint,files=build_packet(store,brief['id'],folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity,brief['id'],None,fingerprint,'running',dump({'packet_path':identity+'/packet','files':files}),None,now(),now()))
    from review_checks import for_version
    return {'version_id':brief['id'],'fingerprint':fingerprint,'status':'complete','summary':'Synthetic check',
            'requirement_checks':for_version(store,brief['id']),
            'coverage_scan_complete':True,'assessment':{'brief_hash':brief['hash'],'status':'complete','summary':'Check',
                                                     'overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3}}


def reviewed_response(tmp_path,decision='resolved'):
    store,source,run,job,brief,payload=attempt(tmp_path)
    initial=save_review(store,brief,'review_before')
    initial['findings']=[{'kind':'insufficient_evidence','severity':'major','description':'Scope unsupported',
                          'evidence':'The original only supports the stated period.','report_quote':'Revenue 12 million USD.'}]
    accept_review(store,'review_before',initial)
    finding=review_status(store,brief['id'])['findings'][0]
    revised=store.publish(run['id'],{'title':'Report','markdown':'Revenue was 12 million USD in the stated period.'},parent_id=brief['id'])
    response=respond(store,finding['id'],revised['id'],'corrected','Added the original period and removed broader claims.')
    final=save_review(store,revised,'review_after')
    final['response_checks']=[{'response_id':response['id'],'decision':decision,'reason':'Compared saved original and revision.'}]
    accept_review(store,'review_after',final)
    return store,source,run,brief,revised,response,final


