"""Existing workspace sources underpin report tables; calculations are not new sources."""
import json
from .industry_data import IndustryData, prepare_report_data


def prepare_for_run(store, run_id, payload):
    run=store.one('runs',run_id)
    references=set(json.loads(run['requirements']).get('reference_source_ids',[]))
    allowed=set(store.source_ids(run_id))-references
    data=IndustryData.model_validate(payload)
    for record in data.records:
        for sid in dict.fromkeys([record.source_id]+([record.previous_source_id] if record.previous_source_id else [])):
            if sid not in allowed:
                raise ValueError(f'指标 {record.metric} 的来源 {sid} 不是本轮证据；请先登记来源，风格参考不可用于事实表')
            source=store.one('sources',sid)
            if source['status']!='ready':
                raise ValueError(f'指标 {record.metric} 的来源尚未成功读取')
            store.source_text(sid)
    return prepare_report_data(data.model_dump(mode='json'))


def report_details(store, brief):
    detail=json.loads(brief['detail'])
    data=detail.get('report_data')
    prepared=prepare_report_data(data) if data else None
    gaps=list(dict.fromkeys(detail.get('gaps',[])+(prepared['gaps'] if prepared else [])))
    if detail.get('report_data_needs_review'):
        gaps.append('手动修订改变了正文数字；以下仍是原稿数据，下载 Word 暂不附原数据图，请对照核查。')
    return {'version_id':brief['id'],'brief_hash':brief['hash'],'data':prepared,'gaps':gaps,
            'note':'计算表保留生成时的数据和口径。手动改稿不会自动修改原始数据，评价时应对照核查。'}
