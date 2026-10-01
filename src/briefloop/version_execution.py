"""Version-bound writer attribution and explicitly sourced execution settings."""
import json


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


def _draft_fingerprint(draft):
    from hashlib import sha256
    from .models import BriefDraft
    from .store import dump
    return sha256(dump(BriefDraft.model_validate(draft).model_dump(mode='json')).encode()).hexdigest()


def record_copy(store, job, draft, folder):
    """Only called at native write_report's admitted Analyst copy boundary."""
    binding={};accepted={};config=None
    try:
        binding=_object((folder/'conversation.json').read_text(encoding='utf-8'))
        accepted=_object((folder/'draft-accepted.json').read_text(encoding='utf-8'))
        from .analyst_drafts import submitted
        admitted=submitted(store,{'run_id':_object(job['payload']).get('run_id'),
            'packet_root':str(folder/'packet'),'result_file':str(folder/'draft.json')})
        if (binding.get('job_id')==job['id'] and accepted.get('revision')
                and accepted.get('attempt_id')==binding.get('message_id') and admitted==draft):
            config=_message_configuration(store,binding.get('session_id'),binding.get('message_id'))
    except (OSError,ValueError,KeyError):pass
    if config is not None:config['attribution']='admitted_analyst'
    from .document_model import document_hash
    # An unbound copy must also be recorded. Otherwise an older same-text copy
    # could be mistaken for this one after a failed or changed admission.
    store.event(job['id'],'writer_copy',{'run_id':_object(job['payload']).get('run_id'),
                'brief_hash':document_hash(draft['editor_document']),'draft_fingerprint':_draft_fingerprint(draft), 'session_id':binding.get('session_id'),
                'message_id':binding.get('message_id'),'accepted_revision':accepted.get('revision'),'configuration':config})


def publication(store, job, *, draft=None, role=None, revision=False, plain_output=None):
    """Internal runner input for atomic publication, never accepted from tool JSON."""
    payload=_object(job['payload'])
    receipt={'job_id':job['id'], 'configuration':configuration(payload,role)}
    if plain_output is not None:
        from .store import content_hash
        rows=store.rows("SELECT data FROM events WHERE job_id=? AND kind='writer_output' AND json_extract(data,'$.stage')='fast-writing' AND json_extract(data,'$.output_hash')=?",(job['id'],content_hash(plain_output)))
        values={json.dumps(_object(r['data']),sort_keys=True) for r in rows}
        value=_object(next(iter(values))) if len(values)==1 else {}
        return {**receipt, **{k:value[k] for k in ('session_id','message_id') if k in value}, 'configuration':value.get('configuration')}
    if payload.get('agent_backend')=='briefloop-native' and job['kind']=='generate' and not role and not revision:
        # Only the admitted copy operation links a native coordinator's output
        # to an Analyst. File equality or a later conversation is not authorship.
        from .document_model import document_hash
        digest=document_hash(draft['editor_document']) if draft and draft.get('editor_document') else None
        rows=store.rows("SELECT data FROM events WHERE job_id=? AND kind='writer_copy' AND json_extract(data,'$.run_id')=? AND json_extract(data,'$.brief_hash')=? AND json_extract(data,'$.draft_fingerprint')=?",(job['id'],payload.get('run_id'),digest,_draft_fingerprint(draft) if draft else None))
        values={json.dumps(_object(r['data']),sort_keys=True) for r in rows}
        value=_object(next(iter(values))) if len(values)==1 else {}
        receipt.update({k:value[k] for k in ('session_id','message_id') if k in value})
        receipt['configuration']=value.get('configuration')
    return receipt


def chat_publication(store, session_id, message_id):
    return {'session_id':session_id,'message_id':message_id,
            'configuration':_message_configuration(store,session_id,message_id,active=True)}


def record_plain_output(store, job, stage, text, session_id, message_id):
    """Called by the trusted transport when copying this message's final text."""
    from .store import content_hash
    value=_message_configuration(store,session_id,message_id)
    store.event(job['id'],'writer_output',{'stage':stage,'output_hash':content_hash(text),
        'session_id':session_id,'message_id':message_id,'configuration':value})


def evidence_publication(store, parent):
    return {'configuration':_writer(store,parent)}


def insert_receipt(connection, version_id, brief_hash, writer=None):
    """Called only in the transaction branch inserting the new version itself.

    Unknown is explicit and immutable too: retries cannot retroactively claim
    somebody else's version or attach the current message to restored output.
    """
    from .store import dump,now
    writer=writer or {}
    data={'version_id':version_id,'brief_hash':brief_hash,'configuration':writer.get('configuration'),'binding':'atomic_publication_v1'}
    data.update({key:writer[key] for key in ('session_id','message_id') if writer.get(key)})
    connection.execute("INSERT INTO events(job_id,kind,data,created) VALUES(?,'writer_version',?,?)",(writer.get('job_id'),dump(data),now()))


def _writer(store, brief):
    receipts=store.rows("SELECT job_id,data FROM events WHERE kind='writer_version' AND json_extract(data,'$.version_id')=?",(brief['id'],))
    valid=[(r['job_id'],_object(r['data'])) for r in receipts if _object(r['data']).get('brief_hash')==brief['hash'] and _object(r['data']).get('binding')=='atomic_publication_v1']
    identities={(job,data.get('session_id'),data.get('message_id')) for job,data in valid}
    values={json.dumps(data.get('configuration'),sort_keys=True) for _,data in valid}
    # Historical filenames and source snapshots do not prove which execution
    # won publication. No immutable receipt means unknown, never today's default.
    return json.loads(next(iter(values))) if len(identities)==len(values)==1 else None


def describe(store, brief):
    mode={'user':'manual','agent':'ai','import':'imported','example':'example'}.get(brief['author'],'unknown')
    current=_writer(store,brief) if mode=='ai' else None
    first=store.rows('SELECT * FROM briefs WHERE run_id=? ORDER BY rowid LIMIT 1',(brief['run_id'],))[0]
    original=_writer(store,first) if first['author']=='agent' else None
    return {'mode':mode,'action':'revision' if brief.get('parent_id') else 'generation','configuration':current,
            'original_configuration':original,'original_version_id':first['id'],'recorded':bool(current)}
