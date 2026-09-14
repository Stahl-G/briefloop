"""Provider-agnostic web search: one failure taxonomy and one budget envelope.

Provider modules (tavily, duckduckgo) validate their own options, perform the
network call and parse rows; reservation, redacted request envelopes and settle
bookkeeping live here so every provider is metered identically. 'native' is the
host backend's own search and is deliberately not a managed provider here.
"""
import json
from .store import dump

MANAGED_PROVIDERS=('tavily','duckduckgo')
PROVIDER_LABELS={'tavily':'Tavily','duckduckgo':'DuckDuckGo'}

# Stable, redacted classification for the request envelope. Do not infer a
# provider's billing outcome from these; they only say why an attempt stopped.
FAILURE_KINDS=('auth','quota','rate_limit','timeout','tls','network','invalid_response','too_large','provider_error','budget')


class SearchError(ValueError):
    pass


def marked(message,kind='provider_error',status=None,cls=SearchError):
    """Build a provider error; cls lets a provider keep its historical class."""
    if kind not in FAILURE_KINDS:raise ValueError('未知的 failure_kind：'+str(kind))
    error=cls(message);error.failure_kind=kind;error.status=status;return error


def network_failure(exc,label,cls=SearchError):
    """Transport failures are classified identically for every provider."""
    import ssl,urllib.error
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,ssl.SSLCertVerificationError):
        message=label+' TLS 证书校验失败；请检查本机 CA 证书或代理证书配置，未关闭证书校验';kind='tls'
    elif isinstance(reason,ssl.SSLError):
        message=label+' TLS 握手失败；请检查本机 TLS 或代理配置';kind='tls'
    elif isinstance(reason,TimeoutError):
        message=label+' 请求超时；未自动重试';kind='timeout'
    else:message='无法连接 '+label+'；请检查网络或代理配置，未自动重试';kind='network'
    return marked(message,kind,cls=cls)


def ssl_context():
    import ssl
    from pathlib import Path
    context=ssl.create_default_context()
    # Python.org macOS installs may have no default CA bundle. Add the system
    # trust bundle while retaining hostname and certificate verification.
    system_bundle=Path('/etc/ssl/cert.pem')
    if system_bundle.is_file():context.load_verify_locations(cafile=str(system_bundle))
    return context


def provider_module(provider):
    # Imported lazily: provider modules import this file for shared helpers.
    if provider=='tavily':
        from . import tavily as module
    elif provider=='duckduckgo':
        from . import duckduckgo as module
    else:raise ValueError('未知搜索 provider：'+str(provider))
    return module


def normalize_provider(value):
    from .models import normalize_search_provider
    provider=normalize_search_provider(value)
    if provider not in MANAGED_PROVIDERS:
        error=marked('本轮搜索源是宿主原生搜索；受控检索只支持 Tavily 与 DuckDuckGo，请在设置中选择后发起新任务')
        error.provider=provider
        raise error
    return provider


def provider_for_run(store,run_id):
    return normalize_provider(store.search_provider_for_run(run_id))


def check_run(store,run_id,provider):
    # Provider gate failures use the provider's own error class so historical
    # callers (tavily.TavilyError) keep catching them.
    fail=getattr(provider_module(provider),'ProviderError',SearchError)
    run=store.one('runs',run_id)
    if not json.loads(run['requirements']).get('allow_web'):
        error=fail('本轮未允许联网搜索');error.provider=provider;raise error
    if store.search_provider_for_run(run_id)!=provider:
        error=fail('本轮搜索服务不是 '+PROVIDER_LABELS[provider]+'，请在设置中选择后发起新任务');error.provider=provider;raise error
    return run


