"""Provider-agnostic web search: one failure taxonomy and one budget envelope.

Provider modules (tavily, duckduckgo) validate their own options, perform the
network call and parse rows; reservation, redacted request envelopes and settle
bookkeeping live here so every provider is metered identically. 'native' is the
host backend's own search and is deliberately not a managed provider here.
"""
import json
import time
from pathlib import Path
from .store import dump

MANAGED_PROVIDERS=('tavily','duckduckgo','bocha','zhipu')
PROVIDER_LABELS={'tavily':'Tavily','duckduckgo':'DuckDuckGo','bocha':'博查','zhipu':'智谱搜索'}

# Stable, redacted classification for the request envelope. Do not infer a
# provider's billing outcome from these; they only say why an attempt stopped.
FAILURE_KINDS=('auth','quota','rate_limit','timeout','tls','network','invalid_response','too_large','provider_error','budget','local_error')


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
    elif provider=='zhipu':
        from . import zhipu as module
    elif provider=='bocha':
        from . import bocha as module
    else:raise ValueError('未知搜索 provider：'+str(provider))
    return module


def normalize_provider(value):
    from .models import normalize_search_provider
    provider=normalize_search_provider(value)
    if provider not in MANAGED_PROVIDERS:
        error=marked('本轮搜索源是宿主原生搜索；受控检索支持 Tavily、博查、智谱与 DuckDuckGo，请在设置中选择后发起新任务')
        error.provider=provider
        raise error
    return provider


def provider_for_run(store,run_id):
    from .search_policy import for_run
    return normalize_provider(for_run(store,run_id)['primary_provider'])


def check_run(store,run_id,provider):
    # Provider gate failures use the provider's own error class so historical
    # callers (tavily.TavilyError) keep catching them.
    fail=getattr(provider_module(provider),'ProviderError',SearchError)
    run=store.one('runs',run_id)
    if not json.loads(run['requirements']).get('allow_web'):
        error=fail('本轮未允许联网搜索');error.provider=provider;raise error
    from .search_policy import for_run,allowed
    if provider not in allowed(for_run(store,run_id)):
        error=fail('本轮搜索服务不是 '+PROVIDER_LABELS[provider]+'，请在设置中选择后发起新任务');error.provider=provider;raise error
    return run


def _joined_search(store,run_id,claim,module,provider):
    """A waiter receives the owner's saved result or classified failure."""
    from . import research_budget as budget
    completed=budget.wait_for_search_claim(store,run_id,claim['claim_key'],claim['waiting'])
    if completed['outcome']=='stale':return None
    if completed['outcome']=='success':
        try:result=json.loads(Path(completed['result_path']).read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError) as exc:
            error=SearchError('同一搜索请求已完成，但本地结果回执无法读取；请重新发起搜索')
            error.provider=provider;error.failure_kind='local_error'
            error.request_record_path=completed['request_record_path']
            raise error from exc
        return {**result,'reused_inflight':True}
    if completed['outcome']=='timeout':raise SearchError(completed['error'])
    if completed['outcome']=='local_error':
        error=SearchError(completed['error'] or '搜索结果已返回，但本地回执保存失败')
        error.provider=provider;error.failure_kind='local_error';error.request_record_path=completed['request_record_path']
        raise error
    error=marked(completed['error'] or '同一搜索请求失败',completed['failure_kind'] or 'provider_error',
                 completed['http_status'],cls=getattr(module,'ProviderError',SearchError))
    error.provider=provider;error.request_record_path=completed['request_record_path']
    raise error


