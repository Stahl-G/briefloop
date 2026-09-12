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
        status='pending' if previous and previous['value']!=value['value'] else 'accepted'
        # Older-dated reports enrich history without replacing a newer effective fact.
        if previous and previous['value']==value['value'] and value['effective_date']<previous['effective_date']:status='historical'
        fid=uid('fact')
        c.execute('INSERT INTO company_facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,value['key'],value['value'],source['id'],value.get('locator',''),value['effective_date'],origin,status,previous['id'] if previous else None,now(),None))
    if status=='pending':
        from .conflicts import create
        create(store,source_ids=[previous['source_id'],source['id']],fact_ids=[previous['id'],fid],description='企业背景同一条目存在分歧：'+value['key'])
    return store.rows('SELECT * FROM company_facts WHERE id=?',(fid,))[0]


def resolve_conflict(store,fact_id,accept):
    if type(accept) is not bool:raise ValueError('请选择采用或保留原记录')
    from .conflicts import respond
    for row in store.rows("SELECT * FROM conflicts WHERE status!='resolved'"):
        if fact_id in json.loads(row['data'])['fact_ids']:
            respond(store,row['id'],'prefer_new' if accept else 'keep_current','用户已选择'+('采用提交材料' if accept else '保留原记录')+'；仍需 Reviewer 对照原件复核')
            return snapshot(store)
    raise ValueError('此条目没有待处理的来源冲突')


def prompt(store, run_id=None):
    review=review_status(store,run_id) if run_id else None
    context=review['context'] if review else snapshot(store)
    if context['enabled'] is None:
        return '如果本轮是企业内部周报，在开展研究前向用户提议是否维护企业背景知识库。用户选择后用 workspace-action 的 company_config 保存；拒绝或暂不选择仍可继续报告。'
    if not context['enabled']:return ''
    if review:return '本轮企业背景检查已完成，使用以下固定快照及原始来源，不重复启动维护：'+dump(review)
    return ('本工作区已启用企业背景知识库。先读当前背景：'+dump(context)+
            '\n本次按既有联网权限和共享预算扫描公司公开 PR、年报、季报等更新。用 company_update 保存有来源和有效日期的背景。'
            '区分披露日/统计期，新一期数值用明确的指标期间作为key；保留日期与来源。用户上传内容与已有记录冲突时工具返回pending，向用户提问并调用company_resolve。'
            '公开资料和用户材料的分歧均保留冲突记录并交Reviewer复核；用户选择不等于冲突已解决。引用背景时回到对应来源，确认本期适用性；背景摘要不是独立的新事实来源。')


def review_status(store, run_id):
    return store.meta('company_review:' + run_id)


def complete_review(store, run_id, reviewed_sources, summary):
    """Save a source-bound maintenance checkpoint, not a bare done flag."""
    import hashlib
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('企业背景检查需要记录结论')
    if not isinstance(reviewed_sources, list) or not reviewed_sources:
        raise ValueError('企业背景检查需要记录已检查的来源')
    allowed=set(store.source_ids(run_id));sources=[]
    for item in reviewed_sources:
        if not isinstance(item, dict) or item.get('source_id') not in allowed:
            raise ValueError('背景检查来源不属于本轮报告')
        if item.get('result') not in ('used','unchanged','unrelated','unavailable') or not str(item.get('note','')).strip():
            raise ValueError('逐项记录背景资料是否采用、无变化、不相关或不可读取，以及理由')
        source=store.one('sources', item['source_id'])
        if source['status']=='ready':store.source_text(source['id'])
        elif item['result']!='unavailable':raise ValueError('读取失败来源只能记录为不可读取')
        sources.append({**item,'hash':source['hash']})
    missing=allowed-{item['source_id'] for item in sources}
    if missing:raise ValueError('企业背景检查尚未覆盖本轮材料：'+', '.join(sorted(missing)))
    context=snapshot(store)
    if any(item['result']=='used' for item in sources) and not context['facts']:
        raise ValueError('已发现可用背景资料，请先用 company_update 保存企业背景条目')
    value={'run_id':run_id,'reviewed_sources':sources,'summary':summary.strip(),
           'context':context,'completed_at':now()}
    value['revision']='context_'+hashlib.sha256(dump(context).encode()).hexdigest()
    store.set_meta('company_review:'+run_id,value)
    return value


def require_review(store, run):
    req=json.loads(run['requirements'])
    if not req.get('company_context_required'):return None
    value=review_status(store,run['id'])
    if not value:raise ValueError('本轮企业背景维护尚未完成，不能进入报告写作')
    return value


def prepare_review(store, runtime, job, run, folder, backend):
    req=json.loads(run['requirements'])
    if not req.get('company_context_required'):return
    if review_status(store,run['id']):return
    pending=store.meta('company_review_pending:'+run['id'])
    if pending:
        complete_review(store,run['id'],pending['reviewed_sources'],pending['summary'])
        return
    from .runtime import source_context,TASK_CONTEXT
    from .agent_commands import tool_command
    review_folder=folder/'company-review';review_folder.mkdir(exist_ok=True)
    data={'run_id':run['id'],'requirements':req,'company_context':snapshot(store),
          'sources':[source_context(store,sid) for sid in store.source_ids(run['id'])]}
    (review_folder/'input.json').write_text(dump(data))
    tool=tool_command(store.root,backend=json.loads(job['payload']).get('agent_backend','codex'))
    prompt_text=TASK_CONTEXT+f"""
你负责本轮报告开始前的企业背景维护。尚未完成此阶段时，系统不会启动报告写作。
读取 {review_folder/'input.json'}。组织：{req.get('organization','')}。
检查本轮已有资料中的公开 PR、年报、季报与企业材料，结合当前企业背景识别新增事实。
遵守本轮联网设置及同一研究预算。未允许联网时只检查登记资料，缺口如实记录。
用 `{tool} workspace-action --request REQUEST_JSON` 保存结果，所有请求文件在本工作区内：
1. company_update 的 fact 含 key/value/source_id/locator/effective_date/origin。有用的企业事实应实际登记；明确指标期间，保留来源。
2. 用户材料冲突产生 pending，保留原已接受事实，交给用户；不可自行 company_resolve。
3. 完成后调用 company_review_complete，包含 run_id、summary、reviewed_sources。
reviewed_sources 必须覆盖本轮每个 source_id，每项含 source_id、result（used/unchanged/unrelated/unavailable）、note（简短说明）。
可用背景资料标 used 并登记相关事实；与公司背景无关的行业材料标 unrelated；不能读取标 unavailable。
不需要为了完成检查编造更新；确无变化可逐项说明依据。不要写报告正文或启动新报告任务。
最终回复简短维护结果即可。
"""
    stage={**job,'kind':'company_review','allow_web':req.get('allow_web',False)}
    store.event(job['id'],'company_review_started',{'run_id':run['id'],'message':'先检查并维护企业背景'})
    runtime.execute(stage,prompt_text,review_folder,resume_on_complete=True)
    value=require_review(store,run)
    store.event(job['id'],'company_review_complete',{'run_id':run['id'],'revision':value['revision'],'summary':value['summary']})
