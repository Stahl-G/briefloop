"""Explicit revision context and honest check scope, without extra model turns."""
import json

import pytest

from briefloop.models import Assessment
from briefloop.runtime import Worker, assessment_prompt
from briefloop.store import Store, dump


def score(brief, **extra):
    return {'brief_hash': brief['hash'], 'summary': 'Reviewed the supplied text.',
            'overall': '达到要求', 'evidence': 3, 'coverage': 3, 'analysis': 3,
            'expression': 3, **extra}


def pair(tmp_path):
    store = Store(tmp_path)
    source = store.add_source('Synthetic announcement', 'Public beta is available to all developers. General availability is planned.')
    run = store.create_run({'title': 'Weekly', 'objective': 'Explain what changed.'}, [source['id']])
    first = store.publish(run['id'], {'title': 'Weekly', 'markdown': 'Summary: generally available.\n\nBody: public beta.'})
    previous = store.assess(first['id'], score(first, findings=[{
        'dimension': 'evidence', 'severity': 'minor', 'description': 'Summary omits the public beta condition.',
        'report_quote': 'Summary: generally available.', 'evidence': 'Only a public beta was announced.',
        'source_id': source['id']}]))
    revised = store.revise(first['id'], 'Summary: generally available.\n\nBody: public beta, not general availability.')
    return store, first, revised, previous


@pytest.mark.parametrize('backend', ['codex', 'briefloop-native'])
def test_recheck_packet_uses_exact_parent_and_keeps_original_problem(tmp_path, backend):
    store, first, revised, previous = pair(tmp_path)
    # The latest score belongs to the child; it must not replace the old concern.
    store.assess(revised['id'], score(revised, summary='Unrelated new assessment.'))
    folder = tmp_path / 'recheck'; folder.mkdir()
    assessment_prompt(store, revised, folder, backend)
    location = folder / 'packet' if backend == 'briefloop-native' else folder
    packet = json.loads((location / 'input.json').read_text())
    context = packet['revision_context']
    assert context['version_id'] == first['id']
    assert context['brief_hash'] == first['hash']
    assert context['assessment_id'] == previous['id']
    assert context['brief']['markdown'] == first['markdown']
    finding = context['findings'][0]
    assert finding['report_quote'] == 'Summary: generally available.'
    expected = packet['assessment_checks'][-1]
    assert expected['id'] == finding['check_id']
    assert expected['prior_assessment_id'] == previous['id']
    assert expected['prior_finding_index'] == 0
    assert packet['brief']['hash'] == revised['hash']


def test_explicit_check_conflict_is_visible_but_minor_findings_do_not_reject_score():
    value = {'brief_hash': 'hash', 'summary': 'Usable with a minor correction.',
             'overall': '达到要求', 'evidence': 3, 'coverage': 3, 'analysis': 3, 'expression': 3,
             'checks': [{'id': 'summary_consistency', 'status': 'passed', 'reason': 'Compared summary and body.'},
                        {'name': 'Old unlinked check', 'status': 'passed'}],
             'findings': [{'dimension': 'evidence', 'severity': 'minor', 'description': 'Summary retains an old error.',
                           'check_ids': ['summary_consistency']}]}
    result = Assessment.model_validate(value).model_dump()
    assert result['overall'] == '达到要求'
    assert result['checks'][0]['status'] == 'needs_attention'
    assert result['checks'][0]['model_status'] == 'passed'
    assert result['checks'][0]['finding_indices'] == [0]
    assert result['checks'][1] == value['checks'][1]
    assert value['checks'][0]['status'] == 'passed'  # The raw model artifact stays intact.
    legacy = Assessment.model_validate({**value, 'findings': []}).model_dump()
    assert legacy['checks'][0]['status'] == 'passed'


def test_missing_followups_are_saved_as_unchecked_without_another_turn(tmp_path):
    store, first, revised, previous = pair(tmp_path)
    job = store.enqueue('assess', {'version_id': revised['id'], 'agent_backend': 'codex'})

    class Runtime:
        calls = 0
        def execute(self, job, prompt, folder):
            self.calls += 1
            packet = json.loads((folder / 'input.json').read_text())
            # One requested check is answered; revision follow-up is omitted.
            (folder / 'assessment.json').write_text(dump(score(revised, checks=[{
                'id': packet['assessment_checks'][0]['id'], 'status': 'passed',
                'reason': 'Compared headings and figures.'}])))
            return {'status': 'complete'}

    runtime = Runtime(); worker = Worker(store, runtime)
    result = worker.assess(job)
    data = json.loads(store.one('assessments', result['assessment_id'])['data'])
    assert runtime.calls == 1
    assert data['overall'] == '达到要求'
    assert [check['status'] for check in data['checks']] == ['passed', 'not_checked', 'not_checked']
    assert data['checks'][-1]['prior_assessment_id'] == previous['id']
    assert data['checks'][-1]['prior_description'] == 'Summary omits the public beta condition.'
    assert len(store.rows('SELECT id FROM briefs WHERE run_id=?', (first['run_id'],))) == 2


