"""Optional Tavily REST source discovery/extraction. Credentials stay outside workspaces."""
from . import __version__,websearch
import hashlib
import json
import os
from contextlib import nullcontext
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from .store import now,uid,content_hash,dump
from .search_credentials import write_key

NAME='tavily'
LABEL='Tavily'
FAILURE_KINDS=websearch.FAILURE_KINDS


class TavilyError(websearch.SearchError):
    pass


ProviderError=TavilyError


def _marked(message,kind='provider_error',status=None):
    if kind not in FAILURE_KINDS:raise ValueError('未知的 failure_kind：'+str(kind))
    error=TavilyError(message);error.failure_kind=kind;error.status=status;return error


def _http_kind(code):
    if code in (401,403):return 'auth'
    if code==402:return 'quota'
    if code==429:return 'rate_limit'
    return 'provider_error'


def _key_path(key_file=None):
    return Path(key_file) if key_file is not None else Path.home()/'.config'/'briefloop'/'tavily.key'


def _read_key(key_file=None):
    key=os.environ.get('TAVILY_API_KEY','').strip()
    if key:return key,'environment'
    try:key=_key_path(key_file).read_text(encoding='utf-8').strip()
    except FileNotFoundError:return '',None
    except OSError:raise TavilyError('无法读取本机 Tavily 凭据文件') from None
    return (key,'file') if key else ('',None)


def key_status(*,key_file=None):
    key,source=_read_key(key_file)
    return {'configured':bool(key),'source':source}


def save_key(api_key,*,key_file=None):
    if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key.strip()):
        raise TavilyError('请输入有效的 Tavily API Key')
    write_key(_key_path(key_file),api_key.strip())
    return key_status(key_file=key_file)


def delete_key(*,key_file=None):
    try:_key_path(key_file).unlink()
    except FileNotFoundError:pass
    return key_status(key_file=key_file)


# Certificate handling is shared with other providers; verification stays on.
_ssl_context=websearch.ssl_context


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A fixed provider API must never forward its bearer token elsewhere.
        return None


