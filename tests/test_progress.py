"""Project public runtime receipts without configuring or invoking a model."""
import json
from briefloop.progress import ProgressTracker
from briefloop.store import Store


def test_child_turn_receipts_drive_progress_without_inventing_activity_liveness(tmp_path):
    store = Store(tmp_path / 'workspace')
    job_id = 'job_progress_receipts'
    folder = store.root / 'jobs' / job_id
    folder.mkdir(parents=True)
    (folder / 'plan.json').write_text('{}', encoding='utf-8')
    log = folder / 'events.jsonl'
    tracker = ProgressTracker(store, job_id, folder)

    def emit(event):
        with log.open('a', encoding='utf-8') as out:
            out.write(json.dumps(event) + '\n')
        tracker.update()
        raw = store.rows("SELECT data FROM events WHERE job_id=? AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1", (job_id,))[0]['data']
        assert 'PRIVATE' not in raw
        return json.loads(raw)

    activity = {'type': 'subagent_activity', 'agentThreadId': 'child', 'kind': 'started',
                'agentPath': 'PRIVATE PATH', 'prompt': 'PRIVATE PROMPT'}
    row = emit({'type': 'item.started', 'item': activity})
    assert row['agents'][0]['status'] == 'unknown'
    row = emit({'type': 'child.turn.started', 'data': {'threadId': 'child'}})
    assert row['agents'][0]['status'] == 'running' and row['stage'] == '子任务正在执行'
    row = emit({'type': 'item.completed', 'item': {**activity, 'kind': 'completed'}})
    assert row['agents'][0]['status'] == 'running'
    row = emit({'type': 'child.thread.started', 'data': {'threadId': 'child', 'agentRole': 'scout'}})
    assert row['agents'][0]['role'] == 'Scout' and row['stage'] == 'Scout 正在读取与核对来源'

    # A frozen plan can retain its original running status after a turn ends.
    (folder / 'agents.json').write_text(json.dumps({'agents': [
        {'agent_id': 'child', 'role': 'scout', 'status': 'running'}]}), encoding='utf-8')
    row = emit({'type': 'child.turn.completed', 'data': {'threadId': 'child', 'status': 'completed'}})
    assert row['agents'][0]['status'] == 'completed' and not row['draft_ready']
    assert next(s['status'] for s in row['stages'] if s['id'] == 'research') == 'done'
    row = emit({'type': 'item.started', 'item': {**activity, 'kind': 'interacted'}})
    assert row['agents'][0]['status'] == 'completed'
    for status in ('interrupted', 'cancelled', 'failed'):
        row = emit({'type': 'child.turn.started', 'data': {'threadId': 'child'}})
        assert row['agents'][0]['status'] == 'running'
        row = emit({'type': 'child.turn.completed', 'data': {'threadId': 'child', 'status': status}})
        assert row['agents'][0]['status'] == status
        assert row['stage'] == '子任务本轮已结束，正在整理与交接'
        assert next(s['status'] for s in row['stages'] if s['id'] == 'research') == 'active'
    assert not store.rows('SELECT id FROM jobs')


def test_provider_retries_project_each_attempt_without_leaking_error_details(tmp_path):
    store=Store(tmp_path/'workspace');folder=store.root/'jobs'/'retry';folder.mkdir(parents=True)
    log=folder/'events.jsonl';tracker=ProgressTracker(store,'retry',folder)
    def emit(kind,message):
        with log.open('a') as out:
            out.write(json.dumps({'type':kind,'data':{'message':message}})+'\n')
        tracker.update()
        row=store.rows("SELECT data FROM events WHERE job_id='retry' AND kind='runtime_progress' ORDER BY seq DESC LIMIT 1")[0]
        assert 'SECRET' not in row['data']
        return json.loads(row['data'])
    previous=None
    for attempt in range(1,7):
        row=emit('runtime.status',f'retry {attempt}/6: Connection error. https://SECRET')
        assert f'{attempt}/6' in row['message']
        assert row['runtime_notice']
        assert row['last_activity'] and row['last_activity']!=previous
        previous=row['last_activity']
    assert len(store.rows("SELECT seq FROM events WHERE job_id='retry' AND kind='runtime_progress'"))==6
    row=emit('runtime.status','provider retry exhausted: Connection error. SECRET')
    assert row['stage']=='模型服务重试已结束'
    row=emit('error','Connection error. SECRET')
    assert row['stage']=='模型连接失败'
    assert tracker.failure_message==row['message']
    # A resumed turn must not inherit the earlier transport failure.
    emit('turn.started','')
    assert tracker.failure_message is None
    row=emit('error','Unknown SECRET')
    assert row['stage']=='模型执行遇到错误' and tracker.failure_message is None
