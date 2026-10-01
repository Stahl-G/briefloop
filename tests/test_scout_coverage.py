"""Committed work, real Native tools and host CLI joins; no paid/network work."""
import json
import sys
import threading

import pytest

from briefloop import research_plan, scout_coverage
from briefloop.chat_tools import workspace_action
from briefloop.cli import main
from briefloop.interactive_runtime import InteractiveRuntime
from briefloop.native_harness import NativeHarness
from briefloop.runtime import Worker
from briefloop.store import Store
from test_native_orchestrator import FlowEngine, setup


TASKS = [{'slot_id': f'scout-{n}', 'assignment': assignment} for n, assignment in enumerate(
    ('Read revenue', 'Read scope', 'Read competition', 'Read regulation'), 1)]


class CoverageEngine(FlowEngine):
    def __init__(self, *args, disposition=None):
        super().__init__(*args)
        self.disposition = disposition

    def next(self, sid):
        name, args = self.queues[sid][0]
        if name == 'save_plan':
            args['plan']['scout_tasks'] = TASKS
            if self.disposition == 'complete':
                self.queues[sid].insert(2, ('run_scouts', {'tasks': TASKS[2:]}))
        elif name == 'workspace_action' and args['request']['action'] == 'finish_research_round' and self.disposition == 'skip':
            args['request']['scout_outcomes'] = [
                {'slot_id': task['slot_id'], 'status': 'skipped', 'reason': 'No remaining time; this direction is uninspected'}
                for task in TASKS[2:]]
        elif name == 'submit_scout_result':
            args['gaps'] = []  # Both real Scout tools have already recorded valid evidence.
        super().next(sid)


@pytest.mark.parametrize('disposition', [None, 'skip', 'complete'])
def test_native_committed_four_tasks_cannot_silently_publish_two(tmp_path, disposition):
    store, source, run, job = setup(tmp_path)
    engine = CoverageEngine(store, run, source, disposition=disposition)
    harness = NativeHarness(store, engine)
    worker = Worker(store)
    worker.runtime = InteractiveRuntime(store, backends={'briefloop-native': harness})
    try:
        if disposition is None:
            with pytest.raises(RuntimeError):
                worker.generate(job, score=False)
            assert store.rows('SELECT id FROM briefs') == []
            failures = [p['error'] for method, p in engine.calls if method == 'tool_result' and not p.get('ok', True)]
            assert any('scout-3' in error and 'scout-4' in error for error in failures)
        else:
            worker.generate(job, score=False)
            assert len(store.rows('SELECT id FROM briefs')) == 1
            research = json.loads((worker.folder(job) / 'analyst' / 'packet' / 'research.json').read_text())
            assert research['gaps'] == []
            if disposition == 'skip':
                assert [gap['slot_id'] for gap in research['execution_gaps']] == ['scout-3', 'scout-4']
                assert all(gap['status'] == 'skipped' and 'uninspected' in gap['reason'] for gap in research['execution_gaps'])
            else:
                assert research['execution_gaps'] == []
        assert len([s for s in engine.sessions.values() if s['role'] == 'scout']) == (4 if disposition == 'complete' else 2)
    finally:
        harness.close()


class RecoverEngine(FlowEngine):
    failed = False

    def call(self, method, params, **kwargs):
        sid = params.get('session_id')
        if method == 'turn_start' and self.sessions[sid]['role'] == 'scout':
            self.calls.append((method, params))
            self.sessions[sid]['eid'] = params['execution_id']
            if not self.failed:
                self.failed = True
                self.sinks[params['execution_id']].put({'kind': 'end', 'status': 'failed', 'error': 'Synthetic transient failure'})
                return {}
            self.queues[sid] = [
                ('source_read', {'source_id': self.source['id']}),
                ('record_evidence', {'items': [{'id': 'revenue', 'source_id': self.source['id'],
                    'source_hash': self.source['hash'], 'locator': 'line 1', 'quote': '2025年收入1200万元。',
                    'facts': ['2025年收入1200万元。'], 'coverage_status': 'complete'}]}),
                ('submit_scout_result', {'gaps': ['Substantive question still unresolved']})]
            self.next(sid)
            return {}
        return super().call(method, params, **kwargs)

    def next(self, sid):
        name, _ = self.queues[sid][0]
        if name == 'save_plan':
            self.queues[sid].insert(2, ('run_scouts', {'tasks': TASKS[:2]}))
        super().next(sid)


