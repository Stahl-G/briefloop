"""Reuse an explicitly selected report's contract, never its current facts (#865)."""
import json


CONTRACT_FIELDS = (
    'objective', 'audience', 'organization', 'industry', 'language', 'extent',
    'writing_mode', 'report_profile', 'workflow_id', 'workflow_variant',
    'template_id', 'sections', 'manual_sections', 'key_questions',
    'writing_preferences', 'target_words', 'max_words', 'length_mode',
    'length_requirement', 'report_timezone',
)


def prepare(store, version_id):
    """Read-only preview; opening it creates no source, run, job or authorization."""
    from .writing_agreements import report_version
    brief = report_version(store, version_id)
    if not brief:raise ValueError('报告版本不存在，请重新选择报告')
    run = store.one('runs', brief['run_id'])
    if not run:raise ValueError('报告任务不存在，请重新选择报告')
    previous = json.loads(run['requirements'])
    requirements = {key: previous[key] for key in CONTRACT_FIELDS if key in previous}
    reader = None
    if previous.get('reader_id'):
        from .readers import profile
        try:
            reader = profile(store, previous['reader_id'])
            requirements['reader_id'] = reader['id']
        except ValueError:
            pass  # Archived readers are not silently reactivated.
    requirements.update(title='', period='', period_start='', period_end='', report_date='',
                        allow_web=False, fact_check=False, reference_source_ids=[],
                        previous_report_version_id=brief['id'], previous_report_hash=brief['hash'])
    from .writing_agreements import listing
    agreements=listing(store,version_id)
    return {'requirements': requirements, 'writing_agreements':agreements,
            'previous': {'version_id': brief['id'], 'run_id': run['id'], 'hash': brief['hash'],
                         'title': json.loads(brief['detail']).get('title', previous.get('title', ''))},
            'source_ids': [], 'reader': reader,
            'notice': '沿用已保存的要求与写作偏好；请填写本期标题和时间范围，核对要求中的历史条件，并重新选择本期材料。旧来源、评分、联网及费用授权不会自动沿用。'}


def validate_origin(store, requirements):
    version = requirements.previous_report_version_id
    digest = requirements.previous_report_hash
    if not version and not digest:
        return
    if not version or not digest:
        raise ValueError('下一期需要明确的往期版本与内容哈希')
    brief = store.one('briefs', version)
    if brief['hash'] != digest:
        raise ValueError('往期报告内容已变化，请重新选择复用版本')
    if not (requirements.period.strip() or (requirements.period_start and requirements.period_end)):
        raise ValueError('开始下一期前请确认本期时间范围')


def conversation_request(store, text, context, *, compact=False):
    """Bind an unsent next-period draft to one saved version for any runtime.

    Only the reusable contract is exposed, never previous sources or a prior
    permission snapshot. Opening the UI cannot enqueue work.
    """
    if not isinstance(context, dict) or not isinstance(context.get('version_id'), str):
        raise ValueError('请选择要沿用的往期报告')
    data = prepare(store, context['version_id'])
    if context.get('hash') != data['previous']['hash']:
        raise ValueError('往期报告内容已变化，请重新选择复用版本')
    requirements=data['requirements']
    requirements['writing_agreement_exclusions']=context.get('writing_agreement_exclusions') or []
    from .models import Requirements
    from .writing_agreements import freeze
    checked=Requirements.model_validate({**requirements,'title':'下一期'})
    freeze(store,checked)
    requirements['writing_agreements']=checked.writing_agreements
    if compact:
        reference={'previous_report_version_id':context['version_id'],'previous_report_hash':context['hash'],
                   'writing_agreement_exclusions':checked.writing_agreement_exclusions}
        return (text+'\n\n本轮仍关联下一期报告：'+json.dumps(reference,ensure_ascii=False)
                +'\n先用 workspace-action 的 next_report（version_id 为上述往期版本）读取当前可沿用约定；不重复附加整份要求。'
                '生成请求必须明确带上相同的 previous_report_version_id、previous_report_hash 和当前 session_id；跳过项由服务端带入。'
                '如本轮改做无关报告，请提示用户取消关联，不自行把它挂入旧系列。旧资料、事实与授权不自动沿用。')
    contract = json.dumps(requirements, ensure_ascii=False)
    return (text + '\n\n下一期报告上下文（用户选择的已保存版本；以下仅为待沿用约定，不是本期事实或授权）：\n'
            + contract + '\n请沿用仍适用的约定，只询问本期时间范围和影响报告的缺失信息。'
            'writing_agreements是用户明确保存的写作约定，不是事实来源或新权限；请遵守，当前明确要求优先。'
            '生成时带上当前session_id；本期跳过项由服务端绑定，不把约定文字再复制到writing_preferences，以便后续撤销能够生效。'
            '标题可由本期目的和期间拟定，不要求用户重新填完整表单。'
            '明确期间后，将 previous_report_version_id 和 previous_report_hash 连同其余适用约定传给 generate.requirements。'
            '旧资料不自动充当本期证据；本次 sources、联网、模型和费用权限以当前回合实际选择为准，'
            '不得从上期恢复。用户只讨论或未明确要求开始时不要提交生成。')