def test_disputed_old_finding_is_not_silently_marked_resolved(tmp_path):
    store, first, revised, previous = pair(tmp_path)
    expected = [{'id': f"revision:{previous['id']}:0", 'name': '修订复核 · 1',
                 'prior_assessment_id': previous['id'], 'prior_finding_index': 0}]
    check = {'id': expected[0]['id'], 'status': 'disputed',
             'reason': 'The source explicitly permits this scope; prior finding was overbroad.'}
    saved = store.assess(revised['id'], score(revised, checks=[check]), expected_checks=expected)
    data = json.loads(saved['data'])
    assert data['checks'][0]['status'] == 'disputed'
    assert data['checks'][0]['reason'] == check['reason']
    # Prior assessment is a historical record, not overwritten by the new opinion.
    assert store.one('assessments', previous['id'])['data'] == previous['data']


@pytest.mark.parametrize('backend', ['codex', 'briefloop-native'])
def test_reviewer_uses_bound_context_and_missing_followup_is_not_a_retry(tmp_path, backend):
    from briefloop.review import run_review, get_review, accept_review, validate_applicable_review
    from review_checks import for_version
    store, first, revised, previous = pair(tmp_path)
    job = store.enqueue('assess', {'version_id': revised['id'], 'agent_backend': backend,
                                   'runtime': {'model': 'fake/model' if backend == 'briefloop-native' else 'fake'}})
    folder = store.root / 'jobs' / job['id']

    class Runtime:
        calls = 0
        def execute(self, stage, prompt, output_folder, **kwargs):
            self.calls += 1
            context = json.loads((output_folder / 'packet/assessment-context.json').read_text())
            assert context['revision_context']['assessment_id'] == previous['id']
            assert context['revision_context']['brief']['markdown'] == first['markdown']
            assert 'assessment-context.json' in prompt
            review = get_review(store, stage['review_id'])
            self.value = {'fingerprint': review['fingerprint'], 'version_id': revised['id'],
                          'status': 'complete', 'summary': 'Synthetic complete review.',
                          'coverage_scan_complete': True, 'requirement_checks': for_version(store, revised['id']),
                          'assessment': score(revised)}
            (output_folder / 'review.json').write_text(dump(self.value))

    runtime = Runtime()
    accepted = run_review(store, runtime, job, revised['id'], folder)
    assert runtime.calls == 1
    assert accepted['status'] == 'complete'  # New observations don't redefine the review gate.
    checks = accepted['result']['assessment']['checks']
    assert len(checks) == 3 and {check['status'] for check in checks} == {'not_checked'}
    saved = json.loads(store.one('assessments', 'assessment_' + accepted['id'])['data'])
    assert saved['checks'] == checks
    assert accept_review(store, accepted['id'], runtime.value)['result'] == accepted['result']
    validate_applicable_review(store, accepted['id'], revised['id'])
    # The old concerns are actual frozen review input, not an unbound side file.
    context_file = folder / 'packet/assessment-context.json'
    context_file.write_text(context_file.read_text() + '\n')
    with pytest.raises(ValueError, match='核查包文件已变化'):
        validate_applicable_review(store, accepted['id'], revised['id'])


def test_legacy_saved_review_score_can_be_read_without_rewriting_new_optional_keys(tmp_path):
    from briefloop.review import ReviewOutput, _save_assessment
    store, first, _, _ = pair(tmp_path)
    old = score(first, findings=[{'dimension': 'evidence', 'severity': 'minor',
                                 'description': 'An old issue.'}])
    # Populate the old defaults, then omit the newly introduced optional key.
    old = Assessment.model_validate(old).model_dump()
    old['findings'][0].pop('check_ids')
    original = dump(old)
    with store.tx() as connection:
        connection.execute('INSERT INTO assessments VALUES(?,?,?,?)',
                           ('assessment_legacy', first['id'], original, '2026'))
        result = ReviewOutput.model_validate({'fingerprint': 'old', 'version_id': first['id'],
                                             'status': 'complete', 'summary': 'Old review', 'assessment': old})
        _save_assessment(connection, 'legacy', result)
    assert store.one('assessments', 'assessment_legacy')['data'] == original
    result.assessment.summary = 'A materially different opinion.'
    with store.tx() as connection, pytest.raises(ValueError, match='不可覆盖'):
        _save_assessment(connection, 'legacy', result)
