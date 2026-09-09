"""Synthetic source changes exercise lineage; no network or model inference."""
import json

import pytest

from briefloop.conflicts import accept_check, respond
from briefloop.evidence import bind_claim, create_claim, create_span
from briefloop.source_updates import (SCHEMA, availability, for_run, for_version, get_change,
                                      impacts, record_change, refresh, register_snapshot)
from briefloop.store import Store, dump, now


def case(tmp_path, *, allow_web=False, page_budget=3):
    store = Store(tmp_path)
    with store.tx() as connection:
        connection.executescript(SCHEMA)
    old = store.add_source('H1 disclosure', 'Revenue was USD 12 million in H1.', url='https://example.test/disclosure')
    run = store.create_run({'title': 'Report', 'objective': 'Revenue changes', 'allow_web': allow_web,
                            'research_budget': {'search_requests': 0, 'candidate_urls': 0, 'source_pages': page_budget}}, [old['id']])
    brief = store.publish(run['id'], {'title': 'Report', 'editor_document': {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Revenue was USD 12 million in H1.'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Growth may increase working capital.'}]}]}})
    span = create_span(store, {'source_id': old['id'], 'locator': {'kind': 'text', 'start_line': 1, 'end_line': 1}})
    fact = create_claim(store, run['id'], {'kind': 'fact', 'statement': 'Revenue was USD 12 million in H1.',
                                          'supports': [{'span_id': span['id'], 'supports_quote': 'USD 12 million'}]})
    inference = create_claim(store, run['id'], {'kind': 'inference', 'statement': 'Growth may increase working capital.',
                                               'premise_claim_ids': [fact['id']], 'reasoning': 'More activity may require inventory.'})
    document = json.loads(brief['editor_document'])
    bind_claim(store, brief['id'], fact['id'], document['content'][0]['attrs']['blockId'], 'USD 12 million')
    bind_claim(store, brief['id'], inference['id'], document['content'][1]['attrs']['blockId'], 'Growth may increase working capital.')
    return store, old, run, brief, fact, inference


def change_request(old, new, **options):
    return {'old_source_id': old['id'], 'new_source_id': new['id'],
            'kind': 'correction', 'relation': 'corrects', 'description': 'The issuer corrects the H1 revenue unit.',
            'scope': 'Company H1 revenue only', 'relationship_evidence': 'New source line 1 explicitly corrects H1.',
            'information_cutoff': '2026-08-31',
            'old_timing': {'effective_start': '2026-01-01', 'effective_end': '2026-06-30',
                           'published_at': '2026-07-15', 'available_at': '2026-07-15', 'basis': 'Original publication header'},
            'new_timing': {'effective_start': '2026-01-01', 'effective_end': '2026-06-30',
                           'published_at': '2026-09-01', 'available_at': '2026-09-01', 'basis': 'Correction header'}, **options}


def test_correction_propagates_to_premises_and_keeps_historical_release(tmp_path):
    store, old, run, brief, fact, inference = case(tmp_path)
    # A synthetic already-delivered record proves updates do not mutate it. This
    # is not a live delivery acceptance test.
    from briefloop.release import SCHEMA as RELEASE_SCHEMA
    with store.tx() as connection:
        connection.executescript(RELEASE_SCHEMA)
        connection.execute('INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           ('release_old', brief['id'], None, 'fixed-before-update', 'released', dump({'original': True}),
                            dump({'file': 'retained.docx'}), None, None, '', now(), now()))
    original_release = store.rows('SELECT * FROM releases')[0]
    new = store.add_source('Correction', 'Correction: H1 revenue was USD 120 million.', url='https://example.test/correction')
    store.attach_source(run['id'], new['id'])
    change = record_change(store, change_request(old, new), run_id=run['id'])
    affected = change['current_impacts']
    assert affected['direct_claim_ids'] == [fact['id']]
    assert affected['indirect_claim_ids'] == [inference['id']]
    assert affected['versions'][0]['version_id'] == brief['id']
    assert affected['releases'][0]['release_id'] == 'release_old'
    assert change['data']['new_availability'] == 'after_cutoff'
    assert change['data']['old_availability'] == 'available_by_cutoff'
    assert change['data']['classification_status'] == 'proposed'
    assert change['review_status'] == 'open'
    respond(store, change['conflict_id'], 'adopt_new', 'The new disclosure says correction; request independent check.')
    assert get_change(store, change['id'])['review_status'] == 'addressed_pending_review'
    # A warning or author response cannot become verified learning input.
    assert get_change(store, change['id'])['data']['classification_status'] == 'proposed'
    with store.tx() as connection:
        accept_check(store, connection, change['conflict_id'], 'confirmed_correction',
                     'Original and correction explicitly refer to the same H1 metric.', 'synthetic-review')
    assert get_change(store, change['id'])['review_status'] == 'resolved'
    assert store.one('briefs', brief['id']) == brief
    assert store.one('sources', old['id']) == old
    assert store.rows('SELECT * FROM releases')[0] == original_release
    assert not store.rows('SELECT id FROM feedback')
    assert for_version(store, brief['id'])[0]['id'] == change['id']


