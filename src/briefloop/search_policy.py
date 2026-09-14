"""Frozen channel permissions; the agent decides which queries close a gap."""
import json
from .models import SearchPolicy, normalize_search_provider

LABELS={'native':'宿主自带搜索','tavily':'Tavily','duckduckgo':'DuckDuckGo','bocha':'博查','zhipu':'智谱搜索'}


def resolve(value=None, primary='tavily'):
    if value is None:
        provider=normalize_search_provider(primary)
        value={'primary_provider':provider,'native_search_enabled':provider=='tavily'}
    return SearchPolicy.model_validate(value).model_dump()


def for_run(store, run_id):
    run=store.one('runs',run_id)
    # Job snapshot takes precedence over later workspace preferences.
    for row in store.rows("SELECT payload FROM jobs WHERE kind='generate' ORDER BY rowid DESC"):
        payload=json.loads(row['payload'])
        if payload.get('run_id')==run_id:
            # Legacy jobs froze an exclusive provider; new defaults cannot add permission.
            policy=payload.get('search_policy')
            if policy is None:
                policy={'primary_provider':normalize_search_provider(payload.get('search_provider','native')),
                        'native_search_enabled':False}
            return resolve(policy)
    req=json.loads(run['requirements'])
    return resolve(req.get('search_policy') or store.settings().get('search_policy'),store.settings()['search_provider'])


def allowed(policy):
    p=resolve(policy)
    channels=[p['primary_provider']]
    if p['coverage_mode']!='primary_only':
        channels+=p['supplemental_providers']
        if p['native_search_enabled']:channels.append('native')
    return list(dict.fromkeys(channels))


def native_allowed(config, internal=False):
    if not internal:return True
    policy=config.get('search_policy')
    if policy is None:
        return normalize_search_provider(config.get('search_provider','native'))=='native'
    return 'native' in allowed(policy)


def instructions(policy, tool, run_id):
    p=resolve(policy);channels=allowed(p)
    text='本轮冻结搜索策略：优先 '+LABELS[p['primary_provider']]+'；允许渠道：'+ '、'.join(LABELS[c] for c in channels)+'。'
    text+='优先不等于独占；按具体信息需要选择已允许渠道，不必先浪费一次首选查询。'
    if p['coverage_mode']=='coverage' and len(channels)>1:
        text+='跨市场/语言任务即使首选有结果，也安排少量互补查询检查遗漏；约20%搜索额度为软预留，不强制花完。'
    elif p['coverage_mode']=='on_gap':text+='重要主体遗漏、缺原文、关键冲突或渠道失败时再补查。'
    text+='关注市场/语言：'+(p['market_scope'] or '按报告需求')+'；关注平台：'+('、'.join(p['platform_scope']) or '按报告需求')+'。'
    text+='中文主体用原名/别名和本地发布记录补查。公众号、小红书分别核对是否发现链接、是否读到正文；不能承诺全量覆盖，摘要不当原文。'
    managed=[c for c in channels if c!='native']
    if managed:
        text+=f'受控搜索统一命令：`{tool} web-search --run {run_id} --provider PROVIDER --purpose primary|coverage_probe|gap_repair --reason "具体信息需求" --query "关键词"`。PROVIDER 可选 '+','.join(managed)+'。'
        text+='可选 --gap-id 绑定已登记缺口；日期、域名、topic、max-results参数沿用web-search。所有受控服务共享同一硬预算，失败也计次，不能换源绕过额度。'
        text+='搜索后用 add-url --run '+run_id+' --url URL 保存正文；Tavily在允许渠道内时才能用tavily-extract。'
        if 'tavily' in managed:text+=f'批量提取命令：`{tool} tavily-extract --run {run_id} --url URL [--url URL ...]`。'
    if 'zhipu' in channels:
        text+='智谱搜索引擎：'+p['zhipu_engine']+'；查询最多70字符，仅支持time-range及单个include-domain，不支持绝对日期或exclude-domain；夸克不支持域名过滤。'
    if 'native' in channels:
        text+='宿主自带搜索仅在实际工具存在且获授权时使用；无法观测的调用次数/底层索引记未知，不冒充受控硬预算。发现URL仍须add-url保存并读取，原生补查失败如实交接。'
    text+='渠道分数不可直接平均；同一URL不重复抓取，转载不算独立佐证。对未授权/无凭据的渠道不调用；401/402等错误保留，只有已允许替代渠道可继续，不绕过权限拒绝。'
    return text


def record_origins(store, urls, record):
    """Keep all discovery channels for a URL; never count them as independent evidence."""
    from .research_budget import canonical_url
    from .store import content_hash,dump
    with store.tx() as c:
        for url in urls:
            key='search_origin:'+content_hash(canonical_url(url))
            row=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            values=json.loads(row['value']) if row else []
            item={k:record.get(k) for k in ('provider','run_id','local_request_id','purpose')}
            if item not in values:values.append(item)
            c.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',(key,dump(values)))


def annotate_sources(store, sources):
    from .research_budget import canonical_url
    from .store import content_hash
    index={row['key']:json.loads(row['value']) for row in store.rows("SELECT key,value FROM meta WHERE key LIKE 'search_origin:%'")}
    for source in sources:
        try:origins=index.get('search_origin:'+content_hash(canonical_url(source['url'])),[])
        except (ValueError,TypeError,KeyError):origins=[]
        source['discovery_providers']=list(dict.fromkeys(r['provider'] for r in origins))
    return sources


def activity(store,run_id):
    store.one('runs',run_id)
    records=[]
    for path in sorted((store.root/'discovery'/run_id).glob('*.request.json'),key=lambda p:p.stat().st_mtime):
        try:record=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if record.get('operation')!='search':continue
        records.append({k:record.get(k) for k in ('provider','query','purpose','reason','outcome','failure_kind','http_status','elapsed_ms','admitted_urls','local_request_id')})
    return {'policy':for_run(store,run_id),'records':records[-100:],'native_search_requests':None}
