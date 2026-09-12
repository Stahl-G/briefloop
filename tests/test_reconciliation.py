import json
import pytest
from briefloop import reconciliation
from briefloop.conflicts import create as create_conflict
from briefloop.evidence import create_claim, create_span
from briefloop.store import Store


def run_with_statements(tmp_path):
    store = Store(tmp_path)
    run = store.create_run({'title': 'Report', 'objective': 'o', 'allow_web': True}, [])
    first = store.add_source('A', 'Revenue 12 million USD in H1.')
    store.attach_source(run['id'], first['id'])
    second = store.add_source('B', 'Revenue was 14 million USD in H1.')
    store.attach_source(run['id'], second['id'])
    span_a = create_span(store, {'source_id': first['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    span_b = create_span(store, {'source_id': second['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    claim_a = create_claim(store, run['id'], {'statement': 'Revenue 12 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement', 'attribution': 'Company A', 'supports': [{'span_id': span_a['id'], 'supports_quote': '12 million USD'}]})
    claim_b = create_claim(store, run['id'], {'statement': 'Revenue was 14 million USD in H1.', 'kind': 'fact',
        'claim_role': 'source_statement', 'attribution': 'Company B', 'supports': [{'span_id': span_b['id'], 'supports_quote': '14 million USD'}]})
    return store, run, first, span_a, claim_a, claim_b


def test_candidates_list_sources_and_source_statements(tmp_path):
    store, run, first, span_a, claim_a, claim_b = run_with_statements(tmp_path)
    index = reconciliation.candidates(store, run['id'])
    assert {s['source_id'] for s in index['sources']} == set(store.source_ids(run['id']))
    assert set(index['statement_claim_ids']) == {claim_a['id'], claim_b['id']}
    assert index['requirements_fingerprint']


def test_save_covers_candidates_and_refuses_bad_references(tmp_path):
    store, run, first, span_a, claim_a, claim_b = run_with_statements(tmp_path)
    ids = [claim_a['id'], claim_b['id']]
    with pytest.raises(reconciliation.ReconciliationError, match='未知关系'):
        reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': [],
            'relations': [{'member_claim_ids': ids, 'relation': 'made_up'}]})
    with pytest.raises(reconciliation.ReconciliationError, match='两个不同'):
        reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': [],
            'relations': [{'member_claim_ids': [claim_a['id']], 'relation': 'contradiction'}]})
    with pytest.raises(reconciliation.ReconciliationError, match='不属于本轮'):
        reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': [claim_a['id']], 'unexamined_claim_ids': [],
            'relations': [{'member_claim_ids': [claim_a['id'], 'claim_missing'], 'relation': 'contradiction'}]})
    with pytest.raises(reconciliation.ReconciliationError, match='明确划分'):
        reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': [claim_a['id']], 'unexamined_claim_ids': []})
    record = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': [],
        'relations': [{'member_claim_ids': ids, 'relation': 'different_scope', 'scope': 'A vs B', 'basis_span_ids': [span_a['id']],
                       'reason': 'same metric different period', 'proposed_treatment': 'separate the scopes'}]})
    assert record['id'].startswith('recon_') and record['relations'][0]['relation'] == 'different_scope'
    duplicate = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': [],
        'relations': [{'member_claim_ids': ids, 'relation': 'different_scope', 'scope': 'A vs B', 'basis_span_ids': [span_a['id']],
                       'reason': 'same metric different period', 'proposed_treatment': 'separate the scopes'}]})
    assert duplicate['id'] == record['id']
    assert reconciliation.read(store, run['id'], record['id'])['stale'] is False


def test_input_change_marks_snapshot_stale(tmp_path):
    store, run, first, span_a, claim_a, claim_b = run_with_statements(tmp_path)
    ids = [claim_a['id'], claim_b['id']]
    record = reconciliation.save(store, run['id'], {'status': 'partial', 'examined_claim_ids': [claim_a['id']],
        'unexamined_claim_ids': [claim_b['id']]})
    extra = store.add_source('C', 'A later filing.')
    store.attach_source(run['id'], extra['id'])
    assert reconciliation.read(store, run['id'], record['id'])['stale'] is True


def test_conflict_keeps_participants_and_reconciliation_reference(tmp_path):
    store, run, first, span_a, claim_a, claim_b = run_with_statements(tmp_path)
    conflict = create_conflict(store, source_ids=[first['id']], description='口径差异', run_id=run['id'],
                               kind='different_scope', importance='supporting',
                               participants=[{'claim_id': claim_a['id'], 'span_ids': [span_a['id']]}], scope='H1')
    assert conflict['data']['participants'][0]['claim_id'] == claim_a['id']
    ids = [claim_a['id'], claim_b['id']]
    record = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': [],
        'relations': [{'member_claim_ids': ids, 'relation': 'contradiction', 'conflict_id': conflict['id'], 'reason': 'conflict'}]})
    with pytest.raises(ValueError, match='对照记录不存在'):
        create_conflict(store, source_ids=[first['id']], description='x', run_id=run['id'], reconciliation_id='recon_missing')


def test_publish_requires_a_real_reconciliation(tmp_path):
    store, run, first, span_a, claim_a, claim_b = run_with_statements(tmp_path)
    with pytest.raises(ValueError, match='对照记录不存在'):
        store.publish(run['id'], {'title': 'T', 'markdown': 'Body', 'reconciliation_id': 'recon_missing'})
    ids = [claim_a['id'], claim_b['id']]
    record = reconciliation.save(store, run['id'], {'status': 'complete', 'examined_claim_ids': ids, 'unexamined_claim_ids': []})
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Body', 'reconciliation_id': record['id']})
    assert brief['id']
