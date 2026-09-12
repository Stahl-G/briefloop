"""Shared source disagreement records; warnings and user choices are not resolution."""
import json
from .store import dump,uid,now

SCHEMA='''
CREATE TABLE IF NOT EXISTS conflicts(id TEXT PRIMARY KEY,run_id TEXT REFERENCES runs(id),
 status TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
'''


def create(store,*,source_ids,description,run_id=None,fact_ids=None,kind='contradiction',importance='core',
           participants=None,scope='',requirement_ids=None,reconciliation_id=None):
    if len(set(source_ids))<1 or not description.strip():raise ValueError('冲突需要来源与具体分歧')
    if kind not in ('contradiction','correction','different_scope','forecast_difference','unknown'):raise ValueError('未知冲突类型')
    if importance not in ('core','supporting'):raise ValueError('未知冲突重要性')
    for sid in source_ids:store.one('sources',sid)
    if run_id:store.one('runs',run_id)
    for identity in fact_ids or []:
        facts=store.rows('SELECT source_id FROM company_facts WHERE id=?',(identity,))
        if not facts or facts[0]['source_id'] not in source_ids:raise ValueError('冲突中的企业事实未绑定参与来源')
    participants=participants or []
    from .evidence import record
    for item in participants:
        if not isinstance(item,dict) or not item.get('claim_id'):raise ValueError('冲突参与陈述需要 claim_id')
        claim=record(store,'claims',item['claim_id'])
        if run_id and claim['run_id']!=run_id:raise ValueError('冲突参与主张属于另一报告')
        for span_id in item.get('span_ids',[]) or []:
            if record(store,'evidence_spans',span_id)['source_id'] not in source_ids:
                raise ValueError('冲突参与片段未绑定参与来源')
    if reconciliation_id:
        from .reconciliation import exists
        if not exists(store,run_id,reconciliation_id):raise ValueError('冲突引用的对照记录不存在')
    data={'source_ids':sorted(set(source_ids)),'description':description,'fact_ids':fact_ids or [],'kind':kind,'importance':importance,'responses':[],
          'participants':participants,'scope':scope,'requirement_ids':requirement_ids or [],'reconciliation_id':reconciliation_id}
    for row in store.rows("SELECT * FROM conflicts WHERE status IN ('open','addressed_pending_review')"):
        old=json.loads(row['data'])
        if row['run_id']==run_id and old['source_ids']==data['source_ids'] and old['description']==description:return {**row,'data':old}
    identity=uid('conflict')
    with store.tx() as c:c.execute('INSERT INTO conflicts VALUES(?,?,?,?,?,?)',(identity,run_id,'open',dump(data),now(),now()))
    store.event(None,'conflict_warning',{'conflict_id':identity,'description':description,'source_ids':source_ids})
    return {'id':identity,'run_id':run_id,'status':'open','data':data}


def respond(store,identity,action,reason):
    rows=store.rows('SELECT * FROM conflicts WHERE id=?',(identity,))
    if not rows or not reason.strip():raise ValueError('冲突不存在或缺少处理依据')
    if rows[0]['status']=='resolved':raise ValueError('冲突已复核解决')
    with store.tx() as c:
        current=c.execute('SELECT * FROM conflicts WHERE id=?',(identity,)).fetchone()
        if current['status']=='resolved':raise ValueError('冲突已复核解决')
        data=json.loads(current['data']);response={'action':action,'reason':reason}
        if data['responses'] and all(data['responses'][-1].get(k)==v for k,v in response.items()):
            return {'id':identity,'status':'addressed_pending_review','data':data}
        data['responses'].append({**response,'created':now()})
        c.execute("UPDATE conflicts SET status='addressed_pending_review',data=?,updated=? WHERE id=?",(dump(data),now(),identity))
    return {'id':identity,'status':'addressed_pending_review','data':data}


def for_run(store,run_id):
    sources=set(store.source_ids(run_id));out=[]
    for row in store.rows('SELECT * FROM conflicts ORDER BY rowid'):
        data=json.loads(row['data'])
        if row['run_id']==run_id or row['run_id'] is None and sources.intersection(data['source_ids']):out.append({**row,'data':data})
    return out


def accept_check(store,c,identity,decision,reason,review_id,chosen_fact_id=None,expected=None):
    row=c.execute('SELECT * FROM conflicts WHERE id=?',(identity,)).fetchone()
    if not row:raise ValueError('复核的冲突不存在')
    data=json.loads(row['data'])
    if expected and (row['status']!=expected['status'] or data!=expected['data']):raise ValueError('冲突在审阅期间已更新，请重新复核')
    if decision not in ('unresolved','confirmed_correction','different_scope','attributed_forecasts','keep_current','adopt_new'):raise ValueError('无效冲突处理结论')
    if not reason.strip():raise ValueError('冲突复核需要依据')
    if chosen_fact_id and chosen_fact_id not in data['fact_ids']:raise ValueError('选定企业事实不属于该冲突')
    if decision in ('adopt_new','confirmed_correction') and data['fact_ids'] and not chosen_fact_id:raise ValueError('采用企业背景需明确对应条目')
    if chosen_fact_id and decision in ('unresolved','keep_current','different_scope','attributed_forecasts'):raise ValueError('该冲突处理不能同时选用一条新的企业事实')
    previous=data.get('review');check={'review_id':review_id,'decision':decision,'reason':reason,'chosen_fact_id':chosen_fact_id}
    history=data.setdefault('review_history',[])
    if previous and previous not in history:history.append(previous)
    history.append(check);data['review']=check
    if decision!='unresolved':
        for fact_id in data['fact_ids']:
            fact=c.execute('SELECT status FROM company_facts WHERE id=?',(fact_id,)).fetchone()
            if fact and fact['status']=='pending':
                status='accepted' if fact_id==chosen_fact_id else 'historical' if decision in ('different_scope','attributed_forecasts') else 'rejected'
                c.execute('UPDATE company_facts SET status=?,resolved_at=? WHERE id=?',(status,now(),fact_id))
    c.execute('UPDATE conflicts SET status=?,data=?,updated=? WHERE id=?',('open' if decision=='unresolved' else 'resolved',dump(data),now(),identity))
