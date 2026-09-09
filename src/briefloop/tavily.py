"""Optional Tavily REST source discovery/extraction. Credentials stay outside workspaces."""
from . import __version__
import hashlib
import json
import os
import ssl
from pathlib import Path
import tempfile
import urllib.request
import urllib.error
from datetime import date
from .store import now,uid,content_hash,dump


class TavilyError(ValueError):
    pass


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


def _ssl_context():
    context=ssl.create_default_context()
    # Python.org macOS installs may have no default CA bundle. Add the system
    # trust bundle while retaining hostname and certificate verification.
    system_bundle=Path('/etc/ssl/cert.pem')
    if system_bundle.is_file():context.load_verify_locations(cafile=str(system_bundle))
    return context


def _post(endpoint,payload,*,key_file=None):
    key,_=_read_key(key_file)
    if not key:raise TavilyError('尚未配置 Tavily API Key，请在设置中填写')
    request=urllib.request.Request('https://api.tavily.com/'+endpoint,data=dump(payload).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':f'BriefLoop/{__version__}'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=65,context=_ssl_context()) as response:
            raw=response.read(25*1024*1024+1)
        if len(raw)>25*1024*1024:raise TavilyError('Tavily 响应过大，未保存不完整结果')
        if key.encode() in raw:raise TavilyError('Tavily 响应包含凭据信息，未保存或展示')
        result=json.loads(raw)
        if not isinstance(result,dict):raise TavilyError('Tavily 返回格式无效')
        return result,raw
    except urllib.error.HTTPError as exc:
        raise TavilyError('Tavily 请求失败（HTTP '+str(exc.code)+'）；请检查密钥、额度和请求参数') from None
    except (urllib.error.URLError,OSError) as exc:
        reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
        if isinstance(reason,ssl.SSLCertVerificationError):
            message='Tavily TLS 证书校验失败；请检查本机 CA 证书或代理证书配置，未关闭证书校验'
        elif isinstance(reason,ssl.SSLError):
            message='Tavily TLS 握手失败；请检查本机 TLS 或代理配置'
        elif isinstance(reason,TimeoutError):
            message='Tavily 请求超时；未自动重试'
        else:message='无法连接 Tavily；请检查网络或代理配置，未自动重试'
        raise TavilyError(message) from None
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise TavilyError('Tavily 未返回有效 JSON') from None


def check_run(store,run_id):
    run=store.one('runs',run_id)
    if not json.loads(run['requirements']).get('allow_web'):raise TavilyError('本轮未允许联网搜索')
    if store.search_provider_for_run(run_id)!='tavily':raise TavilyError('本轮搜索服务不是 Tavily，请在设置中选择后发起新任务')
    return run


def search(query,*,topic='general',time_range=None,start_date=None,end_date=None,include_domains=None,exclude_domains=None,max_results=5,search_depth='basic',key_file=None,store=None,run_id=None):
    if not isinstance(query,str) or not query.strip():raise TavilyError('搜索词不能为空')
    if topic not in ('general','news'):raise TavilyError('topic 必须是 general 或 news')
    if type(max_results) is not int or not 1<=max_results<=10:raise TavilyError('max_results 必须为 1–10')
    if search_depth not in ('basic','advanced'):raise TavilyError('search_depth 必须是 basic 或 advanced')
    if time_range not in (None,'day','week','month','year','d','w','m','y'):raise TavilyError('无效时间范围')
    for value in (start_date,end_date):
        if value:
            try:date.fromisoformat(value)
            except (ValueError,TypeError):raise TavilyError('日期使用 YYYY-MM-DD') from None
    if start_date and end_date and start_date>end_date:raise TavilyError('开始日期不能晚于结束日期')
    reservation=None
    if store is not None and run_id is not None:
        check_run(store,run_id)
        from . import research_budget as budget
        try:reservation=budget.reserve_search(store,run_id,max_results)
        except budget.BudgetExhausted as exc:return exc.result
        max_results=reservation['max_results']
    payload={'query':query,'topic':topic,'max_results':max_results,'search_depth':search_depth,'include_answer':False,'include_raw_content':False,'auto_parameters':False,'include_usage':True}
    for name,value in (('time_range',time_range),('start_date',start_date),('end_date',end_date),('include_domains',include_domains),('exclude_domains',exclude_domains)):
        if value:payload[name]=value
    try:
        result,raw=_post('search',payload,key_file=key_file)
    except TavilyError as exc:
        if reservation:
            budget.save_discovery(store,run_id,reservation['request_id'],dump({'status':'failed','query':query,'error':str(exc)}).encode())
        raise
    discovery=None
    if reservation:
        # Preserve the complete provider response before any candidate limiting.
        discovery=budget.save_discovery(store,run_id,reservation['request_id'],raw)
    results=[]
    for item in result.get('results',[]):
        if not isinstance(item,dict):continue
        results.append({'title':item.get('title',''),'url':item.get('url',''),'snippet':item.get('content',''),'kind':'search_snippet','score':item.get('score'),'published_date':item.get('published_date')})
    output={'provider':'tavily','query':query,'results':results,'usage':result.get('usage'),'request_id':result.get('request_id'),'note':'搜索摘要仅用于发现来源。请读取原网页或用 tavily-extract 保存提供方提取正文后再引用。'}
    if reservation:
        admitted=budget.record_candidates(store,run_id,[row['url'] for row in results])
        allowed=set(admitted['allowed_urls'])
        output.update({'results':[row for row in results if budget.canonical_url(row['url']) in allowed],
                       'status':'budget_exhausted' if admitted['unadmitted_urls'] else 'ok',
                       'unadmitted_urls':admitted['unadmitted_urls'],'discovery_path':discovery,
                       'remaining':admitted['budget']['remaining'],'budget':admitted['budget']})
        if admitted['unadmitted_urls']:
            output['message']='候选 URL 预算已用完；未纳入的 URL 和完整搜索响应已保留，不继续扩大检索'
    return output


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10 or not all(isinstance(url,str) and url.startswith(('https://','http://')) for url in urls):raise TavilyError('请提供 1–10 个 HTTP(S) 来源地址')
    if extract_depth not in ('basic','advanced'):raise TavilyError('无效提取深度')
    cached=[]
    if run_id:
        check_run(store,run_id)
        from .sources import existing_for_run
        from . import research_budget as budget
        pending=[]
        for url in dict.fromkeys(urls):
            previous=existing_for_run(store,run_id,url)
            if previous:cached.append({**previous,'reused':True})
            else:pending.append(url)
        if not pending:return {'provider':'tavily','sources':cached,'reused':True,'budget':budget.snapshot(store,run_id)}
        try:budget.reserve_pages(store,run_id,pending)
        except budget.BudgetExhausted as exc:return {**exc.result,'sources':cached,'unprocessed_urls':pending}
        urls=pending
    # No query/chunks_per_source: retain the full provider-extracted content.
    response,raw=_post('extract',{'urls':urls,'extract_depth':extract_depth,'format':'markdown','include_usage':True},key_file=key_file)
    results=list(cached)
    for url in dict.fromkeys(urls):
        item=next((x for x in response.get('results',[]) if isinstance(x,dict) and x.get('url')==url),None)
        if item is None and len(urls)==1 and len(response.get('results',[]))==1:item=response['results'][0]
        text=item.get('raw_content','') if isinstance(item,dict) else ''
        if not isinstance(text,str):text=''
        error=None if text.strip() else 'Tavily 未能提取可读正文；原始提供方响应已保留'
        sid=uid('src');original=store.root/'sources'/(sid+'.original.tavily.json');original.write_bytes(raw)
        provenance={'url':url,'content_type':'application/json','fetched_at':now(),'raw_sha256':hashlib.sha256(raw).hexdigest(),'text_sha256':content_hash(text),'extractor':'tavily.extract','original_path':str(original.relative_to(store.root)),'extraction_status':'failed' if error else 'ready','original_kind':'provider_response','provider':'tavily','notice':'保存的是 Tavily 提供方响应及提取正文，不是原网站 HTML/PDF 字节。','request_id':response.get('request_id')}
        if error:provenance['error']=error
        if item and item.get('url')!=url:provenance['provider_url']=item.get('url')
        (store.root/'sources'/(sid+'.provenance.json')).write_text(dump(provenance))
        source=store.add_source((item.get('title') if item else None) or url.rsplit('/',1)[-1] or url,text,url=url,error=error,source_id=sid)
        if run_id:store.attach_source(run_id,sid)
        results.append({**source,'provenance':provenance})
    return {'provider':'tavily','sources':results,'usage':response.get('usage'),'request_id':response.get('request_id'),**({'budget':budget.snapshot(store,run_id)} if run_id else {})}