def test_native_successful_retry_clears_only_execution_failure(tmp_path):
    store, source, run, job = setup(tmp_path)
    engine = RecoverEngine(store, run, source)
    harness = NativeHarness(store, engine)
    worker = Worker(store)
    worker.runtime = InteractiveRuntime(store, backends={'briefloop-native': harness})
    try:
        worker.generate(job, score=False)
        receipts = [json.loads(p['content'][0]['text']) for method, p in engine.calls
                    if method == 'tool_result' and p.get('request_id') == 'run_scouts' and p.get('ok')]
        assert sorted(task['status'] for task in receipts[0]['tasks']) == ['complete', 'failed']
        assert len(receipts[0]['execution_gaps']) == 1
        assert [task['status'] for task in receipts[1]['tasks']] == ['complete', 'complete']
        research = json.loads((worker.folder(job) / 'research.json').read_text())
        assert research['execution_gaps'] == []
        from briefloop.task_progress import summary
        assert not any('执行未完成' in item['label'] for item in summary(store, job['id'])['timeline'])
        assert research['gaps'] == ['Substantive question still unresolved']
        execution = scout_coverage.view(store, run['id'])['scout_execution']
        assert sum(any(event['status'] == 'failed' for event in task['history']) for task in execution) == 1
        # Two original sessions and only one additional turn: no rerun of the completed slot.
        scout_sids = {sid for sid, session in engine.sessions.items() if session['role'] == 'scout'}
        assert len(scout_sids) == 2
        assert len([p for method, p in engine.calls if method == 'turn_start' and p['session_id'] in scout_sids]) == 3
    finally:
        harness.close()


@pytest.mark.parametrize('mode', ['missing_manifest', 'missing_results', 'mismatched_plan', 'skip', 'zero_findings', 'material_only'])
def test_host_cli_join_and_worker_share_coverage_boundary(tmp_path, monkeypatch, mode):
    store = Store(tmp_path)
    store.update_settings({'model': 'synthetic', 'model_selection_required': False})
    source = store.add_source('Synthetic filing', 'Known local fact.')
    run = store.create_run({'title': 'Fixture', 'objective': 'Check material', 'allow_web': False, 'fact_check': False},
                           [source['id']], research_protocol='quality_v1')
    job = store.enqueue('generate', {'run_id': run['id']})
    assert json.loads(job['payload'])['scout_coverage_version'] == 1

    class Host:
        cancelled = threading.Event()
        def execute(self, job, prompt, folder, on_tick, **kwargs):
            tasks = [{**task, 'result_file': str(folder / task['slot_id'] / 'result.json')} for task in TASKS]
            (folder / 'plan.json').write_text(json.dumps({'scout_tasks': [] if mode == 'material_only' else tasks[:2] if mode == 'mismatched_plan' else tasks}))
            if mode != 'missing_manifest':
                request = {'action': 'set_scout_tasks', 'run_id': run['id'], 'scout_tasks': [] if mode == 'material_only' else tasks}
                request_file = folder / 'request.json'
                request_file.write_text(json.dumps(request))
                monkeypatch.setattr(sys, 'argv', ['briefloop', 'tool', '--workspace', str(store.root),
                    'workspace-action', '--request', str(request_file)])
                main()
            if mode not in ('missing_manifest', 'material_only'):
                supplied = tasks if mode == 'zero_findings' else tasks[:2]
                for task in supplied:
                    path = tmp_path / task['result_file']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({'sources': [], 'gaps': ['No matching evidence in local material']}))
                monkeypatch.setattr(sys, 'argv', ['briefloop', 'tool', '--workspace', str(store.root),
                    'join-scouts', '--run', run['id'], '--round', research_plan.frozen(store, run['id'])['current_round_id'],
                    '--files', *[task['result_file'] for task in supplied]])
                main()
            (folder / 'draft.json').write_text(json.dumps({'title': 'Fixture', 'markdown': 'Known local fact.'}))
            on_tick()
            assert store.rows('SELECT id FROM briefs') == []  # Direct host file publication cannot bypass closeout.
            request = {'action': 'finish_research_round', 'run_id': run['id']}
            if mode == 'skip':
                request['scout_outcomes'] = [{'slot_id': task['slot_id'], 'status': 'skipped', 'reason': 'Explicit scope reduction'} for task in tasks[2:]]
            workspace_action(store, request)
            return {'status': 'complete'}

    worker = Worker(store, Host())
    monkeypatch.setattr(worker, 'complete_draft_checks', lambda job, brief, folder, **kwargs: {'version_id': brief['id']})
    if mode.startswith('missing') or mode == 'mismatched_plan':
        with pytest.raises(research_plan.AdmissionError) as error:
            worker.generate(job, score=False)
        assert error.value.code == ('scout_plan_missing' if mode == 'missing_manifest' else 'scout_plan_mismatch' if mode == 'mismatched_plan' else 'scout_coverage_incomplete')
        assert store.rows('SELECT id FROM briefs') == []
    else:
        assert worker.generate(job, score=False)['version_id']
        gaps = scout_coverage.view(store, run['id'])['execution_gaps']
        assert len(gaps) == (2 if mode == 'skip' else 0)
        if mode == 'skip':
            from briefloop.task_progress import summary
            assert sum('已说明跳过' in item['label'] for item in summary(store, job['id'])['timeline']) == 2


