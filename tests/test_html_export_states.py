"""Reader status must agree with the frozen review inputs and delivery semantics."""
import json
import re

import pytest

from briefloop.html_export import html_report
from briefloop.release import decision, eligibility
from briefloop.review import accept_review, build_packet, respond, review_status
from briefloop.store import Store
from review_checks import for_version


def sample(tmp_path, **detail):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Synthetic source', 'Revenue is 12 million USD.')
    run = store.create_run({'title': 'Status report', 'objective': 'Explain revenue',
                            'language': 'en', 'allow_web': False}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Status report', 'markdown': 'Revenue is 12 million USD.', **detail})
    return store, run, brief


def complete_review(store, brief, identity='review_export', **result):
    folder = store.root / identity
    fingerprint, files = build_packet(store, brief['id'], folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity, brief['id'], None, fingerprint, 'running',
                            json.dumps({'packet_path': identity + '/packet', 'files': files}),
                            None, '2026', '2026'))
    accept_review(store, identity, {'fingerprint': fingerprint, 'version_id': brief['id'],
                                   'status': 'complete', 'summary': 'Synthetic review',
                                   'coverage_scan_complete': True,
                                   'requirement_checks': for_version(store, brief['id']),
                                   'findings': [], **result})
    return identity


def manifest(page):
    return json.loads(re.search(r'id="briefloop-manifest">(.*?)</script>', page, re.S).group(1))


def appendix(page):
    return page.split('<section id="appendix-checks">', 1)[1].split('</section>', 1)[0]


def test_mixed_findings_keep_response_pending_until_reviewed(tmp_path):
    store, run, brief = sample(tmp_path)
    findings = [{'kind': 'insufficient_evidence', 'severity': 'major', 'description': description,
                 'evidence': 'Synthetic basis', 'report_quote': 'Revenue is 12 million USD.'}
                for description in ('Still open', 'Response pending', 'Now resolved', 'Now dismissed')]
    complete_review(store, brief, findings=findings)
    rows = review_status(store, brief['id'])['findings']
    resolved = respond(store, rows[2]['id'], brief['id'], 'corrected', 'Added a synthetic basis')
    dismissed = respond(store, rows[3]['id'], brief['id'], 'disagree', 'Synthetic evidence explains the scope')
    complete_review(store, brief, 'review_followup', response_checks=[
        {'response_id': resolved['id'], 'decision': 'resolved', 'reason': 'Verified the correction'},
        {'response_id': dismissed['id'], 'decision': 'dismissed_with_evidence', 'reason': 'Verified the scope'}])
    respond(store, rows[1]['id'], brief['id'], 'corrected', 'Await independent confirmation')
    page = html_report(store, brief['id'])
    assert '2 open findings' in page
    checks = appendix(page)
    assert 'Still open' in checks and 'Response pending' in checks
    assert 'Response awaiting review' in checks
    assert 'Now resolved' not in checks and 'Now dismissed' not in checks
    assert manifest(page)['review']['applicable'] is False
    assert eligibility(store, brief['id'])['eligible'] is False
    # The gate still blocks a major author response awaiting independent review.
    gate = decision({'requirements': {'requirement_items': []}, 'evidence': {'bindings': []},
                     'conflicts': []}, {'status': 'complete', 'requirement_checks': []},
                    review_status(store, brief['id'])['findings'])
    assert any(item['code'] == 'finding_unresolved' for item in gate['blockers'])


def test_all_unresolved_gap_states_survive_structured_projection(tmp_path):
    records = [{'related': 'Revenue', 'impact': 'gap-' + state, 'action': 'Verify', 'status': state}
               for state in ('open', 'addressed', 'review_needed', 'unresolved', 'resolved')]
    store, _, brief = sample(tmp_path, gap_records=records, gaps=['obsolete legacy gap'])
    checks = appendix(html_report(store, brief['id']))
    for state in ('open', 'addressed', 'review_needed', 'unresolved'):
        assert 'gap-' + state in checks
    assert 'gap-resolved' not in checks and 'obsolete legacy gap' not in checks
    assert 'Addressed · awaiting review' in checks and 'Review required' in checks
    gate = decision({'requirements': {'requirement_items': []}, 'evidence': {'bindings': []},
                     'conflicts': [], 'detail': {'gap_records': records, 'gaps': ['obsolete legacy gap']}},
                    {'status': 'complete', 'requirement_checks': []}, [])
    assert len([item for item in gate['notices'] if item['code'].startswith('delivery_gap_')]) == 4


@pytest.mark.parametrize('records', [None, [], [{'related': 'Revenue', 'impact': 'Closed gap', 'status': 'resolved'}]])
def test_legacy_gaps_only_fallback_when_structured_records_are_empty(tmp_path, records):
    detail = {'gaps': ['Legacy gap']}
    if records is not None:
        detail['gap_records'] = records
    store, _, brief = sample(tmp_path, **detail)
    checks = appendix(html_report(store, brief['id']))
    assert ('Legacy gap' in checks) == (not records)
    assert 'Closed gap' not in checks


