"""Synthetic control-path tests; provider verdicts are not semantic accuracy evidence."""
import json
import threading

import pytest

from briefloop import jev, jev_checks
from briefloop.store import Conflict, Store, dump


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, '_key_path', lambda: tmp_path/'credentials'/'jev.key')
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    store = Store(tmp_path/'workspace')
    text = '# Synthetic project A\nStatus as of April\n| Status | Target |\n|---|---|\n| Under construction | up to 80 units |\nFor selected partners only.'
    source = store.add_source('Synthetic project A', text)
    run = store.create_run({'title': 'Synthetic brief', 'objective': 'Check project status',
                            'completion_mode': 'fast'}, [source['id']])
    quote = 'Project A completed 80 units for all partners.'
    brief = store.publish(run['id'], {'title': 'Synthetic brief', 'markdown': quote,
                                    'citations': [{'source_id': source['id'], 'report_quote': quote,
                                                   'excerpt': '| Under construction | up to 80 units |', 'locator': 'line 5'}]})
    return store, source, brief


def response(status='contradicted'):
    return {'model': 'jev-fixture', 'answers': {'support': {'type': 'choice', 'choice': status,
            'probabilities': {key: float(key == status) for key in jev.CRITERIA}}},
            'usage': {'input_tokens': 200, 'output_tokens': 10},
            'reasoning': 'This field must never be persisted.'}


def test_explicit_admission_freezes_context_and_reuses_the_same_job(tmp_path, monkeypatch):
    store, source, brief = fixture(tmp_path, monkeypatch)
    before = store.one('briefs', brief['id'])
    preview = jev_checks.view(store, brief['id'])
    assert preview['provider']['configured'] is False
    assert not store.rows("SELECT id FROM jobs WHERE kind='jev_check'")
    item = preview['items'][0]
    assert item['eligible'] and 'Status | Target' in item['evidence'][0]['source_context']
    assert 'selected partners' in item['evidence'][0]['source_context']
    with pytest.raises(ValueError, match='确认'):
        jev_checks.enqueue(store, brief['id'], preview['fingerprint'])
    jev.save_key('synthetic-key')
    with pytest.raises(Conflict):
        jev_checks.enqueue(store, brief['id'], 'old-fingerprint', allow_external=True)
    job = jev_checks.enqueue(store, brief['id'], preview['fingerprint'], allow_external=True)
    duplicate = jev_checks.enqueue(store, brief['id'], preview['fingerprint'], allow_external=True)
    assert job['id'] == duplicate['id']
    assert 'synthetic-key' not in job['payload']
    assert store.one('briefs', brief['id']) == before


def test_completed_observation_never_changes_assessment_review_or_report(tmp_path, monkeypatch):
    store, source, brief = fixture(tmp_path, monkeypatch)
    jev.save_key('synthetic-key')
    frozen = jev_checks.snapshot(store, brief['id'])
    job = jev_checks.enqueue(store, brief['id'], frozen['fingerprint'], allow_external=True)
    called = []
    def evaluate(item, model):
        called.append(item)
        return jev.parse_response(response())
    result = jev_checks.run(store, job, threading.Event(), evaluate=evaluate)
    assert result['items'][0]['status'] == 'contradicted'
    assert result['items'][0]['usage'] == {'input_tokens': 200, 'output_tokens': 10}
    assert 'reasoning' not in dump(result) and 'synthetic-key' not in dump(result)
    assert result['affects_release'] is False
    assert not store.rows('SELECT id FROM assessments') and not store.rows('SELECT id FROM reviews')
    assert store.one('briefs', brief['id'])['markdown'] == brief['markdown']
    # An explicit resume reuses recorded successes, not another provider request.
    jev_checks.run(store, store.one('jobs', job['id']), threading.Event(), evaluate=evaluate)
    assert len(called) == 1


