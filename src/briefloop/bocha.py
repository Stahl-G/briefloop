"""Optional Bocha REST source discovery/extraction. Credentials stay outside workspaces."""
from . import __version__,websearch
import json
import os
import tempfile
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from .store import dump

NAME='bocha'
LABEL='博查'
FAILURE_KINDS=websearch.FAILURE_KINDS


class BochaError(websearch.SearchError):
    pass


ProviderError=BochaError


def _marked(message,kind='provider_error',status=None):
    if kind not in FAILURE_KINDS:raise ValueError('未知的 failure_kind：'+str(kind))
    error=BochaError(message);error.failure_kind=kind;error.status=status;return error


def _http_kind(code):
    if code in (401,403):return 'auth'
    if code==402:return 'quota'
    if code==429:return 'rate_limit'
    return 'provider_error'


def _key_path(key_file=None):
    return Path(key_file) if key_file is not None else Path.home()/'.config'/'briefloop'/'bocha.key'


def _read_key(key_file=None):
    key=os.environ.get('BOCHA_API_KEY','').strip()
    if key:return key,'environment'
    try:key=_key_path(key_file).read_text().strip()
    except FileNotFoundError:return '',None
    except OSError:raise BochaError('无法读取本机 Bocha 凭据文件') from None
    return (key,'file') if key else ('',None)


def key_status(*,key_file=None):
    key,source=_read_key(key_file)
    return {'configured':bool(key),'source':source}


def save_key(api_key,*,key_file=None):
    if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key.strip()):
        raise BochaError('请输入有效的 Bocha API Key')
    path=_key_path(key_file);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,temporary=tempfile.mkstemp(prefix='.bocha-',dir=path.parent)
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_search(query, options):
    if not isinstance(query,str) or not query.strip():raise _marked('搜索词不能为空','invalid_response')
    from .tavily import validate_search as validate
    parameters=validate(query,options)
    # Web Search does not implement Tavily's news/depth switches. Preserve what
    # was asked while documenting the effective mapping in the saved request.
    parameters['effective_topic']='web'
    parameters['effective_depth']='provider_default'
    return parameters


def call_search(query, parameters, *, key_file=None):
    key,_=_read_key(key_file)
    if not key:raise _marked('尚未配置博查 API Key，请在设置中填写','auth')
    times={'day':'oneDay','week':'oneWeek','month':'oneMonth','year':'oneYear'}
    start,end=parameters.get('start_date'),parameters.get('end_date')
    if start or end:
        freshness=(start or '1970-01-01')+'..'+(end or date.today().isoformat())
    else:freshness=times.get(parameters.get('time_range'),'noLimit')
    payload={'query':query,'freshness':freshness,'summary':True,'count':parameters['max_results']}
    for source,target in [('include_domains','include'),('exclude_domains','exclude')]:
        if parameters.get(source):payload[target]='|'.join(parameters[source])
    request=urllib.request.Request('https://api.bocha.cn/v1/web-search',data=dump(payload).encode(),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':f'BriefLoop/{__version__}'},method='POST')
    opener=urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=websearch.ssl_context()))
    try:
        with opener.open(request,timeout=65) as response:raw=response.read(25*1024*1024+1)
        if len(raw)>25*1024*1024:raise _marked('博查响应过大','too_large')
        if key.encode() in raw:raise _marked('响应包含凭据，未保存或展示','invalid_response')
        result=json.loads(raw)
        if not isinstance(result,dict):raise _marked('博查响应格式无效','invalid_response')
        code=result.get('code',200)
        if code!=200:
            kind='quota' if code==403 else _http_kind(code)
            raise _marked('博查请求失败（业务状态 '+str(code)+'）',kind,code)
        data=result.get('data')
        pages=data.get('webPages') if isinstance(data,dict) else None
        values=pages.get('value') if isinstance(pages,dict) else None
        if not isinstance(values,list):raise _marked('博查未返回网页列表','invalid_response')
        return result,raw,result.get('log_id'),result.get('usage')
    except urllib.error.HTTPError as exc:
        try:error_body=exc.read(8192).decode('utf-8',errors='replace')
        except OSError:error_body=''
        if exc.code==403 and 'not have enough money' in error_body:
            raise _marked('博查账户余额不足（HTTP 403），可使用本轮已允许的其他渠道','quota',403) from None
        raise _marked('博查请求失败（HTTP '+str(exc.code)+'）',_http_kind(exc.code),exc.code) from None
    except (urllib.error.URLError,OSError) as exc:
        raise websearch.network_failure(exc,'博查',BochaError) from None
    except (json.JSONDecodeError,UnicodeDecodeError):
        raise _marked('博查未返回有效 JSON','invalid_response') from None


def rows(parsed):
    return [{'title':item.get('name',''),'url':item['url'],'snippet':item.get('summary') or item.get('snippet',''),
             'kind':'search_snippet','score':None,'published_date':item.get('datePublished'),'site_name':item.get('siteName')}
            for item in parsed['data']['webPages']['value']
            if isinstance(item,dict) and isinstance(item.get('url'),str) and item['url'].startswith(('http://','https://'))]


def search_note():
    return '博查逐页摘要仅用于发现。用 add-url 登记并读取正文；公众号、小红书是否可读需逐条检查。topic/depth 使用博查网页搜索默认设置。'


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    from .sources import fetch,fetch_for_run
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10:raise BochaError('请提供1–10个URL')
    if run_id:websearch.check_run(store,run_id,NAME)
    result=[]
    for url in dict.fromkeys(urls):
        item=fetch_for_run(store,run_id,url) if run_id else fetch(store,url)
        result.append(item)
        if item.get('status')=='budget_exhausted':break
    return {'provider':NAME,'sources':result,'note':'正文通过直接抓取登记，不是博查摘要。'}
