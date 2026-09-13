"""Optional Tavily REST source discovery/extraction. Credentials stay outside workspaces."""
from . import __version__,websearch
import hashlib
import json
import os
import tempfile
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from .store import now,uid,content_hash,dump

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
    try:key=_key_path(key_file).read_text().strip()
    except FileNotFoundError:return '',None
    except OSError:raise TavilyError('无法读取本机 Tavily 凭据文件') from None
    return (key,'file') if key else ('',None)


def key_status(*,key_file=None):
    key,source=_read_key(key_file)
    return {'configured':bool(key),'source':source}


def save_key(api_key,*,key_file=None):
    if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key.strip()):
        raise TavilyError('请输入有效的 Tavily API Key')
    path=_key_path(key_file);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,temporary=tempfile.mkstemp(prefix='.tavily-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w') as file:file.write(api_key.strip())
        os.chmod(temporary,0o600);os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return key_status(key_file=key_file)


def delete_key(*,key_file=None):
    try:_key_path(key_file).unlink()
    except FileNotFoundError:pass
    return key_status(key_file=key_file)


# Certificate handling is shared with other providers; verification stays on.
_ssl_context=websearch.ssl_context


def _post(endpoint,payload,*,key_file=None):
    key,_=_read_key(key_file)
    if not key:raise TavilyError('尚未配置 Tavily API Key，请在设置中填写')
    request=urllib.request.Request('https://api.tavily.com/'+endpoint,data=dump(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':f'BriefLoop/{__version__}'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=65,context=_ssl_context()) as response:
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


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10 or not all(isinstance(url,str) and url.startswith(('https://','http://')) for url in urls):raise TavilyError('请提供 1–10 个 HTTP(S) 来源地址')
    if extract_depth not in ('basic','advanced'):raise TavilyError('无效提取深度')
    cached=[];local_id=None;round_id=None
    if run_id:
        check_run(store,run_id)
        from .sources import existing_for_run
        from . import research_budget as budget
        local_id=uid('extract')
        pending=[]
        for url in dict.fromkeys(urls):
            previous=existing_for_run(store,run_id,url)
            if previous:cached.append({**previous,'reused':True})
            else:pending.append(url)
        if not pending:return {'provider':NAME,'sources':cached,'reused':True,'budget':budget.snapshot(store,run_id)}
        try:reservation=budget.reserve_pages(store,run_id,pending,request_id=local_id)
        except budget.BudgetExhausted as exc:
            envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':None,'provider':NAME,'operation':'extract','query':None,'parameters':{'urls':pending,'extract_depth':extract_depth,'format':'markdown'},'outcome':'budget_exhausted','failure_kind':'budget','raw_response_path':None,'admitted_urls':[],'unadmitted_urls':pending,'budget_after':exc.result.get('budget',{})}
            path=budget.save_request_record(store,run_id,local_id,envelope)
            return {**exc.result,'sources':cached,'unprocessed_urls':pending,'local_request_id':local_id,'request_record_path':path}
        round_id=reservation['round_id']
        urls=pending
    parameters={'urls':list(dict.fromkeys(urls)),'extract_depth':extract_depth,'format':'markdown'}
    envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':round_id,'provider':NAME,'operation':'extract','query':None,'parameters':parameters,'outcome':None,'failure_kind':None,'raw_response_path':None,'admitted_urls':[],'unadmitted_urls':[],'budget_after':{}}
    try:
        response,raw=_post('extract',{'urls':urls,'extract_depth':extract_depth,'format':'markdown','include_usage':True},key_file=key_file)
    except TavilyError as exc:
        envelope['outcome']='failed';envelope['failure_kind']=getattr(exc,'failure_kind','provider_error')
        if local_id:
            path=budget.save_request_record(store,run_id,local_id,envelope)
            exc.request_record_path=path
            if round_id:
                from .research_plan import settle_request
                settle_request(store,run_id,local_id,'failed',failure_kind=envelope['failure_kind'],record_path=path)
        raise
    results=list(cached);failed=[]
    for url in dict.fromkeys(urls):
        item=next((x for x in response.get('results',[]) if isinstance(x,dict) and x.get('url')==url),None)
        if item is None and len(urls)==1 and len(response.get('results',[]))==1:item=response['results'][0]
        text=item.get('raw_content','') if isinstance(item,dict) else ''
        if not isinstance(text,str):text=''
        error=None if text.strip() else 'Tavily 未能提取可读正文；原始提供方响应已保留'
        sid=uid('src');original=store.root/'sources'/(sid+'.original.tavily.json');original.write_bytes(raw)
        provenance={'url':url,'content_type':'application/json','fetched_at':now(),'raw_sha256':hashlib.sha256(raw).hexdigest(),'text_sha256':content_hash(text),'extractor':'tavily.extract','original_path':str(original.relative_to(store.root)),'extraction_status':'failed' if error else 'ready','original_kind':'provider_response','provider':NAME,'notice':'保存的是 Tavily 提供方响应及提取正文，不是原网站 HTML/PDF 字节。','request_id':response.get('request_id')}
        if error:provenance['error']=error;failed.append(url)
        if item and item.get('url')!=url:provenance['provider_url']=item.get('url')
        (store.root/'sources'/(sid+'.provenance.json')).write_text(dump(provenance))
        source=store.add_source((item.get('title') if item else None) or url.rsplit('/',1)[-1] or url,text,url=url,error=error,source_id=sid)
        if run_id:store.attach_source(run_id,sid)
        results.append({**source,'provenance':provenance})
    envelope['provider_request_id']=response.get('request_id');envelope['outcome']='success'
    envelope['admitted_urls']=list(dict.fromkeys(urls));envelope['unadmitted_urls']=[]
    envelope['extraction_failed_urls']=failed
    output={'provider':NAME,'sources':results,'usage':response.get('usage'),'request_id':response.get('request_id'),
            'local_request_id':local_id,'provider_request_id':response.get('request_id'),'outcome':'success','failure_kind':None,
            'round_id':round_id,
            'extraction_failed_urls':failed,'request_record_path':None}
    if run_id:
        envelope['raw_response_path']=budget.save_discovery(store,run_id,local_id,raw)
        envelope['budget_after']=budget.snapshot(store,run_id)
        output['budget']=envelope['budget_after']
        output['request_record_path']=budget.save_request_record(store,run_id,local_id,envelope)
        if round_id:
            from .research_plan import settle_request
            settle_request(store,run_id,local_id,'completed',record_path=output['request_record_path'])
    return output
