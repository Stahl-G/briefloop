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
    return {'requirements': requirements,
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
