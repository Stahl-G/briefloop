"""Bounded browsing projections. Bodies, evidence and evaluations load on demand."""
import json

PAGE_SIZE = 30
HOT_VERSIONS = 40
_VISIBLE = "r.mode='normal' AND NOT EXISTS (SELECT 1 FROM meta m WHERE m.key='deleted_report:'||r.id)"
_SOURCE_COUNT = "(SELECT count(*) FROM (SELECT value AS id FROM json_each(r.source_ids) UNION SELECT source_id FROM run_sources WHERE run_id=r.id))"
_COLUMNS = """b.rowid AS position,b.id,b.run_id,b.parent_id,b.author,b.hash,b.created,
 json_object('title',substr(json_extract(b.detail,'$.title'),1,300)) AS detail,
 substr(b.markdown,1,400) AS excerpt,
 substr(json_extract(r.requirements,'$.objective'),1,400) AS objective,
 json_extract(r.requirements,'$.workflow_id') AS workflow_id,
 json_extract(r.requirements,'$.workflow_variant') AS workflow_variant,
 json_extract(r.requirements,'$.template_id') AS template_id,
 (SELECT id FROM briefs WHERE run_id=b.run_id ORDER BY rowid DESC LIMIT 1) AS latest_version_id,
 (SELECT count(*) FROM briefs WHERE run_id=b.run_id) AS version_count,
 (SELECT count(*) FROM briefs WHERE run_id=b.run_id AND author='user') AS user_version_count,
 (SELECT id FROM assessments WHERE version_id=b.id ORDER BY rowid DESC LIMIT 1) AS assessment_id,
 (SELECT json_extract(data,'$.status') FROM assessments WHERE version_id=b.id ORDER BY rowid DESC LIMIT 1) AS assessment_status,
 (SELECT json_extract(data,'$.overall') FROM assessments WHERE version_id=b.id ORDER BY rowid DESC LIMIT 1) AS assessment_overall,
 """ + _SOURCE_COUNT + " AS source_count"
_STATUS = """CASE
 WHEN (SELECT json_extract(data,'$.status') FROM assessments WHERE version_id=b.id ORDER BY rowid DESC LIMIT 1)='complete' THEN 'scored'
 WHEN EXISTS(SELECT 1 FROM jobs j WHERE j.kind='release' AND j.status='complete' AND json_extract(j.payload,'$.version_id')=b.id) THEN 'released'
 WHEN EXISTS(SELECT 1 FROM jobs j WHERE j.kind IN ('generate','revise','assess','review') AND j.status IN ('queued','running') AND (json_extract(j.payload,'$.run_id')=b.run_id OR json_extract(j.payload,'$.version_id')=b.id)) THEN 'running'
 ELSE 'draft' END"""


def _limit(value):
    return max(1, min(int(value), 100))


def versions(store, run_id, *, cursor='', limit=PAGE_SIZE, before=''):
    args = [run_id]
    where = _VISIBLE + ' AND b.run_id=?'
    if cursor:
        where += ' AND b.rowid<?'; args.append(int(cursor))
    if before:
        where += ' AND b.rowid<(SELECT rowid FROM briefs WHERE id=? AND run_id=?)'; args.extend((before,run_id))
    limit = _limit(limit)
    rows = store.rows(f'SELECT {_COLUMNS} FROM briefs b JOIN runs r ON r.id=b.run_id WHERE {where} ORDER BY b.rowid DESC LIMIT ?', (*args,limit+1))
    return _page(rows,limit)


def _page(rows, limit):
    more = len(rows)>limit
    items = rows[:limit]
    return {'items':items,'next_cursor':str(items[-1]['position']) if more else None,'has_more':more}


def reports(store, *, cursor='', limit=PAGE_SIZE, q='', status='', days='', sources='', source_id=''):
    where = [_VISIBLE,'b.rowid=(SELECT max(rowid) FROM briefs WHERE run_id=r.id)']; args=[]
    if cursor:where.append('b.rowid<?');args.append(int(cursor))
    text=str(q or '').strip().lower()
    if len(text)>200:raise ValueError('搜索词最多 200 个字符')
    if text:
        where.append("(instr(lower(json_extract(b.detail,'$.title')),?)>0 OR instr(lower(json_extract(r.requirements,'$.objective')),?)>0 OR EXISTS(SELECT 1 FROM briefs old WHERE old.run_id=b.run_id AND instr(lower(old.markdown),?)>0))")
        args.extend((text,text,text))
    if status:
        if status not in ('scored','released','running','draft'):raise ValueError('无效报告状态')
        where.append(f'({_STATUS})=?');args.append(status)
    if days:where.append("julianday(b.created)>=julianday('now')-?");args.append(max(0,int(days)))
    if source_id:
        where.append('(EXISTS(SELECT 1 FROM json_each(r.source_ids) WHERE value=?) OR EXISTS(SELECT 1 FROM run_sources WHERE run_id=r.id AND source_id=?))');args.extend((source_id,source_id))
    if sources in ('yes','no'):where.append(_SOURCE_COUNT+('>0' if sources=='yes' else '=0'))
    limit=_limit(limit)
    rows=store.rows(f"SELECT {_COLUMNS},({_STATUS}) AS report_status FROM briefs b JOIN runs r ON r.id=b.run_id WHERE {' AND '.join(where)} ORDER BY b.rowid DESC LIMIT ?",(*args,limit+1))
    return _page(rows,limit)


