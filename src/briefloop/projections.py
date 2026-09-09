"""Read-only source and learning views for the local interface."""
import json


def source_details(store, sid):
    source=store.one('sources',sid)
    provenance=None
    record=store.root/'sources'/f'{sid}.provenance.json'
    if record.exists():provenance=json.loads(record.read_text())
    original=None
    if provenance and provenance.get('original_path'):
        path=(store.root/provenance['original_path']).resolve()
        if path.is_relative_to((store.root/'sources').resolve()) and path.is_file():original=path
    if original is None:
        # Uploaded originals predate provenance sidecars; do not pretend an old
        # downloaded plain-text source is the original web response.
        if not source.get('url'):
            extracted=(store.root/source['path']).resolve()
            for path in sorted((store.root/'sources').glob(sid+'.*')):
                if path.resolve()!=extracted and path.suffix!='.json' and path.is_file():
                    original=path;break
    return source,provenance,original


def learning_candidates(store):
    from wikiskill import feedback_loop
    result=[]
    for job in store.rows("SELECT * FROM jobs WHERE kind='learn' ORDER BY rowid DESC LIMIT 30"):
        root=store.root/'jobs'/job['id']/'study'
        if not (root/'config.json').exists():continue
        try:state=feedback_loop.work(root)
        except (ValueError,OSError,KeyError):continue
        records=[]
        for h in state['history']:
            if h.get('candidate_skill'):
                records.append((h['candidate_skill'],'accepted' if h['accepted'] else 'rejected',h.get('reason',''),h.get('round')))
        pending=state.get('candidate')
        if pending and pending.get('skill') and not any(x[0]['file']==pending['skill']['file'] for x in records):
            reason='候选已生成，尚未完成比较验证；当前仍使用原有技能。'
            if job['status'] in ('cancelled','interrupted','failed'):reason='验证任务已暂停或未完成，候选已保留；当前仍使用原有技能。继续时使用当前模型设置；若模型已改变，会开始新的执行，可能重新完成学习步骤。'
            records.append((pending['skill'],'pending_validation',reason,state.get('round')))
        for record,status,reason,round_number in records:
            path=(root/record['file']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():continue
            markdown=path.read_text()
            title=next((line.lstrip('# ').strip() for line in markdown.splitlines() if line.startswith('# ')),'候选技能')
            result.append({'job_id':job['id'],'job_status':job['status'],'title':title,'markdown':markdown,
                           'status':status,'reason':reason,'round':round_number})
    return {'candidates':result}
