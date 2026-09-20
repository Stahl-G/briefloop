"""Unsupported Reviewer combinations are explained before any model call (#726)."""
import http.client
import json
import threading

import pytest

from briefloop.review_capability import CODE, ReviewBackendUnsupported, restricted_review, summary
from briefloop.store import Store

pytestmark = pytest.mark.real_review_capabilities


def _source(store):
    return store.add_source('Synthetic', 'Revenue was USD 12 million.')


def _web(**extra):
    return {'title': 'T', 'objective': 'o', 'allow_web': True, **extra}


def _backend(store, name):
    store.set_meta('settings', {**store.settings(), 'agent_backend': name})


def test_one_declaration_and_unknown_backends_never_claim_the_reviewer():
    from briefloop.backends import BRIDGE_BACKENDS
    assert restricted_review('opencode') is True
    assert restricted_review('codex') is False
    assert not any(restricted_review(name) for name in BRIDGE_BACKENDS)
    with pytest.raises(ValueError):
        restricted_review('made-up-host')
    assert summary()['restricted_review'] == [{'id': 'opencode', 'label': 'Opencode CLI'}, {'id':'briefloop-native', 'label':'BriefLoop 内置引擎'}]


def test_a_separately_chosen_reviewer_unblocks_a_main_chain_without_one(tmp_path):
    from pydantic import ValidationError
    from briefloop.models import Settings
    from briefloop.release import eligibility
    from briefloop.review import enqueue_review
    store = Store(tmp_path)
    source = _source(store)
    store.set_meta('settings', {**store.settings(), 'model_selection_required': False, 'company_context_enabled': False})
    with pytest.raises(ReviewBackendUnsupported) as refused:
        store.create_run(_web(fact_check=True), [source['id']])
    assert '独立审阅执行后端' in str(refused.value)

    reviewer = {'backend': 'opencode', 'model': 'opencode-go/deepseek-v4.1-flash', 'model_variant': 'high'}
    store.set_meta('settings', {**store.settings(), 'review_runtime': reviewer})
    run = store.create_run(_web(fact_check=True), [source['id']])
    job = store.enqueue('generate', {'run_id': run['id']})
    payload = json.loads(job['payload'])
    assert payload['agent_backend'] == 'codex' and payload['review_runtime'] == reviewer

    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
    assert not [b for b in eligibility(store, brief['id'])['blockers'] if b['code'] == CODE]
    # The route frozen with the parent job wins over later settings.
    store.set_meta('settings', {**store.settings(), 'review_runtime': None})
    review = json.loads(enqueue_review(store, brief['id'], payload={**payload, 'parent_job_id': job['id']})['payload'])
    assert review['agent_backend'] == 'opencode'
    assert review['runtime'] == {'model': 'opencode-go/deepseek-v4.1-flash', 'model_variant': 'high'}
    assert review['role_models']['evaluator'] == review['runtime']
    assert 'review_runtime' not in review
    # Following the main chain again: no route, so formal delivery says why.
    assert [b for b in eligibility(store, brief['id'])['blockers'] if b['code'] == CODE]
    with pytest.raises(ReviewBackendUnsupported):
        enqueue_review(store, brief['id'])

    for bad in ({'backend': 'codex', 'model': 'gpt-5.6-luna'}, {'backend': 'opencode', 'model': 'no-provider'},
                {'backend': 'briefloop-native', 'model': ' '}):
        with pytest.raises(ValidationError):
            Settings.model_validate({'review_runtime': bad})


def test_fact_check_is_refused_before_the_run_exists_and_ordinary_work_continues(tmp_path):
    store = Store(tmp_path)
    source = _source(store)
    with pytest.raises(ReviewBackendUnsupported) as refused:
        store.create_run(_web(fact_check=True), [source['id']])
    assert refused.value.code == CODE and 'Opencode CLI' in str(refused.value)
    assert store.rows('SELECT * FROM runs') == []
    # Ordinary and internal reports on the same backend still start.
    store.set_meta('settings', {**store.settings(), 'company_context_enabled': False})
    assert store.create_run(_web(), [source['id']])
    assert store.create_run(_web(writing_mode='internal_report'), [source['id']])
    # The workspace default is resolved first, so it cannot slip past the check.
    store.set_meta('settings', {**store.settings(), 'fact_checker': True})
    with pytest.raises(ReviewBackendUnsupported):
        store.create_run(_web(), [source['id']])
    _backend(store, 'opencode')
    assert json.loads(store.create_run(_web(), [source['id']])['requirements'])['fact_check'] is True


