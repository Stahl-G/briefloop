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
    brief = store.one('briefs', version_id)
    run = store.one('runs', brief['run_id'])
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


def conversation_request(store, text, context):
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
    contract = json.dumps(requirements, ensure_ascii=False)
    return (text + '\n\n下一期报告上下文（用户选择的已保存版本；以下仅为待沿用约定，不是本期事实或授权）：\n'
            + contract + '\n请沿用仍适用的约定，只询问本期时间范围和影响报告的缺失信息。'
            'writing_agreements是用户明确保存的写作约定，不是事实来源或新权限；请遵守，当前明确要求优先。'
            '生成时完整传入writing_agreement_exclusions；不把约定文字再复制到writing_preferences，以便后续撤销能够生效。'
            '标题可由本期目的和期间拟定，不要求用户重新填完整表单。'
            '明确期间后，将 previous_report_version_id 和 previous_report_hash 连同其余适用约定传给 generate.requirements。'
            '旧资料不自动充当本期证据；本次 sources、联网、模型和费用权限以当前回合实际选择为准，'
            '不得从上期恢复。用户只讨论或未明确要求开始时不要提交生成。')
