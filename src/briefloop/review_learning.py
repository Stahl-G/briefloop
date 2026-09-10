"""Feed verified revisions into the existing WikiSkill feedback queue."""
import hashlib
import json
from .store import dump,now


def record_verified_corrections(store,review_id):
    rows=store.rows('SELECT * FROM reviews WHERE id=?',(review_id,))
    if not rows or rows[0]['status']!='complete' or not rows[0]['result']:return []
    from .review import ReviewOutput, _packet, get_review
    review=get_review(store,review_id);result=ReviewOutput.model_validate(review['result']).model_dump();decisions={}
    if result['status']!='complete':return []
    if result['version_id']!=review['version_id'] or result['fingerprint']!=review['fingerprint']:
        raise ValueError('学习输入未绑定已接纳 Review')
    # Historical replay validates the exact packet, not today's source set or
    # current responses. A later disclosure cannot rewrite a verified event.
    packet,target_data,_=_packet(store,review)
    history=json.loads((packet/'history/responses.json').read_text())
    response_rows={row['id']:row for row in history if row['version_id']==review['version_id']}
    versions={row['id']:row for row in json.loads((packet/'history/versions.json').read_text())}
    target_hash=review['data']['files']['target.json'];evidence=target_data['evidence']
    for check in result.get('response_checks',[]):decisions[check['response_id']]=(check['decision'],check['reason'])
    for finding in result.get('findings',[]):
        if finding.get('response_to') and finding.get('resolution'):
            decisions.setdefault(finding['response_to'],(finding['resolution'],finding.get('evidence','')))
    recorded=[]
    for response_id,(decision,reason) in decisions.items():
        if decision not in ('resolved','dismissed_with_evidence'):continue
        response=response_rows.get(response_id)
        if not response:raise ValueError('已复核的处理说明不在固定核查包中')
        value=response['data'];finding=response['finding_data']
        before=versions.get(response['finding_version']);after=versions.get(response['version_id'])
        if not before or not after or after['hash']!=target_data['brief_hash']:
            raise ValueError('核查包缺少对应修订前后版本')
        # Avoid teaching the same resolved response again on every later review.
        identity='feedback_review_'+hashlib.sha256(dump([response_id,decision,after['hash']]).encode()).hexdigest()[:24]
        kind=('review_disagreement' if decision=='dismissed_with_evidence' else
              'traceability_revision' if finding['kind']=='missing_binding' else 'verified_revision')
        payload={'kind':'verified_review_revision','change_kind':kind,'before':before['id'],'after':after['id'],
                 'before_hash':before['hash'],'after_hash':after['hash'],'finding_id':response['finding_id'],
                 'before_text':before['markdown'],'after_text':after['markdown'],
                 'requirements':target_data.get('requirements_input',target_data['requirements']),
                 'finding':finding,'response_id':response_id,'response':value,'review_id':review_id,
                 'review_decision':decision,'verified_reason':reason,'evidence':evidence,
                 'sources':target_data['sources'],'source_updates':target_data.get('source_updates',[]),
                 'source_timing':target_data.get('source_timing',[]),'source_update_checks':result.get('conflict_checks',[]),
                 'assessments':[{'version_id':after['id'],'assessment':result['assessment']}],
                 'execution_records':json.loads((packet/'history/executions.json').read_text()),
                 'review_fingerprint':review['fingerprint'],'evidence_snapshot_hash':target_hash,
                 'note':'仅学习经复核的修订方法。verified_revision未判定为原稿事实错误；正常来源更新与correction须按明确时间/更正依据区分。未决怀疑不属于本事件。'}
        with store.tx() as c:
            c.execute('INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?,?)',(identity,after['id'],'review_correction',dump(payload),None,now()))
        recorded.append(identity)
    return recorded


def source_snapshot(store,run_id,*,source_ids=None):
    from .media import source_files
    items=[]
    allowed=set(store.source_ids(run_id))
    selected=allowed if source_ids is None else set(source_ids)
    if not selected.issubset(allowed):raise ValueError('来源快照包含其他任务的来源')
    for sid in sorted(selected):
        source,_,original=source_files(store,sid);store.source_text(sid)
        items.append({'source_id':sid,'text_hash':source['hash'],
                      'original_hash':hashlib.sha256(original.read_bytes()).hexdigest() if original else None})
    return items