def search(query,*,provider=None,topic='general',time_range=None,start_date=None,end_date=None,
           include_domains=None,exclude_domains=None,max_results=5,search_depth='basic',
           key_file=None,store=None,run_id=None,purpose='primary',reason='',gap_id=None):
    """One metered search request; provider stays swappable behind one envelope."""
    if provider is None:
        if store is None or run_id is None:raise SearchError('未指定搜索 provider')
        provider=provider_for_run(store,run_id)
    else:provider=normalize_provider(provider)
    if purpose not in ('primary','coverage_probe','gap_repair'):raise SearchError('无效搜索用途')
    if not isinstance(reason,str) or len(reason)>1000:raise SearchError('搜索原因过长')
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
    if provider=='zhipu':
        from .search_policy import for_run
        options['search_engine']=for_run(store,run_id)['zhipu_engine'] if store is not None and run_id else 'search_std'
    parameters=module.validate_search(query,options)
    reservation=None;local_id=None;round_id=None;claim=None
    if store is not None and run_id is not None:
        check_run(store,run_id,provider)
        if gap_id:
            from .research_plan import frozen
            plan=frozen(store,run_id) or {}
            gaps=[g for info in plan.get('rounds',{}).values() for g in info.get('gaps',[])]
            if not any(g.get('id')==gap_id for g in gaps):raise SearchError('缺口不属于本轮计划')
        from . import research_budget as budget
        # The identity uses validated options before a shared budget may cap
        # max_results. A different key-file path must not join another account,
        # but neither its path nor credential is stored in the claim table.
        identity={'provider':provider,'query':query,'parameters':parameters,
                  'purpose':purpose,'gap_id':gap_id,'key_file':str(key_file) if key_file is not None else None}
        for _ in range(3):
            claim=budget.claim_search(store,run_id,identity,max_results)
            if claim['waiting']:
                joined=_joined_search(store,run_id,claim,module,provider)
                if joined is not None:return joined
                continue
            if claim['budget_exhausted']:return claim['budget_exhausted']
            break
        else:raise SearchError('同一搜索请求的执行者连续失联，请稍后重试')
        reservation=claim['reservation']
        max_results=reservation['max_results'];parameters['max_results']=max_results
        local_id=reservation['request_id'];round_id=reservation['round_id']
    envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':round_id,
              'purpose':purpose,'reason':reason,'gap_id':gap_id,
              'provider':provider,'operation':'search','query':query,'parameters':parameters,'outcome':None,
              'failure_kind':None,'raw_response_path':None,'admitted_urls':[],'unadmitted_urls':[],'budget_after':{}}
    started=time.monotonic()
    from contextlib import nullcontext
    guard=budget.keep_search_claim_alive(store,run_id,claim['claim_key'],claim['owner']) if claim else nullcontext()
    with guard:
        admitted_recorded=False;persisting_raw=False
        try:
            parsed,raw,provider_request_id,usage=module.call_search(query,parameters,key_file=key_file)
            if not isinstance(raw,bytes):
                raise marked('搜索服务原始响应格式无效','invalid_response',
                             cls=getattr(module,'ProviderError',SearchError))
            envelope['elapsed_ms']=round((time.monotonic()-started)*1000)
            discovery=None
            if reservation:
                # Keep the full response even when a later URL is invalid.
                persisting_raw=True
                discovery=budget.save_discovery(store,run_id,reservation['request_id'],raw)
                persisting_raw=False
            envelope['provider_request_id']=provider_request_id;envelope['raw_response_path']=discovery
            results=module.rows(parsed)
            envelope['outcome']='success';envelope['failure_kind']=None
            output={'provider':provider,'query':query,'results':results,'usage':usage,'request_id':provider_request_id,
                    'local_request_id':local_id,'provider_request_id':provider_request_id,'outcome':'success','failure_kind':None,
                    'request_record_path':None,'round_id':round_id,'note':module.search_note()}
            if reservation:
                admitted=budget.record_candidates(store,run_id,[row['url'] for row in results],
                                                  reservation=reservation,claim_key=claim['claim_key'],claim_owner=claim['owner'])
                admitted_recorded=True
                allowed=set(admitted['allowed_urls'])
                from .search_policy import record_origins
                if admitted['accepted']:record_origins(store,allowed,envelope)
                envelope['admitted_urls']=sorted(allowed);envelope['unadmitted_urls']=admitted['unadmitted_urls'];envelope['budget_after']=admitted['budget']
                # A provider may repeat a URL with different fragments. Use
                # canonical identity for deduplication, but keep the first
                # original link so a useful deep-link fragment is not lost.
                seen=set();unique=[]
                for row in results:
                    url=budget.canonical_url(row['url'])
                    if url in allowed and url not in seen:
                        unique.append(row);seen.add(url)
                output.update({'results':unique,
                               'status':('response_rejected' if not admitted['accepted'] else
                                         'budget_exhausted' if admitted['unadmitted_urls'] else 'ok'),
                               'unadmitted_urls':admitted['unadmitted_urls'],'discovery_path':discovery,
                               'remaining':admitted['budget']['remaining'],'budget':admitted['budget']})
                if not admitted['accepted']:
                    envelope['outcome']=output['outcome']='response_rejected'
                    envelope['rejection_reason']=admitted['rejection_reason']
                    output['message']='请求所属阶段已结束或认领已失效；迟到搜索响应已保留，候选未接纳'
                output['request_record_path']=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
                from .research_plan import settle_request
                settle_request(store,run_id,reservation['request_id'],'completed',record_path=output['request_record_path'])
                if admitted['accepted'] and admitted['unadmitted_urls']:
                    output['message']='候选 URL 预算已用完；未纳入的 URL 和完整搜索响应已保留，不继续扩大检索'
                result_path=budget.save_search_result(store,run_id,reservation['request_id'],output)
                budget.finish_search_claim(store,run_id,claim['claim_key'],claim['owner'],outcome='success',result_path=result_path)
            return output
        except Exception as original:
            if admitted_recorded:
                # Candidate admission is already committed. A subsequent file
                # or origin-index error is local delivery failure, not a failed
                # provider response; never rewrite the settled request as such.
                envelope['delivery_error']='本地搜索结果回执保存失败'
                path=None
                try:
                    path=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
                    from .research_plan import settle_request
                    settle_request(store,run_id,reservation['request_id'],'completed',record_path=path)
                except Exception:pass
                budget.finish_search_claim(store,run_id,claim['claim_key'],claim['owner'],outcome='local_error',
                                           error='搜索结果已接纳，但本地回执保存失败；原始响应仍保留',
                                           failure_kind='local_error',request_record_path=path)
                error=SearchError('搜索结果已接纳，但本地回执保存失败；原始响应仍保留')
                error.provider=provider;error.failure_kind='local_error';error.request_record_path=path
                raise error from original
            if persisting_raw:
                # The provider returned, but we could not safely retain its
                # response. Settle the reserved request and release waiters;
                # do not claim the raw response was saved or blame the API.
                envelope.update(outcome='local_error',failure_kind='local_error',
                                delivery_error='无法保存搜索服务返回的原始响应')
                path=None
                try:path=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
                except Exception:pass
                try:
                    from .research_plan import settle_request
                    settle_request(store,run_id,reservation['request_id'],'failed',
                                   failure_kind='local_error',record_path=path)
                finally:
                    budget.finish_search_claim(store,run_id,claim['claim_key'],claim['owner'],outcome='local_error',
                                               error='搜索服务已返回，但本地原始响应保存失败',
                                               failure_kind='local_error',request_record_path=path)
                error=SearchError('搜索服务已返回，但本地原始响应保存失败')
                error.provider=provider;error.failure_kind='local_error';error.request_record_path=path
                raise error from original
            if isinstance(original,SearchError):exc=original
            elif isinstance(original,(ValueError,TypeError,KeyError)):
                message=('搜索服务返回的来源数据无效；原始响应已保留' if envelope['raw_response_path'] else
                         '搜索服务返回的数据无效；未保存原始响应')
                exc=marked(message,'invalid_response',
                           cls=getattr(module,'ProviderError',SearchError))
            else:
                exc=marked('搜索请求或响应保存失败；请检查本地日志','provider_error',
                           cls=getattr(module,'ProviderError',SearchError))
            exc.provider=provider
            envelope['elapsed_ms']=round((time.monotonic()-started)*1000);envelope['http_status']=getattr(exc,'status',None)
            envelope['outcome']='failed';envelope['failure_kind']=getattr(exc,'failure_kind','provider_error')
            if reservation:
                save_error=None
                if envelope['raw_response_path'] is None:
                    try:budget.save_discovery(store,run_id,reservation['request_id'],
                                              dump({'status':'failed','query':query,'error':str(exc)}).encode())
                    except Exception as error:save_error=error
                path=None
                try:path=budget.save_request_record(store,run_id,reservation['request_id'],envelope)
                except Exception as error:save_error=error
                exc.request_record_path=path
                from .research_plan import settle_request
                try:settle_request(store,run_id,reservation['request_id'],'failed',
                                   failure_kind=envelope['failure_kind'],record_path=path)
                finally:
                    budget.finish_search_claim(store,run_id,claim['claim_key'],claim['owner'],outcome='failed',
                                               error=str(exc),failure_kind=envelope['failure_kind'],
                                               http_status=getattr(exc,'status',None),request_record_path=path)
                if save_error is not None:
                    exc.local_record_error='搜索请求回执未能完整保存'
            raise exc from original if exc is not original else None


def extract(store,urls,*,run_id=None,provider=None,extract_depth='basic',key_file=None):
    """Provider-facing extraction entry: Tavily uses its API; keyless providers
    register pages through the same metered direct-fetch path as add-url."""
    if provider is None:
        if run_id is None:raise SearchError('未指定搜索 provider')
        provider=provider_for_run(store,run_id)
    else:provider=normalize_provider(provider)
    return provider_module(provider).extract(store,urls,run_id=run_id,extract_depth=extract_depth,key_file=key_file)
