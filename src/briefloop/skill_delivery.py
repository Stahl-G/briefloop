"""Verify direct-author method delivery against the host's durable message.

This proves input delivery, never compliance or quality. The chat journal is
owned by the transport; no self-hash or author-written receipt is trusted.
"""
import json

START = '<briefloop-method>'
END = '</briefloop-method>'


def selected(store, run, payload):
    if 'skill_override' in payload:
        return payload['skill_override']
    return store.one('skills', run['skill_id']) if run.get('skill_id') else None


def context(store, skill):
    from .skills import bind_context
    bindings = bind_context(store, skill)
    roles = sorted(set(bindings) & {'scout', 'analyst'})
    return {'skill_id': skill['id'] if roles else None, 'roles': roles,
            'instructions': skill['content'] if roles else ''}


def appendix(value):
    return '\n' + START + '\n' + value['instructions'] + '\n' + END if value['roles'] else ''


def _transport_output(store, job_id, receipt):
    rows = store.rows("SELECT data FROM events WHERE job_id=? AND kind='writer_output'", (job_id,))
    return any((value.get('stage'), value.get('session_id'), value.get('message_id')) ==
               ('direct-writing', receipt.get('session_id'), receipt.get('message_id'))
               for value in (json.loads(row['data']) for row in rows))


def record(store, job, folder, prompt, value):
    """Only accept a completed transport message bound to this exact execution."""
    try:
        binding = json.loads((folder / 'conversation.json').read_text(encoding='utf-8'))
        if binding.get('job_id') != job['id'] or not _transport_output(store, job['id'], binding):
            return
        rows = store.rows("SELECT prompt,status FROM chat_messages WHERE id=? AND session_id=? AND role='user'",
                          (binding.get('message_id'), binding.get('session_id')))
        if not rows or rows[0]['status'] != 'completed' or not (rows[0]['prompt'] or '').endswith(prompt):
            return
    except (OSError, ValueError):
        return
    store.event(job['id'], 'direct_skill_delivered', {
        'skill_id': value['skill_id'], 'roles': value['roles'],
        'session_id': binding['session_id'], 'message_id': binding['message_id']})


def verify(store, brief):
    """Fail closed for direct trials, including resumed comparison/adoption."""
    brief = store.one('briefs', brief['id'])
    run = store.one('runs', brief['run_id'])
    if json.loads(run['requirements']).get('completion_mode') != 'direct':
        return
    from .draft_completion import origin
    job = origin(store, brief['id'])
    skill = selected(store, run, json.loads(job['payload']))
    expected = context(store, skill)
    if skill and not expected['roles']:
        raise ValueError('该技能没有直写作者可应用的角色绑定，不能比较或采用该试写')
    events = store.rows("SELECT data FROM events WHERE job_id=? AND kind='direct_skill_delivered' ORDER BY seq DESC", (job['id'],))
    for row in events:
        receipt = json.loads(row['data'])
        if not _transport_output(store, job['id'], receipt):
            continue
        if (receipt.get('skill_id'), receipt.get('roles')) != (expected['skill_id'], expected['roles']):
            continue
        messages = store.rows("SELECT prompt,status FROM chat_messages WHERE id=? AND session_id=? AND role='user'",
                              (receipt.get('message_id'), receipt.get('session_id')))
        if not messages or messages[0]['status'] != 'completed':
            continue
        prompt = messages[0]['prompt'] or ''
        if expected['roles']:
            if not prompt.endswith(appendix(expected)):
                continue
        elif START in prompt or END in prompt:
            continue
        return receipt
    raise ValueError('直写技能缺少实际送达记录，不能比较或采用该试写；原稿与学习进度保留')
