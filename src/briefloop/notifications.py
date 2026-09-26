"""Workspace unread activity, separate from agent conversation messages."""
import hashlib
import json
import re
from .store import dump, now

CATEGORIES = ('reports', 'templates', 'learning', 'updates')


def post(store, key, category, title, *, target=None, body='', severity='info'):
    if category not in CATEGORIES:
        raise ValueError('未知提醒类别')
    with store.tx() as c:
        c.execute('INSERT OR IGNORE INTO notifications(event_key,category,title,body,target,severity,created) VALUES(?,?,?,?,?,?,?)',
                  (key, category, title, body, dump(target or {}), severity, now()))


def snapshot(store):
    rows=store.rows('SELECT * FROM notifications ORDER BY seq DESC LIMIT 50')
    for row in rows:
        row['target']=json.loads(row['target'])
    counts={category:0 for category in CATEGORIES}
    for row in store.rows('SELECT category,count(*) AS n FROM notifications WHERE read_at IS NULL GROUP BY category'):
        counts[row['category']]=row['n']
    return {'items':rows, 'counts':counts, 'unread':sum(counts.values()),
            'through':rows[0]['seq'] if rows else 0}


def mark_read(store, through, category=None, seq=None):
    if not isinstance(through,int) or through<0 or category is not None and category not in CATEGORIES:
        raise ValueError('无效的提醒位置')
    if seq is not None and (not isinstance(seq,int) or seq<1):
        raise ValueError('无效的提醒编号')
    sql='UPDATE notifications SET read_at=? WHERE read_at IS NULL AND seq<=?'
    args=[now(),through]
    if category is not None:sql+=' AND category=?';args.append(category)
    if seq is not None:sql+=' AND seq=?';args.append(seq)
    with store.tx() as c:c.execute(sql,args)
    return snapshot(store)


def job_status(store, job, status):
    kind=job.get('kind')
    category='templates' if kind=='prepare_template' else 'learning' if kind=='learn' else 'reports'
    if kind not in ('generate','revise','fact_check','export_docx','export_xlsx','release','prepare_template','learn'):
        return
    if status not in ('running','complete','failed','interrupted','cancelled'):
        return
    # Successful template/wiki updates are emitted when their content is saved.
    if kind in ('prepare_template','learn') and status in ('running','complete'):
        return
    from .task_labels import LABELS as KIND_LABELS
    payload=json.loads(job['payload'])
    label=KIND_LABELS[kind]
    title=label+' '+{'running':'已开始','complete':'已完成','failed':'失败','interrupted':'已中断','cancelled':'已停止'}[status]
    result=json.loads(job.get('result') or '{}')
    target={'job_id':job['id'],'run_id':payload.get('run_id'),'version_id':result.get('version_id') or payload.get('version_id')}
    from .execution_records import sanitize
    body='可查看任务详情和已保存结果。'
    if status in ('failed','interrupted'):
        reason=sanitize(str(job.get('error') or ''))[:500]
        body=(reason+'。' if reason else '')+'请打开任务查看错误原因及恢复操作。'
    post(store,f"job:{job['id']}:{payload.get('attempt',1)}:{status}",category,title,target=target,
         body=body,
         severity='error' if status in ('failed','interrupted') else 'info')


def wiki_changed(store, text):
    digest=hashlib.sha256(text.encode()).hexdigest()
    with store.tx() as c:
        old=c.execute("SELECT value FROM meta WHERE key='notification_wiki_hash'").fetchone()
        if old and json.loads(old['value'])==digest:return
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('notification_wiki_hash',?)",(dump(digest),))
        c.execute('INSERT INTO notifications(event_key,category,title,body,target,severity,created) VALUES(?,?,?,?,?,?,?)',
                  ('wiki:'+now()+':'+digest,'learning','Wiki 已更新','本工作区的写作与核查经验有新变化。','{}','info',now()))


def version_available(store, current, latest):
    if not all(isinstance(v,str) and re.fullmatch(r'\d+\.\d+\.\d+',v) for v in (current,latest)):
        raise ValueError('无效版本号')
    if tuple(map(int,latest.split('.'))) > tuple(map(int,current.split('.'))):
        post(store,'version:'+latest,'updates','BriefLoop v'+latest+' 可更新',body='打开版本与更新查看安装方式。')