def test_web_generate_returns_the_structured_code_and_creates_nothing(tmp_path):
    from briefloop.server import make_server, _close_service
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f'127.0.0.1:{server.server_port}'
    try:
        source = _source(server.store)
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', '/api/session', headers={'Host': authority})
        token = json.loads(connection.getresponse().read())['token']
        connection.request('POST', '/api/generate', body=json.dumps({'requirements': _web(fact_check=True), 'source_ids': [source['id']]}),
                           headers={'Host': authority, 'X-BriefLoop-Token': token, 'Origin': 'http://' + authority})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 400 and payload['code'] == CODE
        assert server.store.rows('SELECT * FROM runs') == [] and server.store.rows('SELECT * FROM jobs') == []
        assert server.store.snapshot()['review_capability'] == summary()
    finally:
        server.shutdown()
        thread.join()
        _close_service(server)


def test_agent_generate_checks_the_backend_it_will_actually_use(tmp_path):
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path)
    source = _source(store)
    _backend(store, 'opencode')
    request = {'action': 'generate', 'requirements': _web(fact_check=True), 'source_ids': [source['id']]}
    with pytest.raises(ReviewBackendUnsupported):
        workspace_action(store, {**request, 'runtime': {'agent_backend': 'codex', 'model': 'gpt-5.6-luna'}})
    assert store.rows('SELECT * FROM runs') == []
    queued = workspace_action(store, {**request, 'runtime': {'agent_backend': 'opencode', 'model': 'synthetic/model'}})
    assert json.loads(store.one('jobs', queued['job_id'])['payload'])['agent_backend'] == 'opencode'