def test_new_period_is_proposed_update_not_retroactive_error_or_automatic_replacement(tmp_path):
    store, old, run, brief, _, _ = case(tmp_path)
    new = store.add_source('Q3 update', 'Q3 revenue was USD 15 million.')
    store.attach_source(run['id'], new['id'])
    value = change_request(old, new, kind='update', relation='new_period', description='New quarter results.',
                           scope='Q3 does not replace H1', relationship_evidence='',
                           new_timing={'effective_start': '2026-07-01', 'effective_end': '2026-09-30',
                                       'published_at': '2026-10-15', 'available_at': '2026-10-15', 'basis': 'Quarterly report cover'})
    result = record_change(store, value, run_id=run['id'])
    assert result['data']['change_type'] == 'update'
    assert result['data']['classification_status'] == 'proposed' and result['review_status'] == 'open'
    later = store.create_run({'title': 'Next report', 'objective': 'Use retained H1 facts'}, [old['id']])
    assert for_run(store, later['id'])[0]['id'] == result['id']
    assert store.one('briefs', brief['id'])['markdown'] == brief['markdown']
    assert not store.rows('SELECT id FROM feedback')
    with pytest.raises(ValueError, match='原文定位'):
        record_change(store, {**value, 'relation': 'supersedes'}, run_id=run['id'])


def test_time_annotations_keep_all_clocks_and_append_explicit_correction(tmp_path):
    store, old, _, _, _, _ = case(tmp_path)
    first = register_snapshot(store, old['id'], timing={'published_at': '2026-07-15', 'basis': 'Page 1'})
    assert first['data']['fetched_at'] is None
    assert first['data']['saved_at'] == old['created']
    assert availability(first['data']['timing'], '2026-08-31') == 'availability_unknown'
    assert availability({'available_at': '2026-08-31'}, '2026-08-31') == 'overlapping_date_precision'
    with pytest.raises(ValueError, match='previous_id'):
        register_snapshot(store, old['id'], timing={'available_at': '2026-07-16', 'basis': 'Archive availability'})
    second = register_snapshot(store, old['id'], timing={'published_at': '2026-07-15', 'available_at': '2026-07-16',
                                                        'basis': 'Archive availability'}, previous_id=first['id'])
    assert second['previous_id'] == first['id'] and second['logical_id'] == first['logical_id']
    assert json.loads(store.rows('SELECT data FROM source_snapshot_metadata WHERE id=?', (first['id'],))[0]['data']) == first['data']
    with pytest.raises(ValueError, match='逻辑身份'):
        register_snapshot(store, old['id'], logical_id='unrelated', previous_id=second['id'])
    with pytest.raises(ValueError, match='ISO'):
        availability({'available_at': '2026-08-01T12:00:00'}, '2026-08-31')


def test_refresh_obeys_permission_and_budget_and_never_reports_cache_as_fresh(tmp_path, monkeypatch):
    from briefloop import sources
    calls = []

    def acquire(store, url):
        calls.append(url)
        return store.add_source('Fresh response', 'Revenue was USD 12 million in H1.', url=url)

    monkeypatch.setattr(sources, 'fetch', acquire)
    store, old, run, _, _, _ = case(tmp_path / 'offline')
    blocked = refresh(store, run['id'], old['id'], information_cutoff='2026-08-31')
    assert blocked['outcome'] == 'not_authorized' and blocked['new_source_id'] is None and not calls
    store, old, run, _, _, _ = case(tmp_path / 'zero-budget', allow_web=True, page_budget=0)
    blocked = refresh(store, run['id'], old['id'], information_cutoff='2026-08-31')
    assert blocked['outcome'] == 'budget_exhausted' and not calls
    store, old, run, _, _, _ = case(tmp_path / 'online', allow_web=True)
    result = refresh(store, run['id'], old['id'], information_cutoff='2026-08-31', trigger='next_run')
    assert len(calls) == 1 and result['outcome'] == 'unchanged_snapshot'
    assert result['new_source_id'] != old['id']
    assert result['data']['budget']['used']['source_pages'] == 1
    assert store.one('sources', old['id']) == old

    def changed(store, url):
        return store.add_source('Changed response', 'H1 revenue corrected to USD 120 million.', url=url)

    monkeypatch.setattr(sources, 'fetch', changed)
    result = refresh(store, run['id'], old['id'], information_cutoff='2026-08-31')
    assert result['outcome'] == 'changed_needs_review' and result['data']['conflict_id']
    new = store.one('sources', result['new_source_id'])
    change = record_change(store, change_request(old, new, conflict_id=result['data']['conflict_id']), run_id=run['id'])
    assert change['conflict_id'] == result['data']['conflict_id']
    assert len(store.rows('SELECT id FROM conflicts')) == 1

    monkeypatch.setattr(sources, 'fetch', lambda store, url: store.add_source('Unavailable', '', url=url, error='HTTP 503'))
    result = refresh(store, run['id'], old['id'], information_cutoff='2026-08-31')
    assert result['outcome'] == 'fetch_failed' and result['data']['error'] == 'HTTP 503'
    assert result['outcome'] != 'unchanged_snapshot'