def hot_state(store, jobs, *, run_id='', version_id='', pending_run=''):
    # Recent versions preserve old clients' basic shape; only an explicit current
    # report and the bounded job window add pins. No historical body is decoded.
    recent=store.rows(f'SELECT {_COLUMNS} FROM briefs b JOIN runs r ON r.id=b.run_id WHERE {_VISIBLE} ORDER BY b.rowid DESC LIMIT ?',(HOT_VERSIONS,))
    pinned_runs={value for value in (run_id,pending_run) if value}
    pinned_versions={version_id} if version_id else set()
    for job in jobs:
        payload=json.loads(job['payload']);result=json.loads(job['result'] or '{}')
        if payload.get('run_id'):pinned_runs.add(payload['run_id'])
        for value in (payload.get('version_id'),payload.get('base_version'),result.get('version_id')):
            if value:pinned_versions.add(value)
    if pinned_versions:
        linked=store.rows('SELECT DISTINCT run_id FROM briefs WHERE id IN ('+','.join('?' for _ in pinned_versions)+')',sorted(pinned_versions))
        pinned_runs.update(row['run_id'] for row in linked)
    # Fetch pinned latest versions together; old active tasks cannot disappear.
    clauses=[];args=[]
    if pinned_runs:
        clauses.append('b.rowid IN (SELECT max(rowid) FROM briefs WHERE run_id IN ('+','.join('?' for _ in pinned_runs)+') GROUP BY run_id)');args.extend(sorted(pinned_runs))
    if pinned_versions:
        clauses.append('b.id IN ('+','.join('?' for _ in pinned_versions)+')');args.extend(sorted(pinned_versions))
    if clauses:
        recent += store.rows(f"SELECT {_COLUMNS} FROM briefs b JOIN runs r ON r.id=b.run_id WHERE {_VISIBLE} AND ({' OR '.join(clauses)})",args)
    briefs=sorted({b['id']:b for b in recent}.values(),key=lambda b:b['position'],reverse=True)
    run_ids=pinned_runs|{b['run_id'] for b in briefs}
    runs=[]
    if run_ids:
        runs=store.rows("SELECT r.id,r.created,r.mode,"+_SOURCE_COUNT+" AS source_count,json_array_length(r.source_ids) AS initial_source_count,json_object('title',substr(json_extract(r.requirements,'$.title'),1,300),'objective',substr(json_extract(r.requirements,'$.objective'),1,400),'workflow_id',json_extract(r.requirements,'$.workflow_id'),'workflow_variant',json_extract(r.requirements,'$.workflow_variant'),'template_id',json_extract(r.requirements,'$.template_id'),'target_minutes',json_extract(r.requirements,'$.target_minutes'),'hard_timeout_minutes',json_extract(r.requirements,'$.hard_timeout_minutes'),'allow_web',json_extract(r.requirements,'$.allow_web')) AS requirements FROM runs r WHERE "+_VISIBLE+' AND r.id IN ('+','.join('?' for _ in run_ids)+') ORDER BY r.rowid DESC',sorted(run_ids))
    assessments=[{'id':b['assessment_id'],'version_id':b['id'],'summary':True,'data':json.dumps({'status':b['assessment_status'],'overall':b['assessment_overall']})} for b in briefs if b['assessment_id']]
    catalog=store.rows("SELECT (SELECT max(rowid) FROM briefs) AS version_revision,(SELECT max(rowid) FROM assessments) AS assessment_revision,(SELECT max(updated) FROM jobs) AS job_revision,(SELECT count(*) FROM meta WHERE key LIKE 'deleted_report:%') AS deletion_revision,(SELECT count(*) FROM runs r WHERE "+_VISIBLE+" AND EXISTS(SELECT 1 FROM briefs WHERE run_id=r.id)) AS total")[0]
    usage=store.rows("SELECT source_id,count(*) AS report_count FROM (SELECT r.id,value AS source_id FROM runs r,json_each(r.source_ids) WHERE "+_VISIBLE+" AND EXISTS(SELECT 1 FROM briefs WHERE run_id=r.id) UNION SELECT r.id,rs.source_id FROM runs r JOIN run_sources rs ON rs.run_id=r.id WHERE "+_VISIBLE+" AND EXISTS(SELECT 1 FROM briefs WHERE run_id=r.id)) GROUP BY source_id")
    return {'briefs':briefs,'runs':runs,'assessments':assessments,'report_catalog':catalog,'source_report_counts':{row['source_id']:row['report_count'] for row in usage}}


def context(store, version_id):
    brief=store.rows('SELECT id,run_id FROM briefs WHERE id=?',(version_id,))
    if not brief:raise ValueError('报告不存在')
    run_id=brief[0]['run_id']
    if run_id in store.deleted_reports():raise ValueError('报告已删除')
    run=store.one('runs',run_id)
    run['all_source_ids']=store.source_ids(run_id);run['source_count']=len(run['all_source_ids'])
    # Details are also consumed by internal learning trials. The report-list
    # visibility filter must not hide a valid, explicitly addressed trial draft.
    latest=store.rows(f'SELECT {_COLUMNS} FROM briefs b JOIN runs r ON r.id=b.run_id WHERE b.run_id=? ORDER BY b.rowid DESC LIMIT 1',(run_id,))[0]
    original=store.rows(f'SELECT {_COLUMNS} FROM briefs b JOIN runs r ON r.id=b.run_id WHERE b.run_id=? ORDER BY b.rowid LIMIT 1',(run_id,))[0]
    assessments=store.rows('SELECT * FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1',(version_id,))
    return {'run':run,'assessments':assessments,'latest':latest,'original':original}
