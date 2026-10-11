"""Existing workspace sources underpin report tables; calculations are not new sources."""
import json
from datetime import datetime
from .industry_data import IndustryData, prepare_report_data, source_ids


def run_window(requirements):
    """The frozen coverage window as (start, end_exclusive) dates, or None when open-ended."""
    context=requirements.get('time_context') or {}
    if not (context.get('start') and context.get('end_exclusive')):
        return None
    return (datetime.fromisoformat(context['start']).date(),datetime.fromisoformat(context['end_exclusive']).date())


def prepare_for_run(store, run_id, payload):
    run=store.one('runs',run_id)
    requirements=json.loads(run['requirements'])
    references=set(requirements.get('reference_source_ids',[]))
    allowed=set(store.source_ids(run_id))-references
    data=IndustryData.model_validate(payload)
    for sid in source_ids(data):
        if sid not in allowed:
            raise ValueError(f'报告数据的来源 {sid} 不是本轮证据；请先登记来源，风格参考不可用于事实表')
        if store.one('sources',sid)['status']!='ready':
            raise ValueError(f'报告数据的来源 {sid} 尚未成功读取')
        store.source_text(sid)
    return prepare_report_data(data.model_dump(mode='json'),run_window(requirements))


def report_details(store, brief):
    detail=json.loads(brief['detail'])
    data=detail.get('report_data')
    run=store.one('runs',brief['run_id'])
    prepared=prepare_report_data(data,run_window(json.loads(run['requirements'])) if run else None) if data else None
    gaps=list(dict.fromkeys(detail.get('gaps',[])+(prepared['gaps'] if prepared else [])))
    if detail.get('report_data_needs_review'):
        gaps.append('手动修订改变了正文数字；以下仍是原稿数据，下载 Word 暂不附原数据图，请对照核查。')
    return {'version_id':brief['id'],'brief_hash':brief['hash'],'data':prepared,'gaps':gaps,
            'note':'计算表保留生成时的数据和口径。手动改稿不会自动修改原始数据，评价时应对照核查。'}
