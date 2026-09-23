"""Optional Zhipu REST source discovery/extraction. Credentials stay outside workspaces."""
from . import __version__,websearch
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from .store import dump
from .search_credentials import write_key

NAME='zhipu'
LABEL='智谱搜索'
FAILURE_KINDS=websearch.FAILURE_KINDS


class ZhipuError(websearch.SearchError):
    pass


ProviderError=ZhipuError


def _marked(message,kind='provider_error',status=None):
    if kind not in FAILURE_KINDS:raise ValueError('未知的 failure_kind：'+str(kind))
    error=ZhipuError(message);error.failure_kind=kind;error.status=status;return error


def _http_kind(code):
    if code in (401,403):return 'auth'
    if code==402:return 'quota'
    if code==429:return 'rate_limit'
    return 'provider_error'


def _key_path(key_file=None):
    return Path(key_file) if key_file is not None else Path.home()/'.config'/'briefloop'/'zhipu.key'


def _read_key(key_file=None):
    key=os.environ.get('ZHIPU_SEARCH_API_KEY','').strip()
    if key:return key,'environment'
    try:key=_key_path(key_file).read_text(encoding='utf-8').strip()
    except FileNotFoundError:return '',None
    except OSError:raise ZhipuError('无法读取本机 Zhipu 凭据文件') from None
    return (key,'file') if key else ('',None)


def key_status(*,key_file=None):
    key,source=_read_key(key_file)
    return {'configured':bool(key),'source':source}


def save_key(api_key,*,key_file=None):
    if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key.strip()):
        raise ZhipuError('请输入有效的 Zhipu API Key')
    write_key(_key_path(key_file),api_key.strip())
    return key_status(key_file=key_file)


def delete_key(*,key_file=None):
    try:_key_path(key_file).unlink()
    except FileNotFoundError:pass
    return key_status(key_file=key_file)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_search(query, options):
    from .tavily import validate_search as validate
    if not isinstance(query,str) or not query.strip() or len(query)>70:raise _marked('智谱搜索词须为1–70个字符','invalid_response')
    p=validate(query,options)
    engine=options.get('search_engine','search_std')
    if engine not in ('search_std','search_pro','search_pro_sogou','search_pro_quark'):raise _marked('未知智谱搜索引擎','invalid_response')
    if p.get('start_date') or p.get('end_date'):raise _marked('智谱不支持绝对日期过滤，请用 --time-range 并逐页核对日期','invalid_response')
    if p.get('exclude_domains') or len(p.get('include_domains',[]))>1:raise _marked('智谱仅支持单个白名单域名，不支持排除域名','invalid_response')
    if engine=='search_pro_quark' and p.get('include_domains'):raise _marked('夸克搜索不支持域名过滤','invalid_response')
    p.update(search_engine=engine,effective_topic='web',effective_depth='provider_default')
    return p


def call_search(query, parameters, *, key_file=None):
    key,_=_read_key(key_file)
    if not key:raise _marked('尚未配置智谱搜索 API Key，请在设置中填写','auth')
    engine=parameters['search_engine'];count=parameters['max_results']
    # Sogou's smallest upstream page is 10; candidate admission below still
    # observes the shared limit and retains the original response.
    if engine=='search_pro_sogou':count=10
    payload={'search_query':query,'search_engine':engine,'search_intent':False,'count':count,
             'search_recency_filter':{'day':'oneDay','week':'oneWeek','month':'oneMonth','year':'oneYear'}.get(parameters.get('time_range'),'noLimit'),
             'content_size':'medium'}
    if parameters.get('include_domains'):payload['search_domain_filter']=parameters['include_domains'][0]
    request=urllib.request.Request('https://open.bigmodel.cn/api/paas/v4/web_search',data=dump(payload).encode(),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':f'BriefLoop/{__version__}'},method='POST')
    opener=urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=websearch.ssl_context()))
    try:
        with opener.open(request,timeout=65) as response:raw=response.read(25*1024*1024+1)
        if len(raw)>25*1024*1024:raise _marked('智谱响应过大','too_large')
        if key.encode() in raw:raise _marked('响应包含凭据，未保存或展示','invalid_response')
        result=json.loads(raw)
        if not isinstance(result,dict) or not isinstance(result.get('search_result'),list):raise _marked('智谱未返回有效网页列表','invalid_response')
        return result,raw,result.get('request_id') or result.get('id'),result.get('usage')
    except urllib.error.HTTPError as exc:
        raise _marked('智谱搜索请求失败（HTTP '+str(exc.code)+'）',_http_kind(exc.code),exc.code) from None
    except (urllib.error.URLError,OSError) as exc:
        raise websearch.network_failure(exc,'智谱搜索',ZhipuError) from None
    except (json.JSONDecodeError,UnicodeDecodeError):raise _marked('智谱未返回有效 JSON','invalid_response') from None


def rows(parsed):
    return [{'title':item.get('title',''),'url':item['link'],'snippet':item.get('content',''),
             'kind':'search_snippet','score':None,'published_date':item.get('publish_date'),'site_name':item.get('media')}
            for item in parsed['search_result'] if isinstance(item,dict) and isinstance(item.get('link'),str)
            and item['link'].startswith(('http://','https://'))]


def search_note():
    return '智谱网页摘要仅用于发现，正文用 add-url 保存并读取。搜索引擎来自本轮冻结配置；topic/depth 使用网页搜索默认设置。'


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    from .sources import fetch,fetch_for_run
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10:raise ZhipuError('请提供1–10个URL')
    if run_id:websearch.check_run(store,run_id,NAME)
    result=[]
    for url in dict.fromkeys(urls):
        item=fetch_for_run(store,run_id,url) if run_id else fetch(store,url)
        result.append(item)
        if item.get('status')=='budget_exhausted':break
    return {'provider':NAME,'sources':result,'note':'正文通过直接抓取登记，不是智谱摘要。'}
