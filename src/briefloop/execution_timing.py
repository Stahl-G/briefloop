"""Targets guide planning; only an explicitly chosen hard limit cancels work."""
import json


def policy(store, *, job_id=None, session_id=None):
    settings = store.settings()
    values = {'target_minutes': settings['timeout_minutes'],
              'hard_timeout_minutes': settings['hard_timeout_minutes']}
    if session_id and not job_id:
        rows = store.rows("SELECT data FROM chat_events WHERE session_id=? AND kind='job/attached' ORDER BY seq DESC LIMIT 1", (session_id,))
        if rows:
            job_id = json.loads(rows[0]['data']).get('jobId')
    if job_id:
        payload = json.loads(store.one('jobs', job_id)['payload'])
        run_id = payload.get('run_id')
        if not run_id and payload.get('version_id'):
            run_id = store.one('briefs', payload['version_id'])['run_id']
        if run_id:
            req = json.loads(store.one('runs', run_id)['requirements'])
            # Older reports never acquired an explicit hard limit under this policy.
            values = {'target_minutes': req.get('target_minutes'),
                      'hard_timeout_minutes': req.get('hard_timeout_minutes') or 0}
    return values


def instructions(target_minutes):
    if not target_minutes:
        return ''
    return (f'本次报告目标用时约 {target_minutes} 分钟，是安排研究与写作的目标，不是截止或取消条件。'
            '优先完成必答内容并及时保存可读稿件，减少重复读取和无关扩展；'
            '超过目标仍继续完成，不删必要分析、不伪造核验、不把任务标为失败。'
            '搜索额度、联网权限及独立审阅要求保持不变。')