def test_lost_response_is_visible_and_not_automatically_repeated(tmp_path, monkeypatch):
    store, _, brief = fixture(tmp_path, monkeypatch)
    jev.save_key('synthetic-key')
    frozen = jev_checks.snapshot(store, brief['id'])
    job = jev_checks.enqueue(store, brief['id'], frozen['fingerprint'], allow_external=True)
    def lost(*args):
        raise TimeoutError('secret-from-provider-error')
    with pytest.raises(ValueError, match='未自动重发'):
        jev_checks.run(store, job, threading.Event(), evaluate=lost)
    saved = store.one('jobs', job['id'])
    assert 'secret-from-provider-error' not in saved['result']
    result = jev_checks.run(store, saved, threading.Event(), evaluate=lambda *args: pytest.fail('must not send twice'))
    assert result['unchecked_count'] == 1
    assert result['items'][0]['status'] == 'not_checked'
    assert result['items'][0]['execution'] == 'uncertain'


def test_post_admission_changes_do_not_change_external_request(tmp_path, monkeypatch):
    store, source, brief = fixture(tmp_path, monkeypatch)
    jev.save_key('synthetic-key')
    frozen = jev_checks.snapshot(store, brief['id'])
    job = jev_checks.enqueue(store, brief['id'], frozen['fingerprint'], allow_external=True)
    original = store.source_text
    monkeypatch.setattr(store, 'source_text', lambda sid, **kw: original(sid, **kw)+'\nNew source publication.')
    assert jev_checks.view(store, brief['id'])['records'][0]['stale'] is True
    def evaluate(item, model):
        assert item['evidence'] == frozen['items'][0]['evidence']
        return jev.parse_response(response())
    jev_checks.run(store, job, threading.Event(), evaluate=evaluate)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(InterruptedError):
        jev_checks.run(store, job, cancelled, evaluate=lambda *args: pytest.fail('cancelled request'))


def test_one_missing_source_keeps_the_whole_claim_unchecked(tmp_path, monkeypatch):
    store, source, brief = fixture(tmp_path, monkeypatch)
    second = store.add_source('Synthetic second source', 'Other source context.')
    run = store.create_run({'title': 'Joint conclusion', 'objective': 'Both sources required'}, [source['id'], second['id']])
    brief = store.publish(run['id'], {'title': 'Joint conclusion', 'markdown': brief['markdown'],
        'citations': json.loads(brief['detail'])['citations'] +
        [{'source_id': second['id'], 'report_quote': brief['markdown'], 'excerpt': 'Not actually present.'}]})
    preview = jev_checks.snapshot(store, brief['id'])
    assert len(preview['items']) == 1 and preview['items'][0]['eligible'] is False
    with pytest.raises(ValueError, match='没有可预检'):
        jev_checks.enqueue(store, brief['id'], preview['fingerprint'], allow_external=True)


def test_provider_contract_preserves_probabilities_without_turning_them_into_truth():
    raw = response()
    result = jev.parse_response(raw)
    assert result['actual_model'] == 'jev-fixture' and 'reasoning' not in result
    raw['answers']['support']['probabilities']['unknown'] = float('nan')
    with pytest.raises(ValueError):
        jev.parse_response(raw)
    raw = response(); del raw['answers']['support']['probabilities']['unknown']
    with pytest.raises(ValueError):
        jev.parse_response(raw)
    raw = response(); raw['answers']['support']['choice'] = 'supported_for_scope'
    with pytest.raises(ValueError):
        jev.parse_response(raw)


def test_only_fixed_endpoint_receives_key_and_errors_never_echo_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, '_key_path', lambda: tmp_path/'jev.key')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-test-key')
    requests = []
    class Result:
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def read(self, limit):return dump(response()).encode()
    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            return Result()
    monkeypatch.setattr(jev.urllib.request, 'build_opener', lambda *args: Transport())
    item = {'statement': 'Synthetic claim', 'evidence': [{'title': 'Synthetic source', 'excerpt': 'Synthetic evidence'}]}
    assert jev.evaluate(item)['status'] == 'contradicted'
    request = requests[0]
    assert request.full_url == jev.ENDPOINT
    assert request.headers['Authorization'] == 'Bearer synthetic-test-key'
    assert 'synthetic-test-key' not in request.data.decode()
    assert set(json.loads(request.data)) == {'model', 'state', 'questions'}
    assert jev._NoRedirect().redirect_request(request, None, 302, '', {}, 'https://elsewhere.invalid') is None