@pytest.mark.parametrize('change', ['evidence', 'body', 'requirements', 'packet'])
def test_completed_review_rejects_changed_inputs(tmp_path, change):
    store, run, brief = sample(tmp_path)
    identity = complete_review(store, brief)
    before = html_report(store, brief['id'])
    assert 'Independently reviewed · not released' in before
    assert manifest(before)['review']['applicable'] is True
    if change == 'evidence':
        source = store.add_source('New evidence', 'Revenue was revised to 13 million USD.')
        store.attach_source(run['id'], source['id'])
    elif change == 'body':
        # A changed stored body, even under the same id, must invalidate its packet.
        with store.tx() as connection:
            connection.execute('UPDATE briefs SET markdown=? WHERE id=?', ('Changed body', brief['id']))
    elif change == 'requirements':
        requirements = json.loads(run['requirements'])
        requirements['objective'] = 'Explain costs as well as revenue'
        with store.tx() as connection:
            connection.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(requirements), run['id']))
    else:
        (store.root / identity / 'packet' / 'target.json').unlink()
    page = html_report(store, brief['id'])
    assert 'Latest saved review does not apply to the current inputs' in page
    assert 'Independently reviewed · not released' not in page
    saved = manifest(page)['review']
    assert saved['id'] == identity and saved['status'] == 'complete'
    assert saved['applicable'] is False and saved['applicability_error']
    assert eligibility(store, brief['id'])['eligible'] is False


def test_revised_body_without_review_keeps_inherited_pending_findings(tmp_path):
    store, _, brief = sample(tmp_path)
    complete_review(store, brief, findings=[{'kind': 'insufficient_evidence', 'severity': 'major',
                    'description': 'Needs confirmation', 'evidence': 'Synthetic basis',
                    'report_quote': 'Revenue is 12 million USD.'}])
    revised = store.revise(brief['id'], 'Revenue is 13 million USD.')
    finding = review_status(store, brief['id'])['findings'][0]
    respond(store, finding['id'], revised['id'], 'corrected', 'Updated the body')
    page = html_report(store, revised['id'])
    assert 'Working draft · not independently reviewed · 1 open findings' in page
    assert 'Needs confirmation' in appendix(page) and 'Response awaiting review' in appendix(page)
    assert manifest(page)['review'] is None


@pytest.mark.parametrize('state', ['queued', 'running', 'failed'])
def test_latest_incomplete_review_does_not_fall_back_to_older_completion(tmp_path, state):
    store, _, brief = sample(tmp_path)
    complete_review(store, brief)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           ('review_newest', brief['id'], None, 'pending', state, '{}', None, '2026', '2026'))
    page = html_report(store, brief['id'])
    expected = 'Independent review in progress' if state != 'failed' else 'Latest independent review did not complete'
    assert expected in appendix(page)
    assert 'Independently reviewed · not released' not in page
    assert manifest(page)['review'] == {'id': 'review_newest', 'status': state,
                                        'applicable': None, 'applicability_error': None}


def test_chinese_pending_review_and_gap_labels(tmp_path):
    store, run, brief = sample(tmp_path, gap_records=[{'related': '收入', 'impact': '仍缺少支持', 'status': 'addressed'}])
    requirements = json.loads(run['requirements'])
    requirements['language'] = 'zh'
    with store.tx() as connection:
        connection.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(requirements), run['id']))
    complete_review(store, brief, findings=[{'kind': 'insufficient_evidence', 'severity': 'major',
                    'description': '等待确认', 'evidence': 'Synthetic basis',
                    'report_quote': 'Revenue is 12 million USD.'}])
    respond(store, review_status(store, brief['id'])['findings'][0]['id'], brief['id'], 'corrected', '已补说明')
    page = html_report(store, brief['id'])
    assert '最近一次已保存审阅不适用于当前输入' in page and '1 项未结发现' in page
    assert '已回应 · 待复核' in appendix(page) and '已处理 · 待复核' in appendix(page)


def test_released_version_review_appendix_does_not_contradict_cover(tmp_path, monkeypatch):
    store, _, brief = sample(tmp_path)
    complete_review(store, brief)
    from briefloop import release
    monkeypatch.setattr(release, 'list_releases', lambda *_: [
        {'id': 'release-fixture', 'version_id': brief['id'], 'status': 'released',
         'result': {'manifest_hash': 'frozen-manifest'}}])
    page = html_report(store, brief['id'])
    assert 'This version has a formal delivery' in page
    assert 'Independent review complete' in appendix(page)
    assert 'not released' not in page
    assert manifest(page)['review']['applicable'] is True
