"""Version-bound writer attribution and explicitly sourced execution settings."""
import json
import re


def _object(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def configuration(payload, role=None):
    payload = _object(payload); runtime = _object(payload.get('runtime'))
    if role:runtime = _object(_object(payload.get('role_models')).get(role, runtime))
    from .execution_records import sanitize
    fields = {'backend': payload.get('agent_backend') or runtime.get('backend'), 'model': runtime.get('model'),
              'effort': runtime.get('reasoning_effort') or runtime.get('model_variant') or runtime.get('effort') or runtime.get('variant')}
    return {**{key: sanitize(value)[:160] if isinstance(value, str) and value else None for key, value in fields.items()},
            'kind': 'frozen_request', 'reported': {}}


def _message_configuration(store, session_id, message_id, *, active=False):
    if not session_id or not message_id or not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'"):return None
    rows = store.rows("SELECT m.runtime,m.turn_id,m.status,s.turn_id AS active_turn FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id WHERE m.id=? AND m.session_id=? AND m.role='user'", (message_id, session_id))
    if not rows:return None
    row=rows[0]
    if active and (row['status'] not in ('sending','delivered','streaming') or not row['turn_id'] or row['turn_id'] != row['active_turn']):return None
    value=configuration({'runtime': _object(row['runtime']), 'agent_backend': _object(row['runtime']).get('backend')})
    # Future host receipts identify the exact durable message. Unbound historical
    # session events are deliberately not assigned by timestamps or current turn.
    reports=store.rows("SELECT data FROM chat_events WHERE session_id=? AND kind='runtime/reported' AND json_extract(data,'$.message_id')=?",(session_id,message_id))
    unique={json.dumps(_object(r['data']),sort_keys=True) for r in reports if _object(r['data']).get('source') in ('codex.turn_response','native.session_response')}
    if len(unique)==1:
        report=_object(next(iter(unique)))
        projected=configuration({'runtime': {'model':report.get('model'),'effort':report.get('effort')},'agent_backend':report.get('backend')})
        value['reported']={k:projected[k] for k in ('backend','model','effort') if projected[k]}
        value['reported_source']=report.get('source')
    return value


def record_copy(store, job, draft, folder):
    """Only called at native write_report's admitted Analyst copy boundary."""
    try:
        binding=_object((folder/'conversation.json').read_text(encoding='utf-8'))
        accepted=_object((folder/'draft-accepted.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):return
    if binding.get('job_id')!=job['id'] or not accepted.get('revision') or accepted.get('attempt_id')!=binding.get('message_id'):return
    config=_message_configuration(store,binding.get('session_id'),binding.get('message_id'))
    if config is None:return
    config['attribution']='admitted_analyst'
    from .document_model import document_hash
    store.event(job['id'],'writer_copy',{'run_id':_object(job['payload']).get('run_id'),
                'brief_hash':document_hash(draft['editor_document']), 'session_id':binding['session_id'],
                'message_id':binding['message_id'],'accepted_revision':accepted['revision'],'configuration':config})


def _published_configuration(store, brief, job, role=None):
    payload=_object(job['payload'])
    if role:return configuration(payload,role)
    req=_object(store.one('runs',brief['run_id'])['requirements'])
    if payload.get('agent_backend')=='briefloop-native' and job['kind']=='generate' and not brief['id'].endswith('_r1') and req.get('completion_mode') not in ('fast','fast_web'):
        # A content hash alone cannot identify an Analyst. Require the native
        # copy operation's explicit job/session/message/admission relationship.
        rows=store.rows("SELECT data FROM events WHERE job_id=? AND kind='writer_copy' AND json_extract(data,'$.run_id')=? AND json_extract(data,'$.brief_hash')=?",(job['id'],brief['run_id'],brief['hash']))
        identities={(_object(r['data']).get('session_id'),_object(r['data']).get('message_id'),_object(r['data']).get('accepted_revision')) for r in rows}
        values={json.dumps(_object(r['data']).get('configuration'),sort_keys=True) for r in rows}
        return json.loads(next(iter(values))) if len(identities)==len(values)==1 else None
    return configuration(payload)


def record(store, brief, job, *, role=None, folder=None):
    """Runner invokes this only for a newly admitted version, never a no-op."""
    if brief['author']!='agent' or brief['id'].endswith('_evidence'):return
    if store.rows("SELECT seq FROM events WHERE kind='writer_version' AND json_extract(data,'$.version_id')=? LIMIT 1",(brief['id'],)):return
    value=_published_configuration(store,brief,job,role)
    if folder is not None and value is not None and value.get('attribution')!='admitted_analyst':
        try:binding=_object((folder/'conversation.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):binding={}
        if binding.get('job_id')==job['id']:
            selected=_message_configuration(store,binding.get('session_id'),binding.get('message_id'))
            if selected is not None:value=selected
    if value is not None:store.event(job['id'],'writer_version',{'version_id':brief['id'],'brief_hash':brief['hash'],'configuration':value})


def record_chat(store, brief, session_id, message_id):
    # Native tool caller supplies its actual active message, never request JSON.
    if brief['author']!='agent':return
    value=_message_configuration(store,session_id,message_id,active=True)
    if value is not None:store.event(None,'writer_version',{'version_id':brief['id'],'brief_hash':brief['hash'],
                   'session_id':session_id,'message_id':message_id,'configuration':value})


def _writer(store, brief):
    receipts=store.rows("SELECT job_id,data FROM events WHERE kind='writer_version' AND json_extract(data,'$.version_id')=?",(brief['id'],))
    valid=[_object(r['data']) for r in receipts if _object(r['data']).get('brief_hash')==brief['hash']]
    if valid:
        identities={(r['job_id'],_object(r['data']).get('session_id'),_object(r['data']).get('message_id')) for r in receipts if _object(r['data']).get('brief_hash')==brief['hash']}
        if len(identities)!=1:return None
        values={json.dumps(r.get('configuration'),sort_keys=True) for r in valid}
        return json.loads(next(iter(values))) if len(values)==1 else None
    if brief['id'].endswith('_evidence'):return None
    candidates={}
    match=re.fullmatch(r'brief_([a-zA-Z0-9]+)(_r1|_analyst)?',brief['id'])
    jobs=store.rows("SELECT * FROM jobs WHERE kind IN ('generate','assess') AND json_extract(payload,'$.run_id')=? ORDER BY rowid",(brief['run_id'],))
    for job in jobs:
        payload=_object(job['payload']);suffix=match[2] if match else None
        exact=bool(match and job['id']=='job_'+match[1] and (job['kind']=='generate' or (suffix=='_r1' and payload.get('continuation_of'))))
        if job['kind']!='generate' and not payload.get('continuation_of'):continue
        if not re.fullmatch(r'job_[a-zA-Z0-9]+',job['id']):continue
        snapshot={}
        try:snapshot=_object((store.root/'jobs'/job['id']/'generated-source-snapshots.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):pass
        if exact or _object(snapshot.get(brief['id'])).get('brief_hash')==brief['hash']:
            candidates[job['id']]=_published_configuration(store,brief,job,'analyst' if exact and suffix=='_analyst' else None)
    # Random refinement IDs share the derived-ID syntax. A missing derived job
    # does not stop snapshot fallback; multiple identities never pick a winner.
    return next(iter(candidates.values())) if len(candidates)==1 else None


def describe(store, brief):
    mode={'user':'manual','agent':'ai','import':'imported','example':'example'}.get(brief['author'],'unknown')
    current=_writer(store,brief) if mode=='ai' else None
    if mode=='ai' and current is None and brief['id'].endswith('_evidence') and brief.get('parent_id'):
        parent=store.one('briefs',brief['parent_id'])
        if parent['author']=='agent' and parent['run_id']==brief['run_id'] and parent['markdown']==brief['markdown']:current=_writer(store,parent)
    first=store.rows('SELECT * FROM briefs WHERE run_id=? ORDER BY rowid LIMIT 1',(brief['run_id'],))[0]
    original=_writer(store,first) if first['author']=='agent' else None
    return {'mode':mode,'action':'revision' if brief.get('parent_id') else 'generation','configuration':current,
            'original_configuration':original,'original_version_id':first['id'],'recorded':bool(current)}