def _post(endpoint,payload,*,key_file=None):
    key,_=_read_key(key_file)
    if not key:raise TavilyError('尚未配置 Tavily API Key，请在设置中填写')
    request=urllib.request.Request('https://api.tavily.com/'+endpoint,data=dump(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':f'BriefLoop/{__version__}'},method='POST')
    opener=urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(request,timeout=65) as response:
            raw=response.read(25*1024*1024+1)
        if len(raw)>25*1024*1024:raise _marked('Tavily 响应过大，未保存不完整结果','too_large')
        if key.encode() in raw:raise _marked('Tavily 响应包含凭据信息，未保存或展示','invalid_response')
        result=json.loads(raw)
        if not isinstance(result,dict):raise _marked('Tavily 返回格式无效','invalid_response')
        return result,raw
    except urllib.error.HTTPError as exc:
        raise _marked('Tavily 请求失败（HTTP '+str(exc.code)+'）；请检查密钥、额度和请求参数',_http_kind(exc.code),exc.code) from None
    except (urllib.error.URLError,OSError) as exc:
        raise websearch.network_failure(exc,'Tavily',TavilyError) from None
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise _marked('Tavily 未返回有效 JSON','invalid_response') from None


def check_run(store,run_id):
    return websearch.check_run(store,run_id,NAME)


def validate_search(query,options):
    if not isinstance(query,str) or not query.strip():raise TavilyError('搜索词不能为空')
    topic=options['topic'];max_results=options['max_results'];search_depth=options['search_depth']
    time_range=options['time_range'];start_date=options['start_date'];end_date=options['end_date']
    if topic not in ('general','news'):raise TavilyError('topic 必须是 general 或 news')
    if type(max_results) is not int or not 1<=max_results<=10:raise TavilyError('max_results 必须为 1–10')
    if search_depth not in ('basic','advanced'):raise TavilyError('search_depth 必须是 basic 或 advanced')
    if time_range not in (None,'day','week','month','year','d','w','m','y'):raise TavilyError('无效时间范围')
    for value in (start_date,end_date):
        if value:
            try:date.fromisoformat(value)
            except (ValueError,TypeError):raise TavilyError('日期使用 YYYY-MM-DD') from None
    if start_date and end_date and start_date>end_date:raise TavilyError('开始日期不能晚于结束日期')
    return {'topic':topic,'max_results':max_results,'search_depth':search_depth,
            'include_domains':list(options['include_domains'] or []),'exclude_domains':list(options['exclude_domains'] or []),
            'time_range':time_range,'start_date':start_date,'end_date':end_date}


def call_search(query,parameters,*,key_file=None):
    payload={'query':query,'topic':parameters['topic'],'max_results':parameters['max_results'],
             'search_depth':parameters['search_depth'],'include_answer':False,'include_raw_content':False,
             'auto_parameters':False,'include_usage':True}
    for name,value in (('time_range',parameters['time_range']),('start_date',parameters['start_date']),
                       ('end_date',parameters['end_date']),('include_domains',parameters['include_domains']),
                       ('exclude_domains',parameters['exclude_domains'])):
        if value:payload[name]=value
    result,raw=_post('search',payload,key_file=key_file)
    return result,raw,result.get('request_id'),result.get('usage')


def rows(parsed):
    results=[]
    for item in parsed.get('results',[]):
        if not isinstance(item,dict):continue
        results.append({'title':item.get('title',''),'url':item.get('url',''),'snippet':item.get('content',''),'kind':'search_snippet','score':item.get('score'),'published_date':item.get('published_date')})
    return results


def search_note():
    return '搜索摘要仅用于发现来源。请读取原网页或用 tavily-extract 保存提供方提取正文后再引用。'


def search(query,*,topic='general',time_range=None,start_date=None,end_date=None,include_domains=None,exclude_domains=None,max_results=5,search_depth='basic',key_file=None,store=None,run_id=None):
    return websearch.search(query,provider=NAME,topic=topic,time_range=time_range,start_date=start_date,end_date=end_date,
                            include_domains=include_domains,exclude_domains=exclude_domains,max_results=max_results,
                            search_depth=search_depth,key_file=key_file,store=store,run_id=run_id)


def _wait_for_extract_claims(store,run_id,waiting,*,request_urls,extract_depth,key_file):
    from . import research_budget as budget
    from .research_plan import AdmissionError
    records=[];unprocessed=[];errors={}
    def retry_url(url):
        try:retry=extract(store,[request_urls[url]],run_id=run_id,extract_depth=extract_depth,key_file=key_file)
        except (TavilyError,AdmissionError) as exc:
            unprocessed.append(url);errors[url]=str(exc)
            return
        records.extend(retry.get('sources',[]))
        pending=retry.get('unprocessed_urls',[])
        unprocessed.extend(budget.canonical_url(item) for item in pending)
        errors.update(retry.get('waiting_errors') or {})
        if not retry.get('sources') and not pending:
            unprocessed.append(url)
        if url in unprocessed:
            errors.setdefault(url,retry.get('message') or retry.get('status') or '来源提取未完成')
    for url,owner in waiting.items():
        result=budget.wait_for_claim(store,run_id,url,owner)
        if result['source_id']:
            source=store.one('sources',result['source_id'])
            provenance=store.root/'sources'/(source['id']+'.provenance.json')
            try:from_tavily=json.loads(provenance.read_text(encoding='utf-8')).get('extractor')=='tavily.extract'
            except (OSError,ValueError):from_tavily=False
            if source['status']=='failed' and not from_tavily:
                retry_url(url)
            else:
                records.append({**source,'reused':True})
        elif result['outcome']=='stale':
            retry_url(url)
        else:
            unprocessed.append(url)
            errors[url]=result.get('error') or ('同一来源的 Tavily 提取失败' if result['outcome']=='failed'
                                                else '同一来源的提取未完成')
    return records,list(dict.fromkeys(unprocessed)),errors


def _failed_source_urls(rows):
    from .research_budget import canonical_url
    return list(dict.fromkeys(canonical_url(row['url']) for row in rows if row['status']=='failed'))


def _waited_status(rows,unprocessed):
    if not unprocessed:return {}
    available=any(row['status']=='ready' for row in rows)
    return {'status':'partial' if available else 'failed',
            'outcome':'partial' if available else 'failed','partial':available}


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10 or not all(isinstance(url,str) and url.startswith(('https://','http://')) for url in urls):raise TavilyError('请提供 1–10 个 HTTP(S) 来源地址')
    if extract_depth not in ('basic','advanced'):raise TavilyError('无效提取深度')
    cached=[];local_id=None;round_id=None;reservation=None;claim=None;waiting={}
    if run_id:
        check_run(store,run_id)
        from . import research_budget as budget
        original_by_key={}
        for original in urls:
            original=original.strip()
            original_by_key.setdefault(budget.canonical_url(original),original)
        urls=list(original_by_key)
        requested_urls=list(urls)
        claim=budget.claim_pages(store,run_id,urls)
        cached=[{**row,'reused':True} for row in claim['cached'].values()]
        waiting=claim['waiting']
        pending=claim['claimed']
        if claim['budget_exhausted']:
            waited,unfinished,wait_errors=_wait_for_extract_claims(store,run_id,waiting,request_urls=original_by_key,
                extract_depth=extract_depth,key_file=key_file)
            cached.extend(waited)
            unprocessed=claim['unadmitted']+unfinished
            local_id=uid('extract')
            envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':None,'provider':NAME,'operation':'extract','query':None,'parameters':{'urls':unprocessed,'extract_depth':extract_depth,'format':'markdown'},'outcome':'budget_exhausted','failure_kind':'budget','raw_response_path':None,'admitted_urls':[],'unadmitted_urls':unprocessed,'budget_after':claim['budget_exhausted'].get('budget',{})}
            envelope['waited_unprocessed_urls']=unfinished
            envelope['waiting_errors']=wait_errors
            envelope['extraction_failed_urls']=_failed_source_urls(cached)
            path=budget.save_request_record(store,run_id,local_id,envelope)
            return {**claim['budget_exhausted'],'sources':cached,'unprocessed_urls':unprocessed,
                    'waiting_errors':wait_errors,'extraction_failed_urls':_failed_source_urls(cached),
                    'partial':any(row['status']=='ready' for row in cached),
                    'local_request_id':local_id,'request_record_path':path}
        if not pending:
            waited,unfinished,wait_errors=_wait_for_extract_claims(store,run_id,waiting,request_urls=original_by_key,
                extract_depth=extract_depth,key_file=key_file)
            by_url={budget.canonical_url(row['url']):row for row in cached+waited}
            combined=[by_url[url] for url in requested_urls if url in by_url]
            return {'provider':NAME,'sources':combined,'reused':not unfinished,
                    'unprocessed_urls':unfinished,'waiting_errors':wait_errors,
                    'extraction_failed_urls':_failed_source_urls(combined),
                    'budget':budget.snapshot(store,run_id),**_waited_status(combined,unfinished)}
        reservation=claim['reservation'];local_id=reservation['request_id']
        round_id=reservation['round_id']
        urls=pending
    network_urls=[original_by_key[url] for url in urls] if run_id else list(dict.fromkeys(urls))
    parameters={'urls':network_urls,'extract_depth':extract_depth,'format':'markdown'}
    envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':round_id,'provider':NAME,'operation':'extract','query':None,'parameters':parameters,'outcome':None,'failure_kind':None,'raw_response_path':None,'admitted_urls':[],'unadmitted_urls':[],'budget_after':{}}
    try:
        lease=(budget.keep_claims_alive(store,run_id,claim['owner'],urls) if run_id else nullcontext())
        with lease:
            response,raw=_post('extract',{'urls':network_urls,'extract_depth':extract_depth,'format':'markdown','include_usage':True},key_file=key_file)
    except TavilyError as exc:
        envelope['outcome']='failed';envelope['failure_kind']=getattr(exc,'failure_kind','provider_error')
        if local_id:
            path=budget.save_request_record(store,run_id,local_id,envelope)
            exc.request_record_path=path
            if round_id:
                from .research_plan import settle_request
                settle_request(store,run_id,local_id,'failed',failure_kind=envelope['failure_kind'],record_path=path)
            budget.abort_claims(store,run_id,claim['owner'],urls,error=str(exc))
        raise
    except Exception as exc:
        if run_id:
            budget.abort_claims(store,run_id,claim['owner'],urls,error=str(exc))
            envelope['outcome']='failed';envelope['failure_kind']='provider_error'
            path=budget.save_request_record(store,run_id,local_id,envelope)
            if round_id:
                from .research_plan import settle_request
                settle_request(store,run_id,local_id,'failed',failure_kind='provider_error',record_path=path)
        raise
    try:
        from .sources import PendingSources
        pending_sources=PendingSources(store) if run_id else None
        source_store=pending_sources if pending_sources is not None else store
        discovery=budget.save_discovery(store,run_id,local_id,raw) if run_id else None
        results=list(cached);failed=[]
        for url in dict.fromkeys(urls):
            source_url=original_by_key[url] if run_id else url
            item=None
            for candidate in response.get('results',[]):
                if not isinstance(candidate,dict):continue
                candidate_url=candidate.get('url')
                if candidate_url==source_url:
                    item=candidate;break
                if run_id:
                    try:matches=budget.canonical_url(candidate_url)==url
                    except ValueError:matches=False
                    if matches:item=candidate;break
            if item is None and len(urls)==1 and len(response.get('results',[]))==1:item=response['results'][0]
            text=item.get('raw_content','') if isinstance(item,dict) else ''
            if not isinstance(text,str):text=''
            error=None if text.strip() else 'Tavily 未能提取可读正文；原始提供方响应已保留'
            sid=uid('src');original=store.root/'sources'/(sid+'.original.tavily.json');original.write_bytes(raw)
            provenance={'url':source_url,'content_type':'application/json','fetched_at':now(),'raw_sha256':hashlib.sha256(raw).hexdigest(),'text_sha256':content_hash(text),'extractor':'tavily.extract','original_path':str(original.relative_to(store.root)),'extraction_status':'failed' if error else 'ready','original_kind':'provider_response','provider':NAME,'notice':'保存的是 Tavily 提供方响应及提取正文，不是原网站 HTML/PDF 字节。','request_id':response.get('request_id')}
            if error:provenance['error']=error;failed.append(url)
            if item and item.get('url')!=source_url:provenance['provider_url']=item.get('url')
            (store.root/'sources'/(sid+'.provenance.json')).write_text(dump(provenance))
            source=source_store.add_source((item.get('title') if item else None) or source_url.rsplit('/',1)[-1] or source_url,text,url=source_url,error=error,source_id=sid)
            results.append({**source,'provenance':provenance})
        accepted=(pending_sources.admit(run_id,reservation,claim_owner=claim['owner'],claimed_urls=urls)
                  if pending_sources is not None else True)
        envelope['provider_request_id']=response.get('request_id');envelope['outcome']='success'
        envelope['admitted_urls']=list(dict.fromkeys(urls));envelope['unadmitted_urls']=[]
        envelope['extraction_failed_urls']=failed
        output={'provider':NAME,'sources':results,'usage':response.get('usage'),'request_id':response.get('request_id'),
                'local_request_id':local_id,'provider_request_id':response.get('request_id'),'outcome':'success','failure_kind':None,
                'round_id':round_id,
                'extraction_failed_urls':failed,'request_record_path':None}
        if run_id:
            envelope['raw_response_path']=discovery
            if not accepted:
                from .research_plan import pending_requests
                request=(pending_requests(store,run_id).get(local_id) or {}) if round_id else {}
                reason=request.get('rejection_reason','page_claim_lost' if not round_id else 'stage_closed_or_replaced')
                envelope.update(outcome='response_rejected',admitted_urls=[],unadmitted_urls=list(dict.fromkeys(urls)),
                                rejection_reason=reason,
                                retained_source_ids=[row['id'] for row,_ in pending_sources.records])
                message=('同一来源读取租约已被接管；迟到提取响应已保留，未登记新报告来源'
                         if reason in ('page_claim_lost','page_claim_expired')
                         else '请求所属阶段已结束或更换；迟到提取响应已保留，未登记新报告来源')
                output.update(status='response_rejected',outcome='response_rejected',sources=list(cached),
                              unprocessed_urls=envelope['unadmitted_urls'],
                              message=message)
            envelope['budget_after']=budget.snapshot(store,run_id)
            output['budget']=envelope['budget_after']
            output['request_record_path']=budget.save_request_record(store,run_id,local_id,envelope)
            if round_id:
                from .research_plan import settle_request
                settle_request(store,run_id,local_id,'completed',record_path=output['request_record_path'])
            waited,unfinished,wait_errors=_wait_for_extract_claims(store,run_id,waiting,request_urls=original_by_key,
                extract_depth=extract_depth,key_file=key_file)
            by_url={budget.canonical_url(row['url']):row for row in output['sources']+waited if row.get('url')}
            output['sources']=[by_url[url] for url in requested_urls if url in by_url]
            if unfinished:output['unprocessed_urls']=list(dict.fromkeys(output.get('unprocessed_urls',[])+unfinished))
            output['extraction_failed_urls']=_failed_source_urls(output['sources'])
            output['waiting_errors']=wait_errors
            aggregate=_waited_status(output['sources'],output.get('unprocessed_urls',[]))
            if aggregate:
                output['partial']=aggregate['partial']
                if accepted:output.update(status=aggregate['status'],outcome=aggregate['outcome'])
            if unfinished:
                envelope['waited_unprocessed_urls']=unfinished
                envelope['waiting_errors']=wait_errors
                envelope['aggregate_status']=aggregate['status']
                output['request_record_path']=budget.save_request_record(store,run_id,local_id,envelope)
        return output
    except Exception as exc:
        if run_id:
            budget.abort_claims(store,run_id,claim['owner'],urls,error=str(exc))
            envelope['raw_response_path']=locals().get('discovery')
            envelope['provider_request_id']=response.get('request_id') if isinstance(response,dict) else None
            outcomes=[store.rows('SELECT source_id FROM page_claim_results WHERE owner=? AND url=?',
                                 (claim['owner'],url)) for url in urls]
            admitted=all(rows and rows[0]['source_id'] for rows in outcomes)
            envelope['outcome']='completed_with_error' if admitted else 'failed'
            envelope['failure_kind']='provider_error'
            path=budget.save_request_record(store,run_id,local_id,envelope)
            if round_id:
                from .research_plan import settle_request
                settle_request(store,run_id,local_id,'completed' if admitted else 'failed',
                               failure_kind='provider_error',record_path=path)
        raise