def test_queue_refuses_reviews_and_fact_checked_work_on_unsupported_backends(tmp_path):
    store = Store(tmp_path)
    source = _source(store)
    _backend(store, 'opencode')
    store.set_meta('settings', {**store.settings(), 'model': 'synthetic/model', 'model_selection_required': False})
    checked = store.create_run(_web(fact_check=True), [source['id']])
    brief = store.publish(checked['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
    codex = {'agent_backend': 'codex', 'runtime': {'model': 'gpt-5.6-luna'}}
    for kind, payload in (('review', {'version_id': brief['id']}), ('assess', {'version_id': brief['id']}),
                          ('generate', {'run_id': checked['id']}), ('fact_check', {'run_id': checked['id']})):
        with pytest.raises(ReviewBackendUnsupported):
            store.enqueue(kind, {**payload, **codex})
    assert store.rows('SELECT * FROM jobs') == []
    # A learning trial never runs the Reviewer, and ordinary runs need none.
    assert store.enqueue('generate', {'run_id': checked['id'], 'single_evaluation': False, **codex})
    plain = store.create_run(_web(), [source['id']])
    assert store.enqueue('generate', {'run_id': plain['id'], **codex})
    assert store.enqueue('review', {'version_id': brief['id']})


class RecordingRuntime:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.cancelled = threading.Event()

    def execute(self, job, prompt, folder, on_tick=lambda: None, **kwargs):
        self.calls.append((folder.name, bool(job.get('readonly_output'))))
        if folder.name in ('evaluation', 'revision-evaluation'):
            pack = json.loads((folder / 'input.json').read_text())
            (folder / 'assessment.json').write_text(json.dumps({'brief_hash': pack['brief']['hash'], 'summary': 'ordinary', 'overall': '建议修改',
                                                                'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4}))
        return {'returncode': 0}


def test_a_legacy_queued_fact_check_stops_before_any_model_turn(tmp_path):
    from briefloop.runtime import Worker
    from briefloop.store import dump, now
    store = Store(tmp_path)
    source = _source(store)
    _backend(store, 'opencode')
    run = store.create_run(_web(fact_check=True), [source['id']])
    job = store.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'opencode', 'runtime': {'model': 'synthetic/model'}})
    # Simulate a job frozen before the check existed, under a backend without the Reviewer.
    payload = {**json.loads(job['payload']), 'agent_backend': 'codex', 'runtime': {'model': 'gpt-5.6-luna'}}
    with store.tx() as c:
        c.execute('UPDATE jobs SET payload=?,updated=? WHERE id=?', (dump(payload), now(), job['id']))
    runtime = RecordingRuntime(store)
    with pytest.raises(ReviewBackendUnsupported):
        Worker(store, runtime).generate(store.one('jobs', job['id']))
    assert runtime.calls == []


def test_internal_report_without_the_reviewer_is_scored_as_ordinary_assessment(tmp_path):
    from briefloop.runtime import Worker, stage_job
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'company_context_enabled': False})
    source = _source(store)
    run = store.create_run(_web(writing_mode='internal_report'), [source['id']])
    job = store.enqueue('generate', {'run_id': run['id'], 'runtime': {'model': 'gpt-5.6-luna'}})
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'}, version_id='brief_' + job['id'][4:])
    runtime = RecordingRuntime(store)
    worker = Worker(store, runtime)
    folder = worker.folder(job) / 'evaluation'
    folder.mkdir(parents=True)
    worker.assess_version(stage_job(store, job, 'evaluator', mode='single'), brief, folder, 'codex')
    assert runtime.calls == [('evaluation', False)]
    assert store.rows("SELECT * FROM jobs WHERE kind='review'") == [] and store.rows('SELECT * FROM reviews') == []
    data = json.loads(store.rows('SELECT data FROM assessments WHERE version_id=?', (brief['id'],))[0]['data'])
    assert data['basis'] == 'assessment_without_review'
    # Scoring does not open delivery, and the gate says what would.
    from briefloop.release import eligibility
    blockers = [b['code'] for b in eligibility(store, brief['id'])['blockers']]
    assert blockers == ['review_missing', CODE]
    _backend(store, 'opencode')
    assert [b['code'] for b in eligibility(store, brief['id'])['blockers']] == ['review_missing']


def test_the_model_cannot_label_its_own_assessment_and_the_basis_is_closed(tmp_path):
    store = Store(tmp_path)
    source = _source(store)
    run = store.create_run(_web(), [source['id']])
    brief = store.publish(run['id'], {'title': 'T', 'markdown': 'Revenue was USD 12 million.'})
    grades = {'brief_hash': brief['hash'], 'summary': 's', 'overall': '达到要求', 'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4}
    with pytest.raises(ValueError):
        store.assess(brief['id'], {**grades, 'basis': 'independent_review'})
    with pytest.raises(ValueError):
        store.assess(brief['id'], grades, basis='independent_review')
    assert 'basis' not in json.loads(store.assess(brief['id'], grades)['data'])


def test_schedules_and_the_runtime_gate_use_the_same_declaration(tmp_path):
    from briefloop import schedules
    from briefloop.interactive_runtime import InteractiveRuntime
    store = Store(tmp_path)
    source = _source(store)
    body = {'name': 'Weekly', 'config': {'frequency': 'daily', 'start': '2026-09-16T09:00', 'timezone': 'UTC',
                                         'requirements': _web(fact_check=True), 'source_ids': [source['id']]}}
    with pytest.raises(ReviewBackendUnsupported):
        schedules.validate(store, body)

    class Harness:
        def __getattr__(self, name):
            raise AssertionError('the host must not be reached: ' + name)
    runtime = InteractiveRuntime(store, backends={'codex': Harness()})
    folder = store.root / 'jobs' / 'review-gate'
    folder.mkdir(parents=True)
    job = {'id': 'job_review_gate', 'kind': 'review', 'readonly_output': 'review.json',
           'payload': json.dumps({'agent_backend': 'codex', 'runtime': {'model': 'gpt-5.6-luna'}})}
    with pytest.raises(ReviewBackendUnsupported):
        runtime.execute(job, 'prompt', folder)


def test_a_long_internal_report_starts_no_checkpoint_review_without_the_reviewer(tmp_path, monkeypatch):
    """The 180s checkpoint must use the same capability check as final scoring."""
    import threading
    from briefloop import runtime as runtime_module
    from briefloop.runtime import Worker
    store = Store(tmp_path)
    store.set_meta('settings', {**store.settings(), 'company_context_enabled': False, 'auto_learn': False})
    source = _source(store)
    run = store.create_run({'title': '内部周报', 'objective': 'o', 'allow_web': False, 'writing_mode': 'internal_report'}, [source['id']])
    # The reader contract is a separate stage; this test is about the checkpoint.
    job = store.enqueue('generate', {'run_id': run['id'], 'reader_contract_required': False, 'runtime': {'model': 'gpt-5.6-luna'}})
    store.update_job(job['id'], 'running')
    real, offset = runtime_module.time.monotonic, [0.0]
    monkeypatch.setattr(runtime_module.time, 'monotonic', lambda: real() + offset[0])

    class Runtime:
        def __init__(self):
            self.calls = []
            self.cancelled = threading.Event()

        def cancel(self):
            self.cancelled.set()

        def execute(self, staged, prompt, folder, on_tick=lambda: None, **kwargs):
            self.calls.append(folder.name)
            if staged.get('runtime_role') == 'evaluator':
                pack = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
                (folder / 'assessment.json').write_text(json.dumps({'brief_hash': pack['brief']['hash'], 'summary': '普通评分',
                                                                    'overall': '建议修改', 'evidence': 4, 'coverage': 4, 'analysis': 4, 'expression': 4}), encoding='utf-8')
                return {'returncode': 0}
            (folder / 'draft.json').write_text(json.dumps({'title': '内部周报', 'markdown': '本周交付三项。'}), encoding='utf-8')
            offset[0] = 200  # the writing turn has now run past the checkpoint threshold
            on_tick()
            return {'returncode': 0}

    runtime = Runtime()
    worker = Worker(store, runtime)
    worker.thread.start()
    try:
        result = worker.generate(store.one('jobs', job['id']))
    finally:
        worker.close()
    assert store.rows("SELECT * FROM jobs WHERE kind='review'") == [] and store.rows('SELECT * FROM reviews') == []
    version = result['version_id']
    data = json.loads(store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1', (version,))[0]['data'])
    assert data['basis'] == 'assessment_without_review'
    from briefloop.release import eligibility
    assert [b['code'] for b in eligibility(store, version)['blockers']] == ['review_missing', CODE]