def test_capacity_is_not_commitment_and_old_runs_remain_compatible(tmp_path):
    store = Store(tmp_path)
    store.update_settings({'model': 'synthetic', 'model_selection_required': False})
    run = store.create_run({'title': 'Fixture', 'objective': 'Check sources', 'allow_web': True, 'research_tier': 'deep'}, [], research_protocol='quality_v1')
    # Model an existing persisted run that predates deterministic admission metadata.
    with store.tx() as connection:
        connection.execute('DELETE FROM meta WHERE key=?', ('scout_coverage_version:' + run['id'],))
    job = store.enqueue('generate', {'run_id': run['id'], 'scout_coverage_version': 1})
    assert 'scout_coverage_version' not in json.loads(job['payload'])
    plan = research_plan.freeze(store, run['id'])
    assert len(plan['rounds'][plan['current_round_id']]['tasks']) == plan['structure']['breadth']
    assert plan['structure']['breadth'] > 1
    assert not scout_coverage.required(store, run['id'])
    old_plan = store.root / 'jobs' / job['id'] / 'plan.json'
    old_plan.parent.mkdir(parents=True)
    old_plan.write_text(json.dumps({'scout_tasks': [{'slot_id': 'old-scout', 'assignment': 'Legacy narrative shape'}]}))
    research_plan.finish_round(store, run['id'])
    assert research_plan.require_writing_closeout(store, run['id'])


def test_manifest_replay_capacity_skips_and_late_results(tmp_path):
    from briefloop.scout_tools import join_scouts
    store, source, run, job = setup(tmp_path, research_tier='deep')
    plan = research_plan.freeze(store, run['id'], structure={'breadth': 4, 'depth': 3})
    tasks = [{**task, 'result_file': str(store.root / 'jobs' / job['id'] / task['slot_id'] / 'result.json')} for task in TASKS[:2]]
    # Two commitments, four allocated slots and three possible rounds.
    first = scout_coverage.declare(store, run['id'], tasks)
    assert scout_coverage.declare(store, run['id'], tasks) == first
    with pytest.raises(ValueError, match='不能省略'):
        scout_coverage.declare(store, run['id'], tasks[:1])
    with pytest.raises(ValueError, match='只能为'):
        scout_coverage.update(store, run['id'], [{'slot_id': 'scout-1', 'status': 'complete'}])
    with pytest.raises(research_plan.AdmissionError, match='未交接'):
        research_plan.finish_round(store, run['id'], early_stop_reason='No additional search needed')
    outcomes = [{'slot_id': task['slot_id'], 'status': 'skipped', 'reason': 'Explicitly uninspected scope'} for task in tasks]
    closed = research_plan.finish_round(store, run['id'], scout_outcomes=outcomes)
    assert len(closed['execution_gaps']) == 2
    assert research_plan.require_writing_closeout(store, run['id'])['round_index'] == 1
    from pathlib import Path
    path = Path(tasks[0]['result_file'])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'sources': [], 'gaps': ['No matching evidence']}))
    join_scouts(store, [path], run_id=run['id'])
    assert all(task['status'] == 'skipped' for task in scout_coverage.view(store, run['id'])['scout_execution'])
    replay = research_plan.finish_round(store, run['id'])
    assert replay['idempotent']
    research_plan.begin_round(store, run['id'])
    with pytest.raises(ValueError, match='本任务、本轮|同一 Scout 结果路径'):
        scout_coverage.declare(store, run['id'], tasks)


