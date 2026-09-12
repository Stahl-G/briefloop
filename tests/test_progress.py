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
