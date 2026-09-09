"""Source-backed company background revisions, separate from WikiSkill methods."""
import json
from .store import dump,uid,now


def snapshot(store):
    rows=store.rows('SELECT * FROM company_facts ORDER BY rowid')
    current={};pending=[]
    for row in rows:
        if row['status']=='accepted':current[row['fact_key']]=row
        elif row['status']=='pending':pending.append(row)
    return {'enabled':store.settings().get('company_context_enabled'),
            'facts':list(current.values()),'pending':pending,'revision':rows[-1]['id'] if rows else None}


def propose(store, value):
    allowed={'key','value','source_id','locator','effective_date','origin'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('企业背景条目格式无效')
    for field in ('key','value','source_id','effective_date'):
        if not isinstance(value.get(field),str) or not value[field].strip():raise ValueError('企业背景缺少 '+field)
    from datetime import date
    date.fromisoformat(value['effective_date'])
    origin=value.get('origin','user')
    if origin not in ('public','user'):raise ValueError('资料来源类型无效')
    source=store.one('sources',value['source_id'])
    if source['status']!='ready':raise ValueError('背景资料尚未成功读取')
    store.source_text(source['id'])
    if origin=='public' and not source.get('url'):raise ValueError('公开更新需要已登记的公开来源地址；上传材料按用户材料处理')
    if not store.settings().get('company_context_enabled'):raise ValueError('用户尚未启用企业背景知识库')
    with store.tx() as c:
        previous=c.execute("SELECT * FROM company_facts WHERE fact_key=? AND status='accepted' ORDER BY rowid DESC LIMIT 1",(value['key'],)).fetchone()
        if previous and previous['value']==value['value'] and previous['source_id']==source['id'] and previous['effective_date']==value['effective_date']:
            return dict(previous)
        status='pending' if origin=='user' and previous and previous['value']!=value['value'] else 'accepted'
        # Older-dated reports enrich history without replacing a newer effective fact.
        if previous and value['effective_date']<previous['effective_date']:status='historical'
        fid=uid('fact')
        c.execute('INSERT INTO company_facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,value['key'],value['value'],source['id'],value.get('locator',''),value['effective_date'],origin,status,previous['id'] if previous else None,now(),None))
    return store.rows('SELECT * FROM company_facts WHERE id=?',(fid,))[0]


def resolve_conflict(store,fact_id,accept):
    if type(accept) is not bool:raise ValueError('请选择采用或保留原记录')
    with store.tx() as c:
        row=c.execute('SELECT * FROM company_facts WHERE id=?',(fact_id,)).fetchone()
        if not row:raise ValueError('背景记录不存在')
        if row['status']!='pending':raise ValueError('此条记录不再等待确认')
        latest=c.execute("SELECT id FROM company_facts WHERE fact_key=? AND status='accepted' ORDER BY rowid DESC LIMIT 1",(row['fact_key'],)).fetchone()
        if accept and latest and latest['id']!=row['previous_id']:raise ValueError('企业背景已有新的更新，请重新比较冲突')
        c.execute('UPDATE company_facts SET status=?,resolved_at=? WHERE id=?',('accepted' if accept else 'rejected',now(),fact_id))
    return snapshot(store)


def prompt(store):
    context=snapshot(store)
    if context['enabled'] is None:
        return '如果本轮是企业内部周报，在开展研究前向用户提议是否维护企业背景知识库。用户选择后用 workspace-action 的 company_config 保存；拒绝或暂不选择仍可继续报告。'
    if not context['enabled']:return ''
    return ('本工作区已启用企业背景知识库。先读当前背景：'+dump(context)+
            '\n本次按既有联网权限和共享预算扫描公司公开 PR、年报、季报等更新。用 company_update 保存有来源和有效日期的背景。'
            '区分披露日/统计期，新一期数值用明确的指标期间作为key；保留日期与来源。用户上传内容与已有记录冲突时工具返回pending，向用户提问并调用company_resolve。'
            '公开资料之间的未决冲突保留在research_notes，不强行覆盖。引用背景时回到对应来源，确认本期适用性；背景摘要不是独立的新事实来源。')
