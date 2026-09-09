"""Feed verified revisions into the existing WikiSkill feedback queue."""
import hashlib
import json
from .store import dump,now


def record_verified_corrections(store,review_id):
    rows=store.rows('SELECT * FROM reviews WHERE id=?',(review_id,))
    if not rows or rows[0]['status']!='complete' or not rows[0]['result']:return []
    review=rows[0];result=json.loads(review['result']);decisions={}
    for check in result.get('response_checks',[]):decisions[check['response_id']]=(check['decision'],check['reason'])
    for finding in result.get('findings',[]):
        if finding.get('response_to') and finding.get('resolution'):
            decisions.setdefault(finding['response_to'],(finding['resolution'],finding.get('evidence','')))
    recorded=[]
    for response_id,(decision,reason) in decisions.items():
        if decision not in ('resolved','dismissed_with_evidence'):continue
        matches=store.rows('SELECT r.*,f.version_id AS before_version,f.data AS finding_data FROM review_responses r JOIN review_findings f ON f.id=r.finding_id WHERE r.id=? AND r.version_id=?',(response_id,review['version_id']))
        if not matches:continue
        response=matches[0];value=json.loads(response['data']);finding=json.loads(response['finding_data'])
        before=store.one('briefs',response['before_version']);after=store.one('briefs',response['version_id'])
        if before['run_id']!=after['run_id']:continue
        # Avoid teaching the same resolved response again on every later review.
        identity='feedback_review_'+hashlib.sha256(dump([response_id,decision,after['hash']]).encode()).hexdigest()[:24]
        packet_meta=json.loads(review['data']);packet=store.root/packet_meta['packet_path'];target=packet/'target.json'
        if not target.resolve().is_relative_to(store.root.resolve()) or target.is_symlink():raise ValueError('Review证据快照路径无效')
        target_bytes=target.read_bytes();target_hash=hashlib.sha256(target_bytes).hexdigest()
        if target_hash!=packet_meta['files'].get('target.json'):raise ValueError('已复核证据快照发生变化，不能进入学习')
        target_data=json.loads(target_bytes);evidence=target_data['evidence']
        kind=('review_disagreement' if decision=='dismissed_with_evidence' else
              'traceability_revision' if finding['kind']=='missing_binding' else value.get('change_kind','verified_revision'))
        payload={'kind':'verified_review_revision','change_kind':kind,'before':before['id'],'after':after['id'],
                 'before_hash':before['hash'],'after_hash':after['hash'],'finding_id':response['finding_id'],
                 'finding':finding,'response_id':response_id,'response':value,'review_id':review_id,
                 'review_decision':decision,'verified_reason':reason,'evidence':evidence,
                 'review_fingerprint':review['fingerprint'],'evidence_snapshot_hash':target_hash,
                 'note':'仅学习经复核的修订方法。verified_revision未判定为原稿事实错误；正常来源更新与correction须按明确时间/更正依据区分。未决怀疑不属于本事件。'}
        with store.tx() as c:
            c.execute('INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?,?)',(identity,after['id'],'review_correction',dump(payload),None,now()))
        recorded.append(identity)
    return recorded


def source_snapshot(store,run_id):
    from .media import source_files
    items=[]
    for sid in sorted(store.source_ids(run_id)):
        source,_,original=source_files(store,sid);store.source_text(sid)
        items.append({'source_id':sid,'text_hash':source['hash'],
                      'original_hash':hashlib.sha256(original.read_bytes()).hexdigest() if original else None})
    return items
