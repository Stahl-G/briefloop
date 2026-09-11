"""Deterministic BriefLoop status notes for background tasks.

These are not model turns. They report a task's start and end into the
conversation that started it, so the user does not have to ask. Tasks with no
conversation are still visible in the sidebar's current-tasks block.
"""
import json

KIND_LABELS = {
    'generate': '生成简报', 'assess': '重新评分', 'review': '独立审阅', 'revise': '按审阅修订',
    'learn': 'WikiSkill 学习', 'export_docx': '生成工作稿 Word', 'release': '制作正式 Word',
    'audit_bundle': '制作审计包', 'source_refresh': '复查来源', 'prepare_template': '准备模板',
}
TERMINAL = ('complete', 'failed', 'interrupted', 'cancelled')


def _payload(job):
    try:
        return json.loads(job.get('payload') or '{}')
    except (ValueError, TypeError):
        return {}


def _chat_ready(store):
    return bool(store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_sessions'"))


def target_session(store, payload):
    """Prefer the task's explicit session, else the newest user conversation.

    Job execution sessions are marked internal and never chosen; the fallback is
    what lets a task the main agent started (no session_id) report progress into
    the conversation the user is actually reading.
    """
    if not _chat_ready(store):
        return None
    session = payload.get('session_id')
    if session and store.rows('SELECT id FROM chat_sessions WHERE id=?', (session,)):
        return session
    rows = store.rows("SELECT id FROM chat_sessions s WHERE lifecycle='active' "
                      "AND NOT EXISTS(SELECT 1 FROM chat_events e WHERE e.session_id=s.id AND e.kind='session/internal') "
                      "ORDER BY updated DESC LIMIT 1")
    return rows[0]['id'] if rows else None


def status_text(kind, status, error=None):
    if kind not in KIND_LABELS:
        return None
    label = KIND_LABELS[kind]
    if status == 'queued':
        return f'已开始任务：{label}。完成后我会在这里汇报。'
    if status == 'complete':
        return f'{label} 已完成。'
    if status in ('failed', 'interrupted', 'cancelled'):
        verb = {'failed': '失败', 'interrupted': '中断', 'cancelled': '已停止'}[status]
        reason = ('：' + str(error)) if error else ''
        return f'{label} {verb}{reason}。可在任务列表查看或恢复。'
    return None


def notify(store, job, status, *, text=None):
    """Post one status note per (job, status) into the task's conversation."""
    body = text or status_text(job.get('kind'), status, job.get('error'))
    if not body:
        return None
    session = target_session(store, _payload(job))
    if not session:
        return None
    marker = f"task:{job['id']}:{status}"
    if store.rows("SELECT seq FROM chat_events WHERE session_id=? AND kind='task/status' "
                  "AND json_extract(data,'$.marker')=?", (session, marker)):
        return None
    from .chat_store import ChatStore
    chat = ChatStore(store)
    message = chat.message(session, body, role='assistant', status='completed', mode='notice')
    chat.event(session, 'task/status', {'marker': marker, 'job_id': job['id'], 'kind': job.get('kind'),
                                        'status': status, 'message_id': message['id']})
    return message
