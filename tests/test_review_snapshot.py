import pytest
from briefloop import reconciliation
from briefloop.evidence import create_claim, create_span
from briefloop.review import _snapshot
from briefloop.store import Store


def test_review_snapshot_separates_source_statements_and_reconciliation(tmp_path):
    store = Store(tmp_path)
    run = store.create_run({'title': 'Report', 'objective': 'o', 'allow_web': True}, [])
    source = store.add_source('A', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    span = create_span(store, {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    statement = create_claim(store, run['id'], {'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement', 'supports': [{'span_id': span['id'], 'supports_quote': '12 million USD'}]})
    candidate = create_claim(store, run['id'], {'statement': 'Revenue was 12 million USD in H1.', 'kind': 'fact'})
    record = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': [statement['id']], 'unexamined_claim_ids': []})
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Body', 'reconciliation_id': record['id']})
    snapshot = _snapshot(store, brief['id'])
    candidate_ids = [item['claim_id'] for item in snapshot['candidate_claims']]
    assert statement['id'] not in candidate_ids, 'source statements are not report candidates'
    assert candidate['id'] in candidate_ids
    assert [item['claim_id'] for item in snapshot['source_statements']] == [statement['id']]
    assert snapshot['reconciliation']['id'] == record['id']
    assert snapshot['reconciliation']['stale'] is False
    legacy = _snapshot(store, brief['id'], snapshot_version=5)
    assert statement['id'] in [item['claim_id'] for item in legacy['candidate_claims']]
    assert 'reconciliation' not in legacy


def test_review_snapshot_without_reconciliation_stays_none(tmp_path):
    store = Store(tmp_path)
    run = store.create_run({'title': 'Report', 'objective': 'o', 'allow_web': True}, [])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Body'})
    snapshot = _snapshot(store, brief['id'])
    assert snapshot['reconciliation'] is None
    assert snapshot['source_statements'] == []


def test_review_status_exposes_reconciliation_for_the_rail(tmp_path):
    from briefloop.review import review_status
    store = Store(tmp_path)
    run = store.create_run({'title': 'Report', 'objective': 'o', 'allow_web': True}, [])
    source = store.add_source('A', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], source['id'])
    span = create_span(store, {'source_id': source['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    statement = create_claim(store, run['id'], {'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement', 'supports': [{'span_id': span['id'], 'supports_quote': '12 million USD'}]})
    record = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': [statement['id']], 'unexamined_claim_ids': []})
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Body', 'reconciliation_id': record['id']})
    status = review_status(store, brief['id'])
    assert status['reconciliation']['id'] == record['id'] and status['reconciliation']['stale'] is False
