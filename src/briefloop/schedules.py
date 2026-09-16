"""In-process report schedules. No system daemon or offline catch-up."""
import calendar
import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import dump, uid, Conflict
from .models import Requirements
from .external_requests import _RequestStore, _operation

SCHEMA = '''
CREATE TABLE IF NOT EXISTS report_schedules(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
 enabled INTEGER NOT NULL, next_at TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_fires(
 id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, due_at TEXT NOT NULL,
 job_id TEXT, run_id TEXT, status TEXT NOT NULL, error TEXT,
 UNIQUE(schedule_id,due_at));
'''
UTC = timezone.utc
UNITS = {'minutes':60, 'hours':3600, 'days':86400, 'weeks':604800}

def stamp(value):
    return value.astimezone(UTC).isoformat(timespec='seconds')

def clock():
    return datetime.now(UTC)

def anchor(config):
    zone = ZoneInfo(config['timezone'])
    local = datetime.fromisoformat(config['start'])
    if local.tzinfo is not None:
        raise ValueError('首次执行时间请使用所选时区的本地时间')
    aware = local.replace(tzinfo=zone)
    if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError('该时间处于夏令时跳转缺口，请选择另一个时间')
    return aware

def next_time(config, after):
    first = anchor(config)
    start = first.astimezone(UTC)
    if start > after:return start
    kind = config['frequency']
    if kind == 'custom':
        seconds = config['every'] * UNITS[config['unit']]
        return start + timedelta(seconds=(math.floor((after-start).total_seconds()/seconds)+1)*seconds)
    local_after = after.astimezone(first.tzinfo)
    if kind in ('daily', 'weekly'):
        days = 1 if kind == 'daily' else 7
        steps = max(0,(local_after.date()-first.date()).days//days)
        candidate = first + timedelta(days=steps*days)
        while candidate.astimezone(UTC) <= after:
            candidate += timedelta(days=days)
    else:
        months = max(0,(local_after.year-first.year)*12+local_after.month-first.month)
        while True:
            year, month0 = divmod(first.year*12+first.month-1+months,12)
            candidate = first.replace(year=year,month=month0+1,day=min(first.day,calendar.monthrange(year,month0+1)[1]))
            if candidate.astimezone(UTC)>after:break
            months += 1
    # In a future DST gap, round-trip selects the first corresponding valid time.
    return candidate.astimezone(UTC)

def validate(store, body):
    name = str(body.get('name','')).strip()
    if not name or len(name)>200:raise ValueError('请输入 1–200 字的计划名称')
    config = dict(body.get('config') or {})
    if config.get('frequency') not in ('daily','weekly','monthly','custom'):raise ValueError('请选择预设或自定义频率')
    try:anchor(config)
    except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError) as exc:raise ValueError('请填写有效的首次执行时间和时区') from exc
    if config['frequency']=='custom':
        if type(config.get('every')) is not int or not 1<=config['every']<=10000 or config.get('unit') not in UNITS:
            raise ValueError('自定义频率须为 1–10000 分钟/小时/天/周')
    req = Requirements.model_validate(config.get('requirements',{})).model_dump()
    # Freeze the optional paid check when the schedule is saved. A later change
    # of workspace defaults must not silently enable it on recurring reports.
    if req['fact_check'] is None:
        req['fact_check']=store.settings().get('fact_checker') is True
    if req['fact_check'] and not req['allow_web']:
        raise ValueError('离线任务不能开启联网事实核查；请允许联网检索，或关闭该开关')
    if req['fact_check']:
        # Each firing would stop at the same check; refuse the schedule when saved.
        from .review_capability import require_for_fact_check
        require_for_fact_check(store.settings().get('agent_backend','codex'))
    ids = config.get('source_ids',[])
    if not isinstance(ids,list) or not all(isinstance(s,str) for s in ids):raise ValueError('请选择材料')
    for sid in ids:store.one('sources',sid)
    if not ids and not req['allow_web']:raise ValueError('请选择来源，或允许联网研究')
    if req['writing_mode']=='internal_report' and store.settings().get('company_context_enabled') is None:
        raise ValueError('请先在材料与需求中选择是否维护企业背景知识库')
    config['requirements']=req
    config['source_ids']=list(dict.fromkeys(ids))
    return name, config

