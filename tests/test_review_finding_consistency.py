"""Major assessment issues cannot disappear between scoring and delivery."""
import json
from copy import deepcopy

import pytest

from briefloop.review import (ReviewOutput, accept_review, build_packet,
                              get_review, review_status, run_review)
from briefloop.release import decision, eligibility, enqueue_release, sha, validate_release
from briefloop.store import Store, dump, now
from review_checks import for_version


def fixture(tmp_path):
    """Offline saved-response replay: no selected model and no model dispatch."""
    store = Store(tmp_path)
    source = store.add_source('Synthetic source', 'Revenue was USD 12 million.')
    run = store.create_run({'title': 'Synthetic report', 'objective': 'Explain revenue'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic report', 'markdown': 'Revenue was USD 12 million.'})
    job_id = 'job_offline_review'
    folder = store.root/'jobs'/job_id
    fingerprint, files = build_packet(store, brief['id'], folder)
    with store.tx() as connection:
        # An existing review job, not a request to start a configured model. The
        # empty frozen runtime also prevents the scripted replay reading settings.
        connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',
            (job_id, 'review', 'running', dump({'version_id': brief['id'], 'runtime': {}}), None, None, now(), now()))
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
            ('review_test', brief['id'], job_id, fingerprint, 'running',
             dump({'packet_path': str((folder/'packet').relative_to(store.root)), 'files': files}), None, now(), now()))
    value = {'fingerprint': fingerprint, 'version_id': brief['id'], 'status': 'complete',
        'summary': 'Synthetic source checked', 'coverage_scan_complete': True,
        'requirement_checks': for_version(store, brief['id']),
        'assessment': {'brief_hash': brief['hash'], 'status': 'complete', 'summary': 'Synthetic issue',
            'overall': '建议修改', 'evidence': 3, 'coverage': 3, 'analysis': 3, 'expression': 3},
        'findings': [{'kind': 'insufficient_evidence', 'severity': 'major', 'description': 'Need support',
                     'evidence': 'Source only contains one value'}]}
    return store, source, brief, value


def reviewed_report(tmp_path):
    store, source, brief, value = fixture(tmp_path)
    value['findings'] = []
    accept_review(store, 'review_test', value)
    return store, source, brief, 'review_test'


def major(description, dimension='evidence'):
    return {'dimension': dimension, 'severity': 'major', 'description': description,
            'evidence': 'Saved source contradicts the report.'}


def with_assessment_issues(value):
    value = deepcopy(value)
    value['assessment']['findings'] = [major('Wrong amount'), major('Wrong date')]
    return value


@pytest.mark.parametrize('top_level', ['omitted', 'empty', 'partial'])
def test_complete_review_cannot_drop_major_assessment_issues_or_partially_save(tmp_path, top_level):
    store, source, brief, value = fixture(tmp_path)
    value = with_assessment_issues(value)
    if top_level == 'omitted':
        value.pop('findings')
    elif top_level == 'empty':
        value['findings'] = []
    else:
        value['findings'][0]['assessment_finding_indices'] = [0]
    with pytest.raises(ValueError, match=r'assessment.findings\[1\].*major'):
        accept_review(store, 'review_test', value)
    assert get_review(store, 'review_test')['result'] is None
    assert not store.rows('SELECT * FROM assessments')
    assert not store.rows('SELECT * FROM review_findings')
    # Incomplete output can retain scoring observations without inventing a
    # missing review finding, but remains ineligible for formal delivery.
    accepted = accept_review(store, 'review_test', {**value, 'status': 'incomplete'})
    assert accepted['result']['assessment']['findings'][1]['description'] == 'Wrong date'
    assert len(review_status(store, brief['id'])['findings']) == (1 if top_level == 'partial' else 0)
    assert not eligibility(store, brief['id'])['eligible']


def test_explicit_links_allow_distinct_descriptions_and_many_to_one_without_downgrades(tmp_path):
    store, source, brief, value = fixture(tmp_path)
    value = with_assessment_issues(value)
    value['findings'][0]['assessment_finding_indices'] = [0, 1]
    for patch in ({'severity': 'minor'}, {'kind': 'expression'}, {'response_to': 'response_old'},
                  {'assessment_finding_indices': [0, 2]}):
        broken = deepcopy(value)
        broken['findings'][0].update(patch)
        with pytest.raises(ValueError, match='assessment_finding_indices'):
            accept_review(store, 'review_test', broken)
        assert not store.rows('SELECT * FROM review_findings')
    broken = deepcopy(value)
    broken['findings'][0]['assessment_finding_indices'] = [True]
    with pytest.raises(ValueError, match='valid integer'):
        ReviewOutput.model_validate(broken)
    accepted = accept_review(store, 'review_test', value)
    assert accepted['status'] == 'complete'
    assert review_status(store, brief['id'])['findings'][0]['data']['assessment_finding_indices'] == [0, 1]
    assert not eligibility(store, brief['id'])['eligible']  # The actual major issue still blocks.


