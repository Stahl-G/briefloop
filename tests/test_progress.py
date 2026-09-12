"""Progress projection must follow the current evaluation directory layout."""
import json
from briefloop.progress import ProgressTracker
from briefloop.store import Store


def test_progress_reports_the_evaluation_assessment(tmp_path):
    store=Store(tmp_path/'workspace')
    job=store.enqueue('generate',{})
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    (folder/'events.jsonl').write_text('')
    tracker=ProgressTracker(store,job['id'],folder)
    tracker.update()
    (folder/'evaluation').mkdir()
    (folder/'evaluation'/'assessment.json').write_text('{}')
    tracker.update()
    row=store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1",(job['id'],))
    assert json.loads(row[0]['data'])['stage']=='评分已返回，正在保存结果'


def test_revision_and_readonly_review_do_not_restart_intake_progress(tmp_path):
    store = Store(tmp_path / 'workspace')
    source = store.add_source('Source', 'Saved original')
    run = store.create_run({'title': 'Report', 'objective': 'Explain'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Report', 'markdown': 'Saved original'})
    job = store.enqueue('generate', {'run_id': run['id']})
    revision = store.root / 'jobs' / job['id'] / 'revision'; revision.mkdir(parents=True)
    (revision / 'input.json').write_text(json.dumps({'brief': brief}))
    tracker = ProgressTracker(store, job['id'], revision, context=job)
    tracker.update()
    row = json.loads(store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1", (job['id'],))[0]['data'])
    assert row['stage'] == '正在按审阅意见修订稿件' and row['draft_ready']
    assert [stage['id'] for stage in row['stages']] == ['revision']
    review_job = store.enqueue('review', {'version_id': brief['id']})
    review = store.root / 'jobs' / review_job['id']; review.mkdir(parents=True)
    tracker = ProgressTracker(store, review_job['id'], review,
                              context={**review_job, 'readonly_output': 'review.json', 'runtime_role': 'evaluator'})
    tracker.update()
    row = json.loads(store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1", (review_job['id'],))[0]['data'])
    assert row['stage'] == '正在独立复核稿件与原件' and row['draft_ready']
    assert [stage['id'] for stage in row['stages']] == ['review']


def test_learning_roles_keep_their_actual_stage_without_a_new_report(tmp_path):
    from briefloop.runtime import stage_job
    store = Store(tmp_path / 'workspace')
    job = store.enqueue('learn', {'k': 1})
    for role, label in [('maintainer', '整理反馈经验'), ('proposer', '提出技能改进')]:
        folder = store.root / 'jobs' / job['id'] / f'1-{role}-req-actual'
        folder.mkdir(parents=True)
        tracker = ProgressTracker(store, job['id'], folder, context=stage_job(store, job, role))
        tracker.update()
        row = json.loads(store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1", (job['id'],))[0]['data'])
        assert row['stage'] == f'{role.title()} 正在{label}'
        assert row['stages'] == [{'id': role, 'label': label, 'status': 'active', 'agents': []}]
        assert not row['draft_ready']
        assert store.one('jobs', job['id'])['status'] == 'queued'


def test_unknown_child_does_not_claim_results_returned(tmp_path):
    store = Store(tmp_path / 'workspace')
    job = store.enqueue('generate', {})
    folder = store.root / 'jobs' / job['id']; folder.mkdir(parents=True)
    event = {'type': 'item.updated', 'item': {'type': 'collab_tool_call',
             'agents_states': {'child': {'status': 'unknown', 'role': 'Analyst',
                                       'activity': '无法读取子任务，当前状态未知'}}}}
    (folder / 'events.jsonl').write_text(json.dumps(event) + '\n')
    ProgressTracker(store, job['id'], folder).update()
    row = json.loads(store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1", (job['id'],))[0]['data'])
    assert row['stage'] == '子任务状态暂不可确认'
    assert row['agents'][0]['status'] == 'unknown'