def listing(store):
    result=[]
    from .execution_records import sanitize
    has_chat=bool(store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_events'"))
    for row in store.rows('SELECT * FROM report_schedules ORDER BY created DESC'):
        row['config']=json.loads(row['config']);row['enabled']=bool(row['enabled'])
        row['history']=store.rows('SELECT f.*,j.status AS job_status,j.error AS job_error FROM schedule_fires f LEFT JOIN jobs j ON f.job_id=j.id WHERE f.schedule_id=? ORDER BY f.rowid DESC LIMIT 5',(row['id'],))
        for entry in row['history']:
            if entry['job_error']:entry['job_error']=sanitize(entry['job_error'])[:1000]
            briefs=store.rows('SELECT id FROM briefs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(entry['run_id'],))
            entry['version_id']=briefs[0]['id'] if briefs else None
            entry['runtime_message']=None
            if has_chat and entry['job_status']=='running':
                statuses=store.rows("SELECT e.data FROM chat_events e WHERE e.kind='runtime/status' AND e.session_id IN (SELECT session_id FROM chat_events WHERE kind='job/attached' AND json_extract(data,'$.jobId')=?) ORDER BY e.seq DESC LIMIT 1",(entry['job_id'],))
                if statuses:
                    status=json.loads(statuses[0]['data'])
                    if status.get('status')=='retry':entry['runtime_message']=sanitize(status.get('message',''))[:1000]
        result.append(row)
    return result

def save(store, body, at=None):
    at=at or clock();name,config=validate(store,body)
    sid=body.get('id') or uid('schedule')
    upcoming=stamp(next_time(config,at))
    with store.tx() as c:
        if body.get('id'):
            if not c.execute('SELECT id FROM report_schedules WHERE id=?',(sid,)).fetchone():raise Conflict('定时报告已删除，请刷新')
            c.execute('UPDATE report_schedules SET name=?,config=?,enabled=?,next_at=? WHERE id=?',(name,dump(config),int(body.get('enabled',True)),upcoming,sid))
        else:c.execute('INSERT INTO report_schedules VALUES(?,?,?,?,?,?)',(sid,name,dump(config),int(body.get('enabled',True)),upcoming,stamp(at)))
    return {'id':sid,'next_at':upcoming}

def change(store, body):
    sid=body['id']
    with store.tx() as c:
        row=c.execute('SELECT * FROM report_schedules WHERE id=?',(sid,)).fetchone()
        if not row:raise Conflict('定时报告已删除，请刷新')
        if body['action']=='delete':c.execute('DELETE FROM report_schedules WHERE id=?',(sid,))
        elif body['action']=='toggle':
            enabled=not row['enabled']
            c.execute('UPDATE report_schedules SET enabled=?,next_at=? WHERE id=?',(int(enabled),stamp(next_time(json.loads(row['config']),clock())),sid))
        else:raise ValueError('未知操作')
    return {'id':sid}

def skip_offline(store, at=None):
    at=at or clock()
    with store.tx() as c:
        for row in c.execute('SELECT * FROM report_schedules WHERE enabled=1 AND next_at<=?',(stamp(at),)).fetchall():
            c.execute('UPDATE report_schedules SET next_at=? WHERE id=?',(stamp(next_time(json.loads(row['config']),at)),row['id']))

def fire(store, sid, *, at=None, manual=False, request_id=None):
    at=at or clock();wakeup=False
    if manual and (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,80}',request_id)):
        raise ValueError('立即运行需要稳定的请求 ID')
    with store.tx() as c:
        row=c.execute('SELECT * FROM report_schedules WHERE id=?',(sid,)).fetchone()
        if not row:raise Conflict('定时报告已删除，请刷新')
        if not manual and (not row['enabled'] or row['next_at']>stamp(at)):return None
        config=json.loads(row['config'])
        due='manual:'+request_id if manual else row['next_at']
        previous=c.execute('SELECT * FROM schedule_fires WHERE schedule_id=? AND due_at=?',(sid,due)).fetchone()
        if previous:
            if not manual:c.execute('UPDATE report_schedules SET next_at=? WHERE id=?',(stamp(next_time(config,at)),sid))
            return {key:previous[key] for key in ('status','job_id','run_id','error')}
        fid=uid('fire')
        if not manual:c.execute('UPDATE report_schedules SET next_at=? WHERE id=?',(stamp(next_time(config,at)),sid))
        active=c.execute("SELECT j.id FROM schedule_fires f JOIN jobs j ON f.job_id=j.id WHERE f.schedule_id=? AND j.status IN ('queued','running') LIMIT 1",(sid,)).fetchone()
        result={'status':'skipped' if active else 'failed','job_id':None,'run_id':None}
        error='上一份报告仍在执行，本次跳过' if active else None
        if not active:
            c.execute('SAVEPOINT admission')
            try:
                view=_RequestStore(store,c)
                local=at.astimezone(ZoneInfo(config['timezone']))
                req={**config['requirements'],'report_date':local.date().isoformat(),'company_context_revision':None}
                if config['frequency']=='custom':duration=timedelta(seconds=config['every']*UNITS[config['unit']])
                elif config['frequency']=='monthly':
                    year,month0=divmod(local.year*12+local.month-2,12)
                    previous=local.replace(year=year,month=month0+1,day=min(local.day,calendar.monthrange(year,month0+1)[1]))
                    duration=local-previous
                else:duration=timedelta(days={'daily':1,'weekly':7}[config['frequency']])
                req.update(period_start='', period_end='', report_timezone=config['timezone'])
                req['period']=f'{(local-duration).isoformat(timespec="minutes")} 至 {local.isoformat(timespec="minutes")}'
                saved_requirements=view.meta('requirements')
                result=_operation(view,'submit',{'requirements':req,'source_ids':config['source_ids']})
                view.set_meta('requirements',saved_requirements)
                wakeup=view.jobs_admitted
                c.execute('RELEASE admission')
            except Exception:
                c.execute('ROLLBACK TO admission');c.execute('RELEASE admission')
                error='未能创建报告。请检查材料、企业背景选择及模型设置，然后立即运行重试。'
        c.execute('INSERT INTO schedule_fires VALUES(?,?,?,?,?,?,?)',(fid,sid,due,result['job_id'],result['run_id'],result['status'],error))
    if wakeup:store.wake_jobs()
    return {**result,'error':error}

def tick(store, at=None):
    at=at or clock()
    for row in store.rows('SELECT id FROM report_schedules WHERE enabled=1 AND next_at<=?',(stamp(at),)):
        fire(store,row['id'],at=at)
