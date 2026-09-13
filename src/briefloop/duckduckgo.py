"""Keyless DuckDuckGo HTML search adapter. No credentials, no third-party deps.

Search posts to the classic html.duckduckgo.com form endpoint and parses result
anchors (interface shape follows gpt-researcher's retrievers/duckduckgo; the
implementation is written here against the stdlib only). DuckDuckGo has no
extraction service, so extract() registers pages through the same metered
direct-fetch path as add-url and keeps budget counting identical.
"""
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from . import websearch
from .websearch import marked

NAME='duckduckgo'
LABEL='DuckDuckGo'
# The HTML endpoint is the no-JS interface; it expects a browser-style agent.
_USER_AGENT='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.5 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'
_ENDPOINT='https://html.duckduckgo.com/html/'
_MAX_RESPONSE=5*1024*1024
_TIME_FILTERS={'day':'d','week':'w','month':'m','year':'y','d':'d','w':'w','m':'m','y':'y'}


class _Results(HTMLParser):
    """Collect result titles (result__a) and snippets (result__snippet) in order."""
    def __init__(self):
        super().__init__();self.rows=[];self.current=None;self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag!='a':return
        classes=(dict(attrs).get('class') or '').split()
        if 'result__a' in classes or 'result__snippet' in classes:
            self.current={'kind':'result__a' if 'result__a' in classes else 'result__snippet',
                          'href':dict(attrs).get('href'),'text':''}
            self.text=[]
    def handle_data(self,data):
        if self.current is not None:self.text.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            self.current['text']=' '.join(''.join(self.text).split())
            self.rows.append(self.current);self.current=None


def _target_url(href):
    """Resolve DuckDuckGo redirect links to their target URL."""
    if not href:return None
    href=unescape(href.strip())
    absolute=href if href.startswith(('https://','http://')) else 'https://duckduckgo.com'+href
    parts=urllib.parse.urlsplit(absolute)
    target=urllib.parse.parse_qs(parts.query).get('uddg',[None])[0]
    if target:return urllib.parse.unquote(target)
    return absolute if parts.netloc and 'duckduckgo.com' not in parts.netloc else None


def _parse(text):
    parser=_Results();parser.feed(text);parser.close()
    titles=[row for row in parser.rows if row['kind']=='result__a']
    snippets=[row for row in parser.rows if row['kind']=='result__snippet']
    results=[]
    for index,title in enumerate(titles):
        url=_target_url(title['href'])
        if not url:continue
        results.append({'title':title['text'],'url':url,
                        'snippet':snippets[index]['text'] if index<len(snippets) else '',
                        'kind':'search_snippet','score':None,'published_date':None})
    if not results:
        if 'no-results' in text or 'No results' in text:return []
        raise marked('未能从 DuckDuckGo 响应解析出任何结果；页面可能被反爬拦截或结构已变化，请更换查询词或改选其他搜索源','invalid_response')
    return results


def _post(form):
    data=urllib.parse.urlencode(form).encode()
    request=urllib.request.Request(_ENDPOINT,data=data,method='POST',headers={
        'Content-Type':'application/x-www-form-urlencoded','User-Agent':_USER_AGENT,
        'Accept':'text/html,application/xhtml+xml','Accept-Language':'en-US,en;q=0.8'})
    try:
        with urllib.request.urlopen(request,timeout=30,context=websearch.ssl_context()) as response:
            raw=response.read(_MAX_RESPONSE+1)
        if len(raw)>_MAX_RESPONSE:raise marked('DuckDuckGo 响应过大，未保存不完整结果','too_large')
        return raw.decode('utf-8','replace'),raw
    except urllib.error.HTTPError as exc:
        if exc.code in (403,429):
            raise marked('DuckDuckGo 拒绝了本次查询（HTTP '+str(exc.code)+'），通常是被限流或反爬拦截；请更换查询词或稍后重试','rate_limit',exc.code) from None
        raise marked('DuckDuckGo 请求失败（HTTP '+str(exc.code)+'）；请稍后重试或改选其他搜索源','provider_error',exc.code) from None
    except (urllib.error.URLError,OSError) as exc:
        raise websearch.network_failure(exc,LABEL) from None