def test_legacy_exact_links_and_expression_only_issues_remain_usable(tmp_path):
    store, source, brief, value = fixture(tmp_path)
    value['assessment']['findings'] = [major(value['findings'][0]['description'])]
    assert accept_review(store, 'review_test', value)['status'] == 'complete'
    # No summary or overall prose is used to infer additional problems.
    clean, source, brief, review_id = reviewed_report(tmp_path/'clean')
    checked = eligibility(clean, brief['id'])
    result = deepcopy(checked['input']['review_result'])
    result['summary'] = '存在重大问题 - this summary is not a structural finding.'
    result['assessment']['findings'] = [major('Shorten the wording', 'expression')]
    expression = {'kind': 'expression', 'severity': 'major', 'description': 'Reader-facing edit',
                  'evidence': 'Repeated wording', 'assessment_finding_indices': [0]}
    result['findings'] = [expression]
    assert decision(checked['input']['snapshot'], result,
                    [{'id': 'expression', 'status': 'open', 'data': expression}])['eligible']
    result['findings'] = []
    result['assessment']['findings'] = [major('Optional minor edit', 'expression') | {'severity': 'minor'}]
    assert decision(checked['input']['snapshot'], result, [])['eligible']
    result['assessment'] = None
    assert decision(checked['input']['snapshot'], result, [])['eligible']


def test_rejected_runtime_output_is_archived_with_repairable_error(tmp_path):
    store, source, brief, value = fixture(tmp_path)
    job = store.one('jobs', store.rows('SELECT job_id FROM reviews')[0]['job_id'])
    folder = store.root/'jobs'/job['id']
    (folder/'review-id.json').write_text(dump({'review_id': 'review_test'}))
    invalid = with_assessment_issues(value)
    invalid.pop('findings')
    raw = dump(invalid).encode()

    class Runtime:
        calls = 0
        output = raw

        def execute(self, stage, prompt, target, **kwargs):
            self.calls += 1
            assert 'assessment_finding_indices' in prompt
            (target/'review.json').write_bytes(self.output)

    runtime = Runtime()
    with pytest.raises(ValueError, match=r'assessment.findings\[0\]'):
        run_review(store, runtime, job, brief['id'], folder)
    assert runtime.calls == 1
    assert get_review(store, 'review_test')['status'] == 'incomplete'
    assert get_review(store, 'review_test')['result'] is None
    error = json.loads((folder/'admission-error.json').read_text())
    assert 'assessment_finding_indices:[0]' in error['error']
    assert (folder/error['original_output']).read_bytes() == raw
    assert (folder/'review.json').read_bytes() == raw
    assert not store.rows('SELECT * FROM assessments')
    assert not store.rows('SELECT * FROM review_findings')
    repaired = with_assessment_issues(value)
    repaired['findings'][0]['assessment_finding_indices'] = [0, 1]
    runtime.output = dump(repaired).encode()
    assert run_review(store, runtime, job, brief['id'], folder)['status'] == 'complete'
    assert runtime.calls == 2
    assert (folder/error['original_output']).read_bytes() == raw
    assert len(store.rows('SELECT * FROM review_findings')) == 1


def test_old_saved_inconsistent_review_blocks_delivery_without_rewriting_history(tmp_path):
    store, source, brief, review_id = reviewed_report(tmp_path)
    checked = eligibility(store, brief['id'])
    assert checked['eligible']
    old_result = with_assessment_issues(checked['input']['review_result'])
    raw = dump(old_result)
    with store.tx() as connection:
        connection.execute('UPDATE reviews SET result=? WHERE id=?', (raw, review_id))
    checked = eligibility(store, brief['id'])
    assert not checked['eligible']
    assert {item['code'] for item in checked['blockers']} == {'review_findings_inconsistent'}
    with pytest.raises(ValueError, match='major'):
        enqueue_release(store, brief['id'])
    assert store.rows('SELECT result FROM reviews WHERE id=?', (review_id,))[0]['result'] == raw
    assert not store.rows('SELECT * FROM releases')
    assert not store.rows('SELECT * FROM review_findings')
    # Previously frozen formal records have the same guard, before serving their
    # files. Ordinary working-copy exports remain independent of review status.
    frozen = {'data': {'review_result': old_result}, 'status': 'released', 'result': {'path': 'old.docx'}}
    frozen['fingerprint'] = sha(dump(frozen['data']).encode())
    with pytest.raises(ValueError, match='请补全独立审阅'):
        validate_release(store, frozen)
    from briefloop.export_jobs import enqueue_export, generate_word, output_path
    import threading
    job = enqueue_export(store, brief['id'])
    output = generate_word(store, job, threading.Event())
    store.update_job(job['id'], 'complete', result=output)
    assert output_path(store, store.one('jobs', job['id'])).is_file()
