"""Bind report-side conversations to the user's selected saved version."""
import json

from .store import dump, now


CONTEXT_EVENT = 'revision/message_context'


def bind_context(store, session_id, message_id, context):
    from .chat_store import ChatStore
    ChatStore(store).session(session_id)
    version_id = None
    if context is not None:
        if not isinstance(context, dict) or not isinstance(context.get('version_id'), str):
            raise ValueError('请选择要讨论的报告版本')
        brief = store.one('briefs', context['version_id'])
        if brief['hash'] != context.get('hash'):
            raise ValueError('报告内容已变化，请重新保存后发送')
        version_id = brief['id']
    value = {'message_id': message_id, 'base_version_id': version_id}
    with store.tx() as connection:
        rows = connection.execute(
            "SELECT data FROM chat_events WHERE session_id=? AND kind=? AND json_extract(data,'$.message_id')=?",
            (session_id, CONTEXT_EVENT, message_id),
        ).fetchall()
        if rows:
            if json.loads(rows[-1]['data']) != value:
                raise ValueError('同一消息关联的报告已变化，请重新发送')
            return
        connection.execute('INSERT INTO chat_events(session_id,kind,data,created) VALUES(?,?,?,?)',
                           (session_id, CONTEXT_EVENT, dump(value), now()))


def context_for_message(store, session_id, message_id):
    rows = store.rows(
        "SELECT data FROM chat_events WHERE session_id=? AND kind=? AND json_extract(data,'$.message_id')=? ORDER BY seq DESC LIMIT 1",
        (session_id, CONTEXT_EVENT, message_id),
    )
    return json.loads(rows[0]['data']) if rows else None


def prepare_input(store, session_id, message):
    context = context_for_message(store, session_id, message['id'])
    if not context or not context.get('base_version_id'):
        return message
    brief = store.one('briefs', context['base_version_id'])
    prompt = message.get('prompt') if message.get('prompt') is not None else message['text']
    prompt += '\n\n当前报告（用户在报告页选择）：' + dump({
        'version_id': brief['id'], 'run_id': brief['run_id'],
    })
    prompt += ('\n按用户请求读取、解释或修订此报告。需要修改时保留原版本；'
               '按读者的重要性补足遗漏，保留关键数字、比较对象与适用条件。'
               '研究过程记录在工作区，正文保留有助于判断的来源归属和事实条件。')
    if message.get('allow_web') and message.get('runtime', {}).get('permission') != 'read-only':
        from .research_plan import AdmissionError
        from .revision_research import begin
        try:
            stage = begin(store, brief['run_id'], base_version_id=brief['id'],
                          session_id=session_id, message_id=message['id'])
            prompt += '\n本消息的报告补查阶段已开启，沿用报告剩余研究预算：' + dump(stage)
        except (AdmissionError, ValueError) as exc:
            prompt += '\n本消息补查阶段未开启：' + str(exc)
            from .chat_store import ChatStore
            ChatStore(store).event(session_id, 'revision/researchUnavailable', {
                'message_id': message['id'], 'version_id': brief['id'], 'message': str(exc),
            })
    return {**message, 'prompt': prompt}


def finish_message(store, message_id, status):
    """Close the message's research and continue checks for its saved revision."""
    messages = store.rows("SELECT * FROM chat_messages WHERE id=? AND role='user'", (message_id,))
    if not messages:
        return
    message = messages[0]
    context = context_for_message(store, message['session_id'], message_id)
    if not context or not context.get('base_version_id'):
        return
    settled = store.rows("""SELECT 1 FROM chat_events WHERE session_id=? AND kind='revision/message_settled'
        AND json_extract(data,'$.message_id')=? LIMIT 1""", (message['session_id'], message_id))
    if settled:
        return
    def mark_settled(**result):
        from .chat_store import ChatStore
        ChatStore(store).event(message['session_id'], 'revision/message_settled', {'message_id': message_id, **result})
    from .revision_research import finish_for_message
    closure = 'completed' if status == 'completed' else 'cancelled' if status == 'cancelled' else 'failed'
    finish_for_message(store, message_id, status=closure)
    if status != 'completed':
        mark_settled(status=status)
        return
    base = store.one('briefs', context['base_version_id'])
    latest = store.rows('SELECT * FROM briefs WHERE run_id=? AND created<=? ORDER BY rowid DESC LIMIT 1',
                        (base['run_id'], message['updated']))[0]
    if latest['id'] == base['id'] or latest['author'] != 'agent':
        mark_settled(status=status, checks='no_revision')
        return
    if not json.loads(latest['detail']).get('evidence_needs_review'):
        mark_settled(status=status, checks='not_needed')
        return
    # Complete this selected report's new draft; this is not writer attribution.
    if latest['created'] < message['created']:
        mark_settled(status=status, checks='no_revision')
        return
    ancestor = latest
    while ancestor.get('parent_id') and ancestor['id'] != base['id']:
        ancestor = store.one('briefs', ancestor['parent_id'])
    if ancestor['id'] != base['id']:
        mark_settled(status=status, checks='different_revision')
        return
    from .chat_store import ChatStore
    try:
        from .draft_completion import enqueue_revision
        job = enqueue_revision(store, latest['id'], runtime=json.loads(message['runtime']), session_id=message['session_id'])
        if job is None:
            mark_settled(status=status, checks='not_needed')
            return
        ChatStore(store).event(message['session_id'], 'revision/checksQueued', {
            'message_id': message_id, 'version_id': latest['id'], 'job_id': job['id'],
        })
        mark_settled(status=status, version_id=latest['id'], job_id=job['id'])
    except (ValueError, KeyError) as exc:
        ChatStore(store).event(message['session_id'], 'revision/checksUnavailable', {
            'message_id': message_id, 'version_id': latest['id'], 'message': str(exc),
        })


def recover_terminal_messages(store):
    """Replay only report-message closeouts lost between durable writes."""
    rows = store.rows("""SELECT DISTINCT m.id,m.status FROM chat_messages m JOIN chat_events context
        ON context.session_id=m.session_id AND context.kind='revision/message_context'
        AND json_extract(context.data,'$.message_id')=m.id
        WHERE m.role='user' AND m.status IN ('completed','failed','cancelled','interrupted')
        AND json_extract(context.data,'$.base_version_id') IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM chat_events done WHERE done.session_id=m.session_id
            AND done.kind='revision/message_settled' AND json_extract(done.data,'$.message_id')=m.id)""")
    for row in rows:
        finish_message(store, row['id'], row['status'])
