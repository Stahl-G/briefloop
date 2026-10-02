"""Read-only report-card projection. Never return raw runtime logs or commands."""
import json
import re
from .research_plan import frozen, pending_requests
from .research_budget import snapshot
from .task_labels import label as task_label
from .progress import public_failure

ACTIVE = ('queued', 'running')


def public_text(value, limit=240):
    text = str(value or '')
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    text = re.sub(r'(?:sk-|pypi-)[\w-]{12,}', '[凭据已隐藏]', text)
    text = re.sub(r'(?:/Users/|/home/|/var/|[A-Z]:\\)\S+', '[本地路径]', text)
    return text[:limit]


def summary(store, job_id):
    job = store.one('jobs', job_id)
    payload = json.loads(job['payload'])
    run_id = payload.get('run_id')
    if not run_id and payload.get('version_id'):
        run_id = store.one('briefs', payload['version_id'])['run_id']
    run = store.one('runs', run_id) if run_id else None
    req = json.loads(run['requirements']) if run else {}
    children = store.rows("SELECT * FROM jobs WHERE json_extract(payload,'$.parent_job_id')=? ORDER BY rowid", (job_id,))
    related = [job] + children
    ids = [j['id'] for j in related]
    events = store.rows('SELECT * FROM events WHERE job_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY seq DESC LIMIT 100', ids)[::-1]
    progress_event = next((e for e in reversed(events) if e['kind'] == 'runtime_progress'), None)
    p = json.loads(progress_event['data']) if progress_event else {}
    running = job['status'] in ACTIVE
    child = next((j for j in reversed(children) if j['status'] in ACTIVE), None) if running else None
    stage = public_text(p.get('stage')) or ('等待开始' if job['status'] == 'queued' else task_label(job['kind'], '处理任务'))
    if running and req.get('completion_mode') in ('fast','fast_web') and job['kind']=='generate':
        stage='直接阅读材料并写作'
    if child:
        stage = task_label(child['kind'], '子任务') + ('等待开始' if child['status'] == 'queued' else '进行中')
    if not running:
        stage = {'complete': '任务已结束', 'failed': '任务未完成', 'cancelled': '任务已停止', 'interrupted': '任务已中断'}.get(job['status'], '任务状态待确认')
    # Review/export cards point at their actual input, not an unrelated newer edit.
    result = json.loads(job['result']) if job.get('result') else {}
    version_id = result.get('version_id') or payload.get('version_id')
    if version_id:
        briefs = store.rows('SELECT id,detail,created FROM briefs WHERE id=? AND run_id=?', (version_id, run_id))
    else:
        briefs = store.rows('SELECT id,detail,created FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run_id,)) if run else []
    brief = briefs[0] if briefs else None
    if running and not brief and stage == '正文已保存，正在准备评分':
        stage = '正文已生成，正在保存稿件'
    title = req.get('title') or (json.loads(brief['detail']).get('title') if brief else '') or task_label(job['kind'], '报告任务')
    sources = []
    if run:
        for sid in store.source_ids(run_id):
            s = store.one('sources', sid)
            sources.append({'id': sid, 'name': public_text(s['name']), 'status': s['status']})
    plan = frozen(store, run_id) or {} if run else {}
    rounds = sorted((plan.get('rounds') or {}).values(), key=lambda r: r.get('index', 0))
    opened = [r for r in rounds if r.get('status') in ('active', 'closed')]
    timeline = []
    for r in opened:
        done = r['status'] == 'closed'
        timeline.append({'label': f"第 {r['index']} 轮研究" + ('已收束' if done else '进行中' if running else '未收束'),
                         'detail': public_text(((r.get('outcome') or {}).get('summary') or '')
                                               + (('；提前收束理由：' + r['outcome']['early_stop_reason']) if (r.get('outcome') or {}).get('early_stop_reason') else '')),
                         'status': 'done' if done else 'active' if running else 'recorded', 'started': r.get('created'), 'ended': r.get('closed'), 'time': r.get('closed') or r.get('created')})
    if run:
        from .scout_coverage import view as scout_view
        labels = {'planned': '未派发', 'dispatched': '尚未交接', 'failed': '执行未完成', 'skipped': '已说明跳过'}
        for task in scout_view(store, run_id)['execution_gaps']:
            timeline.append({'label': f"第 {task['round_index']} 轮 {task['slot_id']}：" + labels[task['status']],
                             'detail': public_text(task['assignment'] + ('；' + task['reason'] if task['reason'] else '')),
                             'status': 'error' if task['status'] == 'failed' else 'warn', 'not_started': task['status'] == 'planned', 'time': ((plan.get('rounds') or {}).get(task['round_id']) or {}).get('closed') or run['created']})
    event_labels = {'fast_search':'规划并检索公开来源', 'fast_sources':'读取选中的网页原文', 'fast_writing':'直接阅读材料并写作', 'fast_evidence':'后台补充原文依据', 'fast_evidence_preserved':'原版依据保留，用户修改优先', 'checks_deferred': '初稿已保存，完整核验待继续', 'checks_started': '开始完整检查', 'checks_finished': '检查阶段已结束', 'revision_required': '审阅已返回', 'draft_missing_resume': '继续完成尚未保存的初稿',
                    'assessment_failed': '评分未完成，已有稿件保留'}
    revision_labels = {'writing': '正在按审阅意见修订', 'checking': '修订稿已保存，正在复核',
                       'repairing_metadata': '正在修复依据关联与处理说明', 'metadata_repaired': '依据关联已修复'}
    revision_phase = None
    for e in events:
        data = json.loads(e['data'])
        label = event_labels.get(e['kind'])
        if running and req.get('completion_mode')=='fast_web' and e['kind'] in ('fast_search','fast_sources','fast_writing'):
            stage=label
        if req.get('completion_mode') in ('fast','fast_web') and e['kind']=='checks_deferred':label='快速初稿已保存，后台检查待开始'
        if running and e['kind']=='fast_evidence' and not store.rows('SELECT id FROM assessments WHERE version_id=?',(payload.get('version_id',''),)):
            stage='后台补充依据和评价'
        if e['kind'] == 'revision_progress': label = revision_labels.get(data.get('stage'))
        if e['kind'] == 'fact_check':
            label = {'dispatch': '事实核查任务已提交', 'finish': '事实核查阶段已结束',
                     'incomplete': '事实核查未完成', 'admit_refused': '本次未能启动事实核查'}.get(data.get('action'))
        if running and not child and e['kind'] == 'revision_progress' and label and (not progress_event or e['seq'] > progress_event['seq']):
            stage = label
            revision_phase = True
        history_label = {'writing': '按审阅意见修订已开始', 'checking': '修订稿已保存，进入复核', 'repairing_metadata': '依据关联与处理说明进入修复'}.get(data.get('stage'), label) if e['kind'] == 'revision_progress' else label
        detail = ''
        if e['kind'] == 'plain_isolation':
            # Fast plain turns state how they were held to their materials.
            from .plain_isolation import public_record, summary as isolation_summary
            isolation = public_record(data)
            label = history_label = {'research': '检索规划', 'evidence': '补充依据'}.get(data.get('phase'), '快速写作') + (
                '：配置要求关闭工具，已记录引擎报告的活动' if isolation['level'] == 'enforced' else '：只读权限不等于材料隔离' if isolation['level'] == 'restricted' else '：引擎工具未关闭，已记录使用情况')
            detail = public_text(isolation_summary(isolation))
        if e['kind'] == 'fast_unsupported':
            label = history_label = '找不到原文依据的结论已单独列出'
            detail = public_text(data.get('message'))
        if label: timeline.append({'label': history_label, 'detail': detail, 'status': 'recorded', 'time': e['created']})
    if run:
        from .reconciliation import _stored
        records = _stored(store, run_id)
        if records:
            record = max(records, key=lambda r: r.get('created', ''))
            timeline.append({'label': '写前证据对照已记录', 'detail':
                f"已检查 {len(record.get('examined_claim_ids', []))} 条来源陈述，未检查 {len(record.get('unexamined_claim_ids', []))} 条；{len(record.get('relations', []))} 条关系记录",
                'status': 'recorded', 'time': record.get('created')})
        checks = store.rows('SELECT data,version_id,created FROM fact_checks WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run_id,))
        if checks:
            check = json.loads(checks[0]['data'])
            same = brief and checks[0]['version_id'] == brief['id']
            timeline.append({'label': '事实核查候选已保存', 'detail':
                f"{len(check.get('candidates', []))} 条候选，{len(check.get('unchecked', []))} 条未检查。候选仍需独立审阅。" + ('' if same else '此记录对应历史稿件。'),
                'status': 'recorded', 'time': checks[0]['created']})
    timeline.sort(key=lambda item: item.get('time') or '')
    agents = [{'id': public_text(a.get('id')), 'role': public_text(a.get('role'), 60),
               'task': public_text(a.get('task')), 'activity': public_text(a.get('activity')),
               'status': a.get('status', 'unknown'), 'started': a.get('started'), 'ended': a.get('ended')} for a in p.get('agents', [])]
    requests = list(pending_requests(store, run_id).values()) if run else []
    searches = [r for r in requests if r.get('operation') == 'search']
    search_counts = {s: sum(r.get('status') == s for r in searches) for s in ('completed', 'failed', 'reserved')}
    budget = snapshot(store, run_id) if run else None
    # Legacy rail uses file existence; do not treat ended/failed Scouts as completion.
    stages = p.get('stages') or []
    stages = [{'id': s.get('id'), 'label': public_text(s.get('label'), 70), 'status': s.get('status')} for s in stages]
    if revision_phase:
        stages = [{'id': 'revision', 'label': '修订与复核', 'status': 'active'}]
    if child:
        stages = [{'id': child['kind'], 'label': task_label(child['kind'], '子任务'), 'status': 'active'}]
    if not running:
        for s in stages:
            if s['status'] == 'active': s['status'] = 'paused' if job['status'] != 'complete' else 'recorded'
    for s in stages:
        if s['id'] == 'research' and not brief and (not opened or any(r['status'] == 'active' for r in opened)) and s['status'] == 'done': s['status'] = 'active' if running else 'paused'
    started_event = next((e for e in reversed(events) if e['kind'] == 'runtime_started'), None)
    session_id = json.loads(started_event['data']).get('session_id') if started_event and running else None
    activity_times = [x for x in [p.get('last_activity'), progress_event['created'] if progress_event else None] + [r.get('updated') or r.get('created') for r in requests] if x]
    starts=store.rows("SELECT created FROM events WHERE job_id=? AND kind='job_started' ORDER BY seq DESC LIMIT 1",(job_id,))
    own_start=starts[0]['created'] if starts else None
    if req.get('completion_mode')=='draft_first' and job['kind']=='generate':
        if brief:stage='初稿已保存，完整核验待继续' if not running else '正在完成初稿'
        for item in stages:
            if item.get('id')=='evaluate':item.update(label='完整核验待继续',status='pending')
    if running and not child and p.get('runtime_notice'):
        stage=public_text(p.get('stage')) or stage
    from .conflicts import for_run as run_conflicts
    conflicts = [{'text': public_text(item['data'].get('description'))} for item in run_conflicts(store, run_id) if item['status'] != 'resolved'] if run else []
    return {'session_id': session_id, 'job_id': job_id, 'run_id': run_id, 'status': job['status'], 'title': public_text(title, 160),
            'stage': stage, 'queued_at': job['created'], 'started': own_start, 'ended': job['updated'] if not running else None,
            'last_activity': max(activity_times) if activity_times else None,
            'tier': plan.get('preset_id') or req.get('research_tier'),'completion_mode':req.get('completion_mode','standard'),
            'round': opened[-1]['index'] if opened else None,
            'sources': sources, 'source_count': len(sources), 'search_counts': search_counts,
            'search_metered': bool(budget and budget['used']['search_requests'] is not None),
            'budget_remaining': budget['remaining'] if budget else None,
            'gaps': [{'text': public_text(g.get('question') or g.get('description') or g.get('reason'))} for r in opened for g in r.get('gaps', [])],
            'conflicts': conflicts, 'agents': agents, 'stages': stages, 'timeline': timeline[-12:],
            'version_id': brief['id'] if brief else None,
            'error': (public_failure(job.get('error')) or '任务未完成，请打开任务查看错误与恢复选项。') if job['status'] in ('failed', 'interrupted') else None}
