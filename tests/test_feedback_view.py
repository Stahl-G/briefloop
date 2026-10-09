"""Saved feedback counts/status stay truthful beyond the polled history limits."""
import json
from pathlib import Path
import subprocess

from briefloop.feedback_view import snapshot as feedback_snapshot
from briefloop.store import Store, dump, now


def report(store):
    source = store.add_source('Synthetic', 'Three files were completed.')
    run = store.create_run({'title': 'Synthetic', 'objective': 'Summarize', 'allow_web': False}, [source['id']])
    return store.publish(run['id'], {'title': 'Synthetic', 'markdown': 'Three files were completed.'})


def render(state):
    script = """
        import fs from 'node:fs';
        import {createFeedbackList} from './frontend/feedback-list.js';
        const state=JSON.parse(fs.readFileSync(0,'utf8')),nodes={};
        const $=id=>nodes[id]??={innerHTML:'',hidden:true,querySelector:()=>null};
        const list=createFeedbackList({$,esc:String,getState:()=>state,openLearning:()=>{},startLearning:()=>{}});
        list.render();
        process.stdout.write(JSON.stringify({rows:list.rows(),html:nodes['feedback-list'].innerHTML,hint:nodes['feedback-saved-hint'].innerHTML,next:nodes['feedback-next-step']}));
    """
    result = subprocess.run(['node', '--input-type=module', '-e', script], input=dump(state),
                            cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_snapshot_retains_old_learning_outcomes_and_real_revisions(tmp_path):
    store = Store(tmp_path)
    brief = report(store)
    feedback_ids = []
    for status in ('failed', 'interrupted', 'cancelled', 'complete'):
        fid = store.comment(brief['id'], status)['id']
        feedback_ids.append(fid)
        with store.tx() as c:
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',
                      (status, 'learn', status, dump({'feedback_ids': [fid]}), None, None, now(), now()))
            c.execute('UPDATE feedback SET batch_id=? WHERE id=?', (status, fid))
    orphan = store.comment(brief['id'], 'Missing batch record')['id']
    with store.tx() as c:
        c.execute("UPDATE feedback SET batch_id='missing' WHERE id=?", (orphan,))
    for _ in range(31):
        job = store.enqueue('export_docx', {})
        store.update_job(job['id'], 'complete')
    revised = store.revise(brief['id'], markdown='Four files were completed.')
    state = store.snapshot()
    assert len(state['jobs']) == 30
    assert not any(job['kind'] == 'learn' for job in state['jobs'])
    saved = {item['id']: item for item in state['feedback']}
    assert [saved[fid]['learning_status'] for fid in feedback_ids] == ['failed', 'interrupted', 'cancelled', 'complete']
    assert saved[orphan]['learning_status'] is None
    revision = state['feedback'][0]
    assert revision['kind'] == 'revision'
    assert json.loads(revision['data']) == {'before': brief['id'], 'after': revised['id']}
    assert state['feedback_summary'] == {'total': 6, 'pending': 1}
    rendered = render(state)
    shown = {row['id']: row for row in rendered['rows']}
    assert [shown[fid]['status'] for fid in feedback_ids] == ['整理失败', '整理中断', '整理已停止', '已整理']
    assert shown[orphan]['status'] == '整理状态未知'
    assert shown[revision['id']]['kind'] == '改稿' and shown[revision['id']]['text'].strip()


def test_feedback_window_uses_global_counts_and_bounded_indexed_status_lookup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    brief = report(store)
    old_pending = store.comment(brief['id'], 'Older feedback still needs learning')['id']
    with store.tx() as c:
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',
                  ('batch', 'learn', 'complete', '{}', None, None, now(), now()))
        c.executemany('INSERT INTO feedback VALUES(?,?,?,?,?,?)',
                      [(f'new_{i}', brief['id'], 'comment', dump({'text': f'New feedback {i}'}), 'batch', now()) for i in range(100)])
    queries = []
    rows = store.rows
    def recorded(query, args=()):
        queries.append(query)
        return rows(query, args)
    monkeypatch.setattr(store, 'rows', recorded)
    state = feedback_snapshot(store)
    assert len(queries) == 2  # One bounded indexed join plus one aggregate, not N+1 history scans.
    assert len(state['feedback']) == 100
    assert old_pending not in {item['id'] for item in state['feedback']}
    assert all(item['learning_status'] == 'complete' for item in state['feedback'])
    assert state['feedback_summary'] == {'total': 101, 'pending': 1}
    rendered = render(state)
    assert '共 101 条，其中 1 条待整理' in rendered['html']
    assert '仅显示最近 100 条' in rendered['html']
    assert '已保存 101 条反馈，1 条待整理' in rendered['hint']
    assert '查看全部' not in rendered['hint']
    plan = rows('EXPLAIN QUERY PLAN ' + queries[0])
    assert any('SEARCH j USING INDEX' in step['detail'] for step in plan)
    assert not any('SCAN j' in step['detail'] for step in plan)


def test_learning_entry_stays_visible_after_feedback_is_claimed():
    pending = render({'feedback_summary': {'total': 1, 'pending': 1}, 'jobs': []})['next']
    assert not pending['hidden'] and '整理反馈' in pending['innerHTML']
    running = render({'feedback_summary': {'total': 1, 'pending': 0},
                      'jobs': [{'kind': 'learn', 'status': 'running'}]})['next']
    assert not running['hidden'] and '正在整理和验证' in running['innerHTML']
    assert '查看状态' in running['innerHTML'] and 'data-feedback-start' not in running['innerHTML']
    complete = render({'feedback_summary': {'total': 1, 'pending': 0},
                       'jobs': [{'kind': 'learn', 'status': 'complete'}]})['next']
    assert complete['hidden']