def search(query,*,provider=None,topic='general',time_range=None,start_date=None,end_date=None,
           include_domains=None,exclude_domains=None,max_results=5,search_depth='basic',
           key_file=None,store=None,run_id=None):
    """One metered search request; provider stays swappable behind one envelope."""
    if provider is None:
        if store is None or run_id is None:raise SearchError('未指定搜索 provider')
        provider=provider_for_run(store,run_id)
    else:provider=normalize_provider(provider)
    module=provider_module(provider)
    if store is not None and run_id and provider == 'tavily':
        import json
        from datetime import datetime, timedelta
        window = json.loads(store.one('runs', run_id)['requirements']).get('time_context')
        if window:
            start_date = datetime.fromisoformat(window['start']).date().isoformat()
            # Tavily ends before end_date and accepts dates, not timestamps.
            # Round a partial last day up, then review exact event times separately.
            end = datetime.fromisoformat(window['end_exclusive'])
            ceiling = end if (end.hour,end.minute,end.second,end.microsecond)==(0,0,0,0) else end+timedelta(days=1)
            end_date = ceiling.date().isoformat()
            time_range = None
    options={'topic':topic,'time_range':time_range,'start_date':start_date,'end_date':end_date,
             'include_domains':list(include_domains or []),'exclude_domains':list(exclude_domains or []),
             'max_results':max_results,'search_depth':search_depth}
    parameters=module.validate_search(query,options)
    reservation=None;local_id=None;round_id=None
    if store is not None and run_id is not None:
        check_run(store,run_id,provider)
        from . import research_budget as budget
        try:reservation=budget.reserve_search(store,run_id,max_results)
        except budget.BudgetExhausted as exc:return exc.result
        max_results=reservation['max_results'];parameters['max_results']=max_results
        local_id=reservation['request_id'];round_id=reservation['round_id']
    envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':round_id,
              'provider':provider,'operation':'search','query':query,'parameters':parameters,'outcome':None,
              'failure_kind':None,'raw_response_path':None,'admitted_urls':[],'unadmitted_urls':[],'budget_after':{}}
    try:
        parsed,raw,provider_request_id,usage=module.call_search(query,parameters,key_file=key_file)
    except SearchError as exc:
        exc.provider=provider
        envelope['outcome']='failed';envelope['failure_kind']=getattr(exc,'failure_kind','provider_error')
        if reservation:
            budget.save_discovery(store,run_id,reservation['request_id'],dump({'status':'failed','query':query,'error':str(exc)}).encode())
            path=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
            exc.request_record_path=path
            from .research_plan import settle_request
            settle_request(store,run_id,reservation['request_id'],'failed',failure_kind=envelope['failure_kind'],record_path=path)
        raise
    discovery=None
    if reservation:
        # Preserve the complete provider response before any candidate limiting.
        discovery=budget.save_discovery(store,run_id,reservation['request_id'],raw)
    results=module.rows(parsed)
    envelope['provider_request_id']=provider_request_id;envelope['raw_response_path']=discovery
    envelope['outcome']='success';envelope['failure_kind']=None
    output={'provider':provider,'query':query,'results':results,'usage':usage,'request_id':provider_request_id,
            'local_request_id':local_id,'provider_request_id':provider_request_id,'outcome':'success','failure_kind':None,
            'request_record_path':None,'round_id':round_id,'note':module.search_note()}
    if reservation:
        admitted=budget.record_candidates(store,run_id,[row['url'] for row in results])
        allowed=set(admitted['allowed_urls'])
        envelope['admitted_urls']=sorted(allowed);envelope['unadmitted_urls']=admitted['unadmitted_urls'];envelope['budget_after']=admitted['budget']
        output.update({'results':[row for row in results if budget.canonical_url(row['url']) in allowed],
                       'status':'budget_exhausted' if admitted['unadmitted_urls'] else 'ok',
                       'unadmitted_urls':admitted['unadmitted_urls'],'discovery_path':discovery,
                       'remaining':admitted['budget']['remaining'],'budget':admitted['budget']})
        output['request_record_path']=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
        from .research_plan import settle_request
        settle_request(store,run_id,reservation['request_id'],'completed',record_path=output['request_record_path'])
        if admitted['unadmitted_urls']:
            output['message']='候选 URL 预算已用完；未纳入的 URL 和完整搜索响应已保留，不继续扩大检索'
    return output


def extract(store,urls,*,run_id=None,provider=None,extract_depth='basic',key_file=None):
    """Provider-facing extraction entry: Tavily uses its API; keyless providers
    register pages through the same metered direct-fetch path as add-url."""
    if provider is None:
        if run_id is None:raise SearchError('未指定搜索 provider')
        provider=provider_for_run(store,run_id)
    else:provider=normalize_provider(provider)
    return provider_module(provider).extract(store,urls,run_id=run_id,extract_depth=extract_depth,key_file=key_file)
