import json
import threading

import pytest

from briefloop.store import Store, dump, now
from briefloop.runtime import Worker
from briefloop.review import build_packet


def passing(**changes):
    return {'status': 'complete', 'overall': '达到要求', 'summary': 'Checked',
            'evidence': 4, 'coverage': 5, 'analysis': 4, 'expression': 4, **changes}


def test_minor_confirmed_defects_override_passing_overall():
    from briefloop.revision_policy import revision_reasons
    findings = [{'id': kind, 'status': 'open', 'data': {'kind': kind, 'severity': 'minor'}}
                for kind in ['insufficient_evidence', 'missing_binding', 'missing_requirement']]
    reasons = revision_reasons(passing(), findings)
    assert {r['finding_id'] for r in reasons if r['type'] == 'review_finding'} == {f['id'] for f in findings}
    assert revision_reasons(None, findings)  # A missing score cannot hide confirmed review defects.
    assert not revision_reasons(passing(), [{**f, 'status': 'resolved'} for f in findings])


def test_explicit_writing_violation_and_optional_polish_are_distinct():
    from briefloop.revision_policy import revision_reasons
    violation = {'id': 'explicit-style', 'status': 'open', 'data': {
        'kind': 'expression', 'severity': 'minor', 'requirement_ids': ['req-no-disclaimer']}}
    assert revision_reasons(passing(), [violation])
    optional = {'id': 'polish', 'status': 'open', 'data': {'kind': 'expression', 'severity': 'minor'}}
    suggestion = {'dimension': 'expression', 'severity': 'minor', 'description': 'Optional shortening'}
    assert not revision_reasons(passing(), [optional])
    assert not revision_reasons(passing(overall='建议修改', findings=[suggestion]), [optional])
    assert revision_reasons(passing(expression=2), [optional])
    assert revision_reasons(passing(overall='建议修改'), [])  # Older unstructured assessments still work.


def test_missing_required_content_in_checks_does_not_need_a_low_score():
    from briefloop.revision_policy import revision_reasons
    review = {'requirement_checks': [{'requirement_id': 'req-question', 'status': 'missing', 'reason': 'Required answer absent.'}],
              'clause_checks': [{'clause_id': 'clause-format', 'status': 'partial', 'reason': 'Second paragraph violates explicit format.'}]}
    reasons = revision_reasons(passing(), [], review)
    assert {r['type'] for r in reasons} == {'requirement_check', 'clause_check'}
    assert [r['reason'] for r in reasons] == ['Required answer absent.', 'Second paragraph violates explicit format.']
    assert not revision_reasons(passing(), [], {'requirement_checks': [{'requirement_id': 'manual', 'status': 'manual'}]})


@pytest.mark.parametrize('has_score', [True, False])
def test_worker_revises_confirmed_minor_defect_once_and_rechecks(tmp_path, has_score):
    store = Store(tmp_path)
    source = store.add_source('Synthetic source', 'Two issues await supplier documents.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Only state supported facts.'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'Two issues await documents, not stalled.'})
    job = store.enqueue('generate', {'run_id': run['id'], 'auto_revision': True})
    if has_score:
        store.assess(brief['id'], {'brief_hash': brief['hash'], **passing()})
    # Seed an admitted review finding; the real review admission/binding path is
    # independently covered by test_review.py. Never writes to a user workspace.
    packet_folder = store.root / 'synthetic-review'
    fingerprint, files = build_packet(store, brief['id'], packet_folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           ('review_case', brief['id'], job['id'], fingerprint, 'complete', dump({'packet_path': str((packet_folder / 'packet').relative_to(store.root)), 'files': files}),
                            dump({'findings': []}), now(), now()))
        connection.execute('INSERT INTO review_findings VALUES(?,?,?,?,?,?)',
                           ('finding_case', 'review_case', brief['id'], 'open', dump({
                               'kind': 'insufficient_evidence', 'severity': 'minor',
                               'description': 'The source does not state that work was not stalled.',
                               'evidence': 'Source only states the wait for documents.'}), now()))

    class Runtime:
        cancelled = threading.Event()
        def __init__(self): self.calls = []
        def execute(self, job, prompt, folder, **kwargs):
            self.calls.append(str(folder.name))
            (folder / 'draft.json').write_text(dump({'title': 'Synthetic', 'markdown': 'Two issues await supplier documents.'}))
            (folder / 'responses.json').write_text(dump([{
                'finding_id': 'finding_case', 'action': 'removed', 'reason': 'Removed unsupported characterization.'}]))
            return {}

    runtime = Runtime()
    worker = Worker(store, runtime)
    checked = []
    def recheck(job, revised, folder, backend):
        checked.append(revised['id'])
        return store.assess(revised['id'], {'brief_hash': revised['hash'], **passing()})
    worker.assess_version = recheck
    result = worker.auto_revise(job, brief, worker.folder(job))
    assert result['revision_status'] == 'complete'
    assert store.one('briefs', brief['id'])['markdown'] == 'Two issues await documents, not stalled.'
    assert len(runtime.calls) == len(checked) == 1
    assert store.one('briefs', result['version_id'])['parent_id'] == brief['id']
    worker.auto_revise(job, brief, worker.folder(job))
    assert len(runtime.calls) == len(checked) == 1
    assert len(store.rows('SELECT id FROM briefs WHERE run_id=?', (run['id'],))) == 2


def test_stale_packet_and_its_score_cannot_trigger(tmp_path):
    from briefloop.review import review_status
    from briefloop.revision_policy import applicable_inputs, revision_reasons
    store = Store(tmp_path)
    source = store.add_source('Synthetic source', 'Only the original fact.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'State the fact.'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'An unsupported fact.'})
    folder = store.root / 'review-packet'
    fingerprint, files = build_packet(store, brief['id'], folder)
    with store.tx() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',
                           ('review_old', brief['id'], None, fingerprint, 'complete',
                            dump({'packet_path': str((folder / 'packet').relative_to(store.root)), 'files': files}),
                            dump({'findings': []}), now(), now()))
        connection.execute('INSERT INTO review_findings VALUES(?,?,?,?,?,?)',
                           ('finding_old', 'review_old', brief['id'], 'open',
                            dump({'kind': 'insufficient_evidence', 'severity': 'minor'}), now()))
    score = {'id': 'assessment_review_old', 'data': dump(passing(overall='建议修改'))}
    inputs = applicable_inputs(store, review_status(store, brief['id']), score)
    assert revision_reasons(*inputs)
    # A changed packet is invalid input, even though its DB finding still says open.
    (folder / 'packet/index.json').write_text('{}')
    assert not revision_reasons(*applicable_inputs(store, review_status(store, brief['id']), score))
