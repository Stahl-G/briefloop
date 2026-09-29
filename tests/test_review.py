import json
import pytest
from briefloop.store import Store
from briefloop.review import build_packet,accept_review,respond,review_status,ReviewOutput
from briefloop.opencode_harness import _permission_rules
from review_checks import for_version
from briefloop.backends.opencode_server import OpencodeServerClient,OpencodeError


def fixture(tmp_path):
    store=Store(tmp_path);src=store.add_source('Source','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain'},[src['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 million USD.'})
    job=store.enqueue('assess',{'version_id':brief['id']})
    folder=store.root/'jobs'/job['id'];fp,files=build_packet(store,brief['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('review_test',brief['id'],job['id'],fp,'running',json.dumps({'packet_path':str((folder/'packet').relative_to(store.root)),'files':files}),None,'2026','2026'))
    value={'fingerprint':fp,'version_id':brief['id'],'status':'complete','summary':'One issue','coverage_scan_complete':True,
           'requirement_checks':for_version(store,brief['id']),
           'assessment':{'brief_hash':brief['hash'],'status':'complete','summary':'Issue','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3},
           'findings':[{'kind':'insufficient_evidence','severity':'major','description':'Need support','evidence':'Source only contains one value','report_quote':'Revenue 12 million USD.'}]}
    return store,src,brief,value


def saved_review(store,brief,folder_name,identity):
    folder=store.root/folder_name;fingerprint,files=build_packet(store,brief['id'],folder)
    with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',(identity,brief['id'],None,fingerprint,'running',json.dumps({'packet_path':folder_name+'/packet','files':files}),None,'2026','2026'))
    return folder,fingerprint


def mixed_clause_review(tmp_path):
    from briefloop.deliverable_spec import clause_items, reader_contract_schema, resolve
    store = Store(tmp_path)
    source = store.add_source('Input', 'Shipment is planned.')
    run = store.create_run({
        'title': 'Report',
        'objective': 'Explain delivery. Verify plan against actual.',
        'key_questions': ['State shipment status.'],
        'writing_preferences': ['Use concise prose.'],
    }, [source['id']])
    spec = resolve(json.loads(run['requirements']))
    parents = {item['kind']: item['requirement_id'] for item in spec['requirement_items']}
    contract = {'source_fingerprint': reader_contract_schema(spec)['properties']['source_fingerprint']['const'],
                'clauses': [
                    {'requirement_id': parents['objective'], 'source_quote': 'Explain delivery.',
                     'kind': 'reader_content', 'instruction': 'Explain delivery.'},
                    {'requirement_id': parents['objective'], 'source_quote': 'Verify plan against actual.',
                     'kind': 'research_method', 'instruction': 'Verify plan against actual.'},
                    {'requirement_id': parents['question'], 'source_quote': 'State shipment status.',
                     'kind': 'reader_content', 'instruction': 'State shipment status.'},
                    {'requirement_id': parents['writing'], 'source_quote': 'Use concise prose.',
                     'kind': 'writing_preference', 'instruction': 'Use concise prose.'},
                ]}
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': 'Shipment is planned.',
                                       'reader_contract': contract})
    job = store.enqueue('review', {'version_id': brief['id']})
    folder = store.root/'jobs'/job['id']
    fingerprint, files = build_packet(store, brief['id'], folder)
    target = json.loads((folder/'packet/target.json').read_text())
    clauses = {}
    for item in clause_items(target['requirements']):
        key = ('objective_content' if item['kind'] == 'reader_content' else 'objective_method') \
            if item['requirement_id'] == parents['objective'] else item['kind']
        clauses[key] = item['clause_id']
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                  ('review_mixed', brief['id'], job['id'], fingerprint, 'running',
                   json.dumps({'protocol': 'clauses_v1', 'packet_path': str((folder/'packet').relative_to(store.root)),
                               'files': files}), None, '2026', '2026'))
    result = {'fingerprint': fingerprint, 'version_id': brief['id'], 'status': 'complete',
              'summary': 'Checked', 'coverage_scan_complete': True,
              'clause_checks': [{'clause_id': item['clause_id'], 'status': 'covered',
                                 'reason': 'Checked against source', 'basis': ['Input']}
                                for item in clause_items(target['requirements'])],
              'findings': []}
    return store, brief, target, parents, clauses, result
