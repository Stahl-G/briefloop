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


def test_findings_can_reference_packet_source_statements_without_promoting_them(tmp_path):
    import json
    from briefloop.review import build_packet, accept_review
    from tests.test_reconciliation import run_with_statements

    store, run, source, span, statement, other_statement = run_with_statements(tmp_path)
    # A real statement belonging to another report must still be rejected.
    foreign_run = store.create_run({'title': 'Other', 'objective': 'o'}, [source['id']])
    foreign = create_claim(store, foreign_run['id'], {
        'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement',
        'supports': [{'span_id': span['id'], 'supports_quote': '12 million USD'}]})
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': 'Body'})
    job = store.enqueue('assess', {'version_id': brief['id']})
    folder = store.root/'jobs'/job['id']
    fingerprint, files = build_packet(store, brief['id'], folder)
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)', (
            'review_statements', brief['id'], job['id'], fingerprint, 'running',
            json.dumps({'packet_path': str((folder/'packet').relative_to(store.root)), 'files': files}),
            None, '2026', '2026'))
    finding = {'kind': 'execution_gap', 'severity': 'major',
               'description': 'Author did not compare conflicting source statements',
               'evidence': 'Saved sources state 12 and 14 million USD',
               'claim_ids': [statement['id'], other_statement['id']]}
    value = {'version_id': brief['id'], 'fingerprint': fingerprint, 'status': 'complete',
             'summary': 'Comparison omitted', 'coverage_scan_complete': True, 'findings': [finding]}
    with pytest.raises(ValueError, match='范围外的主张ID'):
        accept_review(store, 'review_statements', {**value, 'findings': [{**finding, 'claim_ids': [foreign['id']]}]})
    with pytest.raises(ValueError, match='主张核查记录不属于本次范围'):
        accept_review(store, 'review_statements', {**value, 'claim_checks': [
            {'claim_id': statement['id'], 'status': 'supported_for_scope', 'reason': 'Source says so'}]})
    saved = accept_review(store, 'review_statements', value)
    assert saved['result']['findings'][0]['claim_ids'] == finding['claim_ids']
    assert saved['result']['claim_checks'] == []
    assert store.rows('SELECT * FROM claim_bindings WHERE version_id=?', (brief['id'],)) == []