def validate_search(query,options):
    if not isinstance(query,str) or not query.strip():raise marked('搜索词不能为空')
    if options['topic']!='general':raise marked('DuckDuckGo 适配器不支持 topic='+str(options['topic'])+'；请改用 general 或在设置中选择 Tavily')
    max_results=options['max_results']
    if type(max_results) is not int or not 1<=max_results<=10:raise marked('max_results 必须为 1–10')
    if options['search_depth'] not in ('basic','advanced'):raise marked('search_depth 必须是 basic 或 advanced')
    time_range=options['time_range']
    if time_range is not None and time_range not in _TIME_FILTERS:raise marked('无效时间范围')
    if options['start_date'] or options['end_date']:raise marked('DuckDuckGo 适配器不支持绝对日期过滤；请用 time-range 相对范围或改选 Tavily')
    for name in ('include_domains','exclude_domains'):
        values=options[name] or []
        if not isinstance(values,list) or any(not isinstance(domain,str) or not domain.strip() for domain in values):
            raise marked('域名过滤必须是文本列表')
    return {'topic':'general','max_results':max_results,'search_depth':options['search_depth'],
            'include_domains':list(options['include_domains'] or []),'exclude_domains':list(options['exclude_domains'] or []),
            'time_range':time_range,'start_date':None,'end_date':None}


def call_search(query,parameters,*,key_file=None):
    # Domain filters map to DuckDuckGo's site:/-site: query operators.
    terms=[query.strip()]
    for domain in parameters['include_domains']:terms.append('site:'+domain.strip())
    for domain in parameters['exclude_domains']:terms.append('-site:'+domain.strip())
    form={'q':' '.join(terms),'kl':'wt-wt'}
    if parameters['time_range']:form['df']=_TIME_FILTERS[parameters['time_range']]
    text,raw=_post(form)
    return {'results':_parse(text)},raw,None,None


def rows(parsed):
    return list(parsed.get('results',[]))


def search_note():
    return '搜索摘要仅用于发现来源。请读取原网页或用 add-url 登记正文后再引用；DuckDuckGo 摘要不提供相关性分数，也未提取发布日期。'


def extract(store,urls,*,run_id=None,extract_depth='basic',key_file=None):
    """Register pages via the metered direct-fetch path; same source_pages budget
    as Tavily extract, with the website's own bytes kept as the original."""
    from . import sources
    if isinstance(urls,str):urls=[urls]
    if not isinstance(urls,list) or not urls or len(urls)>10 or not all(isinstance(url,str) and url.startswith(('https://','http://')) for url in urls):
        raise marked('请提供 1–10 个 HTTP(S) 来源地址')
    unique=list(dict.fromkeys(urls))
    if not run_id:
        fetched=[sources.fetch(store,url) for url in unique]
        return {'provider':NAME,'sources':fetched,'outcome':'success','failure_kind':None,'usage':None,'request_id':None,
                'local_request_id':None,'provider_request_id':None,'round_id':None,
                'extraction_failed_urls':[row.get('url') for row in fetched if row.get('error')],
                'request_record_path':None,'note':'DuckDuckGo 没有提供方提取服务；正文按直接抓取登记，等同 add-url。'}
    websearch.check_run(store,run_id,NAME)
    from .store import uid
    from . import research_budget as budget
    local_id=uid('extract')
    envelope={'local_request_id':local_id,'provider_request_id':None,'query_id':None,'run_id':run_id,'round_id':None,
              'provider':NAME,'operation':'extract','query':None,
              'parameters':{'urls':unique,'method':'direct_fetch','extract_depth':extract_depth},'outcome':None,
              'failure_kind':None,'raw_response_path':None,'admitted_urls':[],'unadmitted_urls':[],'budget_after':{}}
    results=[]
    for url in unique:
        item=sources.fetch_for_run(store,run_id,url)
        if item.get('status')=='budget_exhausted':
            processed={row.get('url') for row in results}
            envelope['outcome']='budget_exhausted';envelope['failure_kind']='budget'
            envelope['unadmitted_urls']=[value for value in unique if value not in processed]
            envelope['budget_after']=item.get('budget',{})
            path=budget.save_request_record(store,run_id,local_id,envelope)
            return {**item,'provider':NAME,'sources':results,
                    'unprocessed_urls':envelope['unadmitted_urls'],'local_request_id':local_id,'request_record_path':path,
                    'note':'DuckDuckGo 没有提供方提取服务；正文按直接抓取登记，等同 add-url。'}
        results.append(item)
    envelope['outcome']='success';envelope['failure_kind']=None
    envelope['admitted_urls']=[row.get('url') for row in results if row.get('url')]
    envelope['budget_after']=budget.snapshot(store,run_id)
    output={'provider':NAME,'sources':results,'outcome':'success','failure_kind':None,'usage':None,'request_id':None,
            'local_request_id':local_id,'provider_request_id':None,'round_id':None,
            'extraction_failed_urls':[row.get('url') for row in results if row.get('status')=='failed'],
            'request_record_path':None,'budget':envelope['budget_after'],
            'note':'DuckDuckGo 没有提供方提取服务；正文按直接抓取登记，等同 add-url。'}
    output['request_record_path']=budget.save_request_record(store,run_id,local_id,envelope)
    return output
