"""Version-bound writer configuration; never read today's workspace defaults."""
import json
import re


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def configuration(payload, role=None):
    payload = _object(payload)
    runtime = _object(payload.get('runtime'))
    if role:
        runtime = _object(_object(payload.get('role_models')).get(role, runtime))
    # Only display fields, never arbitrary provider config, commands or secrets.
    from .execution_records import sanitize
    fields = {'backend': payload.get('agent_backend') or runtime.get('backend'),
              'model': runtime.get('model'),
              'effort': runtime.get('reasoning_effort') or runtime.get('model_variant') or runtime.get('effort') or runtime.get('variant')}
    return {key: sanitize(value)[:160] if isinstance(value, str) and value else None
            for key, value in fields.items()}


def _published_configuration(store, brief, job, role=None):
    if role:
        return configuration(job['payload'], role)
    # Native orchestration copies an independent Analyst's admitted draft to
    # the root publication. Prefer that actual conversation when hashes match.
    if not brief['id'].endswith('_r1') and re.fullmatch(r'job_[a-zA-Z0-9]+', job['id']):
        folder = store.root / 'jobs' / job['id'] / 'analyst'
        try:
            binding = _object((folder / 'conversation.json').read_text(encoding='utf-8'))
            draft = _object((folder / 'draft.json').read_text(encoding='utf-8'))
            from .document_model import document_hash
            if binding.get('job_id') == job['id'] and draft.get('editor_document') and document_hash(draft['editor_document']) == brief['hash']:
                return configuration({'runtime': binding.get('runtime'), 'agent_backend': binding.get('backend')})
        except (OSError, ValueError, TypeError):
            pass
    return configuration(job['payload'])


def record(store, brief, job, *, role=None):
    """Runner-owned receipt, bound to the admitted version and content hash."""
    if brief['id'].endswith('_evidence'):
        return
    if store.rows("SELECT seq FROM events WHERE kind='writer_version' AND json_extract(data,'$.version_id')=? LIMIT 1", (brief['id'],)):
        return
    value = {'version_id': brief['id'], 'brief_hash': brief['hash'],
             'configuration': _published_configuration(store, brief, job, role)}
    store.event(job['id'], 'writer_version', value)


def record_chat(store, brief, session_id, message_id):
    # Caller passes the runner's active message identity, never a model claim.
    if not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'"):
        return
    rows = store.rows("SELECT m.runtime FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id WHERE m.id=? AND m.session_id=? AND m.role='user' AND s.turn_id=m.id",
                      (message_id, session_id))
    if rows:
        runtime = _object(rows[0]['runtime'])
        store.event(None, 'writer_version', {'version_id': brief['id'], 'brief_hash': brief['hash'],
                    'configuration': configuration({'runtime': runtime, 'agent_backend': runtime.get('backend')})})


def _writer(store, brief):
    rows = store.rows("SELECT data FROM events WHERE kind='writer_version' AND json_extract(data,'$.version_id')=? ORDER BY seq LIMIT 1", (brief['id'],))
    if rows:
        receipt = _object(rows[0]['data'])
        if receipt.get('brief_hash') == brief['hash']:
            return receipt.get('configuration')
    # Exact IDs are publication contracts in the old runners, not loose result
    # matches (assessment results may mention a version they did not write).
    match = re.fullmatch(r'brief_([a-zA-Z0-9]+)(_r1|_analyst)?', brief['id'])
    if not match:
        if brief['id'].endswith('_evidence'):
            return None
        # Legacy refinements use random version IDs. Their runner-owned source
        # snapshot explicitly records each admitted version and its exact hash.
        jobs = store.rows("SELECT * FROM jobs WHERE kind IN ('generate','assess') AND json_extract(payload,'$.run_id')=? ORDER BY rowid", (brief['run_id'],))
        for job in jobs:
            payload = _object(job['payload'])
            if job['kind'] != 'generate' and not payload.get('continuation_of'):
                continue
            if not re.fullmatch(r'job_[a-zA-Z0-9]+', job['id']):
                continue
            path = store.root / 'jobs' / job['id'] / 'generated-source-snapshots.json'
            try:
                saved = _object(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if _object(saved.get(brief['id'])).get('brief_hash') == brief['hash']:
                return _published_configuration(store, brief, job)
        return None
    rows = store.rows('SELECT * FROM jobs WHERE id=?', ('job_' + match[1],))
    if not rows:
        return None
    job = rows[0]; payload = _object(job['payload'])
    if payload.get('run_id') != brief['run_id']:
        return None
    suffix = match[2]
    if job['kind'] != 'generate' and not (suffix == '_r1' and job['kind'] == 'assess' and payload.get('continuation_of')):
        return None
    return _published_configuration(store, brief, job, 'analyst' if suffix == '_analyst' else None)


def describe(store, brief):
    author = brief['author']
    mode = {'user': 'manual', 'agent': 'ai', 'import': 'imported', 'example': 'example'}.get(author, 'unknown')
    current = _writer(store, brief) if mode == 'ai' else None
    # An unchanged evidence-only version preserves its prose writer, not its
    # evaluator/evidence model. Other unknown AI children stay unknown.
    if mode == 'ai' and current is None and brief['id'].endswith('_evidence') and brief.get('parent_id'):
        parent = store.one('briefs', brief['parent_id'])
        if parent['author'] == 'agent' and parent['run_id'] == brief['run_id'] and parent['markdown'] == brief['markdown']:
            current = _writer(store, parent)
    first = store.rows('SELECT * FROM briefs WHERE run_id=? ORDER BY rowid LIMIT 1', (brief['run_id'],))[0]
    original = _writer(store, first) if first['author'] == 'agent' else None
    return {'mode': mode, 'action': 'revision' if brief.get('parent_id') else 'generation',
            'configuration': current, 'original_configuration': original,
            'original_version_id': first['id'], 'recorded': bool(current)}
