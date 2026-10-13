"""Host-admitted research for a real user's saved-report revision.

The host binds report selection to the durable user message before dispatch.
This module has no agent-facing authorization or budget-grant operation.
"""
import json

from .store import now, uid
from .research_plan import AdmissionError, _read_plan, _save_plan, _requests_key

CONTEXT_EVENT = 'revision/message_context'
STATUSES = ('completed', 'cancelled', 'failed', 'budget_exhausted')


def binding(connection, run_id, base_version_id, session_id, message_id):
    """Check the host journal, not arguments supplied by the writing agent."""
    run = connection.execute('SELECT requirements FROM runs WHERE id=?', (run_id,)).fetchone()
    brief = connection.execute('SELECT run_id FROM briefs WHERE id=?', (base_version_id,)).fetchone()
    if not run or not brief or brief['run_id'] != run_id:
        raise AdmissionError('补研究的基础稿件不属于本报告', code='revision_version_mismatch')
    if not json.loads(run['requirements']).get('allow_web'):
        raise AdmissionError('本报告冻结为离线任务，不能联网修订', code='revision_offline')
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'").fetchone()
    message = connection.execute('SELECT * FROM chat_messages WHERE id=? AND session_id=?',
                                 (message_id, session_id)).fetchone() if tables else None
    if (not message or message['role'] != 'user' or not message['allow_web']
            or message['status'] not in ('sending', 'delivered', 'streaming') or not message['text'].strip()):
        raise AdmissionError('补研究需要本会话真实用户消息及本回合联网许可', code='revision_message_mismatch')
    contexts = connection.execute('SELECT data FROM chat_events WHERE session_id=? AND kind=? ORDER BY seq DESC',
                                  (session_id, CONTEXT_EVENT)).fetchall()
    context = next((json.loads(row['data']) for row in contexts
                    if json.loads(row['data']).get('message_id') == message_id), None)
    if not context or context.get('base_version_id') != base_version_id:
        raise AdmissionError('用户消息没有绑定所选修订稿件', code='revision_context_missing')
    return {'run_id': run_id, 'base_version_id': base_version_id,
            'session_id': session_id, 'message_id': message_id}


def begin(store, run_id, *, base_version_id, session_id, message_id, job_id=None):
    """Open or recover one stage per bound user message without renewing budget."""
    from .research_budget import _load, KINDS
    with store.tx() as connection:
        bound = binding(connection, run_id, base_version_id, session_id, message_id)
        plan = _read_plan(connection, run_id)
        if plan is None:
            raise AdmissionError('本报告尚未冻结研究计划', code='plan_missing')
        previous = plan.get('revision_research') or {}
        for stage in [previous, *(plan.get('revision_research_history') or [])]:
            if stage and all(stage.get(key) == value for key, value in bound.items()):
                return {**stage, 'idempotent': True}
        if previous.get('status') == 'active':
            raise AdmissionError('另一条用户修订仍在补研究，请先结束或恢复它', code='revision_stage_open')
        if any(info.get('status') == 'active' for info in (plan.get('rounds') or {}).values()):
            raise AdmissionError('原研究轮次尚未结束', code='revision_round_open')
        if (plan.get('fact_check') or {}).get('status') == 'active':
            raise AdmissionError('本报告核查阶段尚未结束', code='revision_fact_check_open')
        _, limits, meter = _load(store, connection, run_id, include_fact_check_grants=False)
        if limits is None:
            raise AdmissionError('旧报告没有明确研究额度，修订不能自动获得无限预算', code='revision_no_budget')
        used = {'search_requests': meter['search_requests'], 'candidate_urls': len(meter['candidate_urls']),
                'source_pages': len(meter['source_pages'])}
        remaining = {kind: max(0, limits[kind] - used[kind]) for kind in KINDS}
        if remaining['source_pages'] == 0:
            raise AdmissionError('本报告原文读取额度已用完；补研究未启动', code='revision_budget_exhausted')
        if previous:
            plan.setdefault('revision_research_history', []).append(previous)
        stage = {**bound, 'stage_id': uid('revision'), 'status': 'active', 'created': now(),
                 'closed': None, 'outcome': None, 'remaining_at_start': remaining}
        plan['revision_research'] = stage
        _save_plan(connection, run_id, plan)
    if job_id:
        store.event(job_id, 'revision_research', {'action': 'begin', **stage})
    return {**stage, 'idempotent': False}


def finish_for_message(store, message_id, *, status='completed', summary=''):
    """Host transport closeout, including interrupted-message recovery."""
    rows = store.rows("SELECT key,value FROM meta WHERE key LIKE 'research_plan:%'")
    results = []
    for row in rows:
        stage = json.loads(row['value']).get('revision_research') or {}
        if stage.get('message_id') == message_id and stage.get('status') == 'active':
            results.append(finish(store, row['key'].removeprefix('research_plan:'), stage['stage_id'],
                                  status=status, summary=summary))
    return results


def finish(store, run_id, stage_id, *, status, summary='', job_id=None):
    """Close exactly this stage; late responses cannot enter the next revision."""
    if status not in STATUSES:
        raise ValueError('无效补研究结束状态')
    with store.tx() as connection:
        plan = _read_plan(connection, run_id)
        current = (plan or {}).get('revision_research') or {}
        stage = next((item for item in [current, *((plan or {}).get('revision_research_history') or [])]
                      if item.get('stage_id') == stage_id), None)
        if not stage:
            raise AdmissionError('补研究阶段不存在', code='revision_stage_missing')
        if stage.get('status') != 'active':
            return {**stage, 'idempotent': True}
        row = connection.execute('SELECT value FROM meta WHERE key=?', (_requests_key(run_id),)).fetchone()
        entries = json.loads(row['value']) if row else {}
        usage = {'search': 0, 'pages': 0}
        for entry in entries.values():
            if entry.get('round_id') == stage_id and entry.get('operation') in usage:
                usage[entry['operation']] += 1
        stage.update(status=status, closed=now(), outcome={'status': status, 'summary': summary, 'usage': usage})
        _save_plan(connection, run_id, plan)
    if job_id:
        store.event(job_id, 'revision_research', {'action': 'finish', 'stage_id': stage_id, 'status': status})
    return {**stage, 'idempotent': False}