def bind_message_context(store, session_id, message_id, context):
    """UI selection is attached to a message before dispatch, including an explicit clear.

    This is state propagation, not proof of the identity of a shell caller.
    Failed sends have no message and cannot affect a later execution.
    """
    from .chat_store import ChatStore
    from .store import dump
    ChatStore(store).session(session_id)
    saved=None
    if context is not None:
        conversation_request(store,'',context)  # Validate version/hash and exclusions.
        saved={key:context.get(key) for key in ('version_id','hash')}
        saved['writing_agreement_exclusions']=context.get('writing_agreement_exclusions') or []
    with store.tx() as c:
        rows=c.execute("SELECT data FROM chat_events WHERE session_id=? AND kind='report/nextContext' AND json_extract(data,'$.message_id')=?",(session_id,message_id)).fetchall()
        value={'message_id':message_id,'context':saved}
        if rows:
            if json.loads(rows[-1]['data'])!=value:raise ValueError('同一消息的下一期选择已变化，请重新发送')
            return
        from .store import now
        c.execute('INSERT INTO chat_events(session_id,kind,data,created) VALUES(?,?,?,?)',
                  (session_id,'report/nextContext',dump(value),now()))


def generation_requirements(store, requirements, session_id):
    """Resolve the UI's selection for the currently executing message, never a queued successor."""
    if not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'"):
        return requirements
    rows=store.rows("""SELECT m.session_id,e.data FROM chat_messages m
        LEFT JOIN chat_events e ON e.session_id=m.session_id AND e.kind='report/nextContext'
            AND json_extract(e.data,'$.message_id')=m.id
        WHERE m.role='user' AND m.status IN ('sending','delivered')
        ORDER BY m.rowid DESC,e.seq DESC""")
    latest={}
    for row in rows:latest.setdefault(row['session_id'],json.loads(row['data'])['context'] if row['data'] else None)
    context=latest.get(session_id)
    selected_origin=requirements.get('previous_report_version_id')
    if context is None:
        # An unrelated CLI request is unaffected by other conversations. Only a
        # request for an actively selected origin without its owner is ambiguous.
        if not session_id and selected_origin and any(value and selected_origin==value['version_id'] for value in latest.values()):
            raise ValueError('下一期选择未绑定到当前执行会话，请使用发起消息的 session_id；未提交生成')
        return requirements
    if selected_origin!=context['version_id'] or requirements.get('previous_report_hash')!=context['hash']:
        raise ValueError('生成请求与本轮关联的往期版本不一致；如需无关的新报告，请先取消关联。未提交生成')
    conversation_request(store,'',context)  # Stale selections fail visibly.
    return {**requirements,'writing_agreement_exclusions':context['writing_agreement_exclusions']}


def context_already_delivered(store, session_id, message_id, context):
    """Compact only after an equivalent selection was actually delivered.

    The compact prompt includes a read-only lookup action, so a runtime handoff
    need not rely on hidden prompts surviving the public-history transfer.
    """
    rows=store.rows("""SELECT e.data FROM chat_events e JOIN chat_messages m
        ON m.id=json_extract(e.data,'$.message_id') AND m.session_id=e.session_id
        WHERE e.session_id=? AND e.kind='report/nextContext' AND m.id!=?
          AND m.status IN ('delivered','completed') ORDER BY m.rowid DESC LIMIT 1""",(session_id,message_id))
    if not rows:return False
    prior=json.loads(rows[0]['data'])['context']
    selected={key:context.get(key) for key in ('version_id','hash')}
    selected['writing_agreement_exclusions']=context.get('writing_agreement_exclusions') or []
    return prior==selected
