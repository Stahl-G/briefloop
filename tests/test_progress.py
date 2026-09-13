"""Progress projection must follow the current evaluation directory layout."""
import json
import pytest
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


@pytest.mark.parametrize('event', [
    {'type':'error','data':{'message':'Reconnecting... waiting for network; Authorization: Bearer secret-test-key'}},
    {'type':'error','message':'Retrying request https://secret-test-key@example.test'},
    {'type':'error','error':{'message':'Reconnecting secret-test-key'}},
])
def test_progress_surfaces_reconnection_without_provider_details(tmp_path,event):
    store=Store(tmp_path/'workspace')
    job=store.enqueue('generate',{})
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    log=folder/'events.jsonl'
    log.write_text(json.dumps(event)+'\n',encoding='utf-8')
    tracker=ProgressTracker(store,job['id'],folder)
    tracker.update()
    def latest():
        row=store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1",(job['id'],))[0]
        assert 'secret-test-key' not in row['data']
        return json.loads(row['data'])
    assert latest()['stage']=='模型连接中断，正在重试'
    # File activity and usage telemetry do not imply the provider recovered.
    (folder/'plan.json').write_text('{}')
    with log.open('a',encoding='utf-8') as out:
        out.write(json.dumps({'type':'thread.tokenUsage.updated','usage':{}})+'\n')
    tracker.update()
    assert latest()['stage']=='模型连接中断，正在重试'
    # Actual resumed tool activity clears the connection notice.
    with log.open('a',encoding='utf-8') as out:
        out.write(json.dumps({'type':'item.started','item':{'type':'command_execution'}})+'\n')
    tracker.update()
    assert latest()['stage']=='正在分配研究任务'


def test_progress_projects_unknown_errors_as_fixed_text(tmp_path):
    store=Store(tmp_path/'workspace')
    job=store.enqueue('generate',{})
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    (folder/'events.jsonl').write_text(json.dumps({'type':'error','data':{'message':'401 api_key=secret-test-key'}})+'\n')
    ProgressTracker(store,job['id'],folder).update()
    row=store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1",(job['id'],))[0]
    assert json.loads(row['data'])['stage']=='模型执行遇到错误'
    assert 'secret-test-key' not in row['data']