def test_foreign_run_and_preexisting_results_do_not_complete_new_tasks(tmp_path):
    from pathlib import Path
    from briefloop.scout_tools import join_scouts
    store, source, old_run, old_job = setup(tmp_path)
    research_plan.freeze(store, old_run['id'])
    old_path = store.root / 'jobs' / old_job['id'] / 'scout-1' / 'result.json'
    old_task = {'slot_id': 'scout-1', 'assignment': 'Read old subject', 'result_file': str(old_path)}
    scout_coverage.declare(store, old_run['id'], [old_task])
    old_path.parent.mkdir(parents=True)
    result = {'sources': [], 'gaps': ['No relevant evidence in this task']}
    old_path.write_text(json.dumps(result))
    join_scouts(store, [old_path], run_id=old_run['id'])
    research_plan.finish_round(store, old_run['id'])
    run = store.create_run({'title': 'New scope', 'objective': 'Read a different subject', 'allow_web': False, 'fact_check': False},
                           [source['id']], research_protocol='quality_v1')
    job = store.enqueue('generate', {'run_id': run['id']})
    research_plan.freeze(store, run['id'])
    task = {**old_task, 'assignment': 'Read new subject'}
    with pytest.raises(ValueError, match='本任务、本轮'):
        scout_coverage.declare(store, run['id'], [task])
    # Reusing allowed source material is fine, but an old artifact cannot count
    # as newly executed work even after copying it to the new canonical path.
    current_path = store.root / 'jobs' / job['id'] / 'scout-1' / 'result.json'
    task['result_file'] = str(current_path)
    current_path.parent.mkdir(parents=True)
    current_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match='已有结果'):
        scout_coverage.declare(store, run['id'], [task])
    current_path.unlink()
    scout_coverage.declare(store, run['id'], [task])
    join_scouts(store, [old_path], run_id=run['id'])  # Evidence reuse, never execution credit.
    assert scout_coverage.view(store, run['id'])['scout_execution'][0]['status'] == 'planned'
    current_path.write_text(json.dumps(result))
    join_scouts(store, [current_path], run_id=run['id'])
    research_plan.finish_round(store, run['id'])
    assert scout_coverage.view(store, run['id'])['execution_gaps'] == []


def test_closed_round_join_cannot_credit_active_round_with_slots_override(tmp_path):
    from pathlib import Path
    from briefloop.scout_tools import join_scouts
    store, source, run, job = setup(tmp_path)
    first = research_plan.freeze(store, run['id'])['current_round_id']
    scout_coverage.declare(store, run['id'], [])
    research_plan.finish_round(store, run['id'])
    second = research_plan.begin_round(store, run['id'])['round_id']
    path = store.root / 'jobs' / job['id'] / 'round-2' / 'scout-1' / 'result.json'
    task = {'slot_id': 'scout-1', 'assignment': 'Second round direction', 'result_file': str(path)}
    scout_coverage.declare(store, run['id'], [task])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'sources': [], 'gaps': ['No matching evidence']}))
    with pytest.raises(ValueError, match='槽位'):
        join_scouts(store, [path], run_id=run['id'], round_id=first, slots=[path])
    assert scout_coverage.view(store, run['id'])['scout_execution'][0]['status'] == 'planned'
    # The lower-level completion boundary is independently round-bound too.
    scout_coverage.complete(store, run['id'], [path], round_id=first)
    assert scout_coverage.view(store, run['id'])['scout_execution'][0]['status'] == 'planned'
    join_scouts(store, [path], run_id=run['id'], round_id=second)
    research_plan.finish_round(store, run['id'])
    assert scout_coverage.view(store, run['id'])['execution_gaps'] == []
