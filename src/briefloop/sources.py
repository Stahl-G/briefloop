"""Small source readers. Preserve originals; extraction failures stay visible."""
from html.parser import HTMLParser
from html import unescape
from . import __version__
from .host_bins import find as find_host_bin
from io import BytesIO
from urllib.parse import urlsplit
from pathlib import Path
from concurrent.futures import Future
import hashlib
import http.client
import ipaddress
import re
import socket
import subprocess
import os
import tempfile
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
import threading

TITLE_MAX_CHARS=200
# Interstitial/anti-bot/error titles are not source labels.
_GENERIC_TITLE_RE=re.compile(r'^(?:just a moment|attention required|access denied|access to this page has been denied|are you a robot|verify you are human|checking your browser|enable javascript|403 forbidden|404 not found|429 too many requests|too many requests|service unavailable|bad gateway)\b',re.I)
# Rejecting a source needs an entire interstitial title, not an article prefix.
# Permit terminal punctuation and the known Cloudflare brand, not arbitrary text.
_ACCESS_TITLE_RE=re.compile(_GENERIC_TITLE_RE.pattern+r'[.!?…]*(?:\s*[|–—-]\s*Cloudflare)?',re.I)
_LOGIN_TITLE_RE=re.compile(r'(?:sign in|log in|login|登录)[.!?…]*',re.I)

# This is deliberately an in-process, per-workspace/run registry, not a content
# cache.  It only joins callers while one snapshot is being created.
_INFLIGHT_FETCHES={}
_INFLIGHT_FETCHES_LOCK=threading.Lock()


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript'): self.skip += 1
        if tag in ('p','div','br','li','tr','h1','h2','h3'): self.parts.append('\n')
        if tag in ('td','th'):self.parts.append('\t')
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript') and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def html_text(data):
    p=TextHTML();p.feed(data)
    return '\n'.join(line.strip() for line in ''.join(p.parts).splitlines() if line.strip())


def html_title(data, content_type='', encoding=''):
    """Best-effort page <title>, used as a human-readable source label.

    Anti-bot and error page titles are ignored so an interstitial cannot
    rename a source; the result is length-capped for the source library.
    """
    if 'html' not in (content_type or '').lower():return ''
    try:text=data.decode(encoding or 'utf-8','ignore')
    except (LookupError,UnicodeDecodeError):text=data.decode('utf-8','ignore')
    match=re.search(r'<title[^>]*>(.*?)</title>',text,re.I|re.S)
    if not match:return ''
    title=re.sub(r'\s+',' ',unescape(match.group(1))).strip()
    if not title or _GENERIC_TITLE_RE.match(title):return ''
    return title[:TITLE_MAX_CHARS]


def _html_block_reason(data, content_type='', encoding=''):
    """Return a reason only for a high-confidence HTML access interstitial.

    A generic title is intentionally not enough: short public notices and
    articles describing challenges are valid sources.  We require independent
    page evidence (challenge markup/text, or an actual password form) before
    marking the retained HTTP response as an extraction failure.
    """
    try:page=data.decode(encoding or 'utf-8','ignore')
    except (LookupError,UnicodeDecodeError):page=data.decode('utf-8','ignore')
    # A missing or incorrect Content-Type must not turn an otherwise readable
    # HTML interstitial into report text.  Keep this sniff deliberately narrow.
    if 'html' not in (content_type or '').lower() and not re.match(r'^\s*(?:<!doctype\s+html|<html\b|<head\b|<title\b)',page,re.I):return None
    title_match=re.search(r'<title[^>]*>(.*?)</title>',page,re.I|re.S)
    title=re.sub(r'\s+',' ',unescape(title_match.group(1))).strip() if title_match else ''
    # Evidence must come from outside <title>/<head>: otherwise a normal
    # article title such as “Checking your browser performance” self-confirms.
    body=re.sub(r'<head\b[^>]*>.*?</head\s*>|<title\b[^>]*>.*?</title\s*>','',page,flags=re.I|re.S).lower()
    challenge_markers=('checking your browser','verify you are human','enable javascript and cookies',
                       'cf-chl-','challenge-platform','captcha')
    if _ACCESS_TITLE_RE.fullmatch(title) and any(marker in body for marker in challenge_markers):
        return '网页返回访问拦截页，未保存为可用正文'
    has_password=bool(re.search(r'<input\b[^>]*\btype\s*=\s*["\']?password\b',body,re.I))
    has_form='<form' in body
    if _LOGIN_TITLE_RE.fullmatch(title) and has_form and has_password:
        return '网页返回登录页，未保存为可用正文'
    return None


def extract(name, data, *, with_extractor=False):
    extractor="text decode utf-8-sig/gb18030"
    ext=Path(name).suffix.lower()
    if ext == '.pdf':
        from .media import pdf_metadata,PDF_NOTICE
        pages=pdf_metadata(data)['pages']
        extractor='pdftotext -layout'
        pdftotext=find_host_bin('pdftotext')
        if pdftotext:
            with tempfile.TemporaryDirectory(prefix='briefloop-read-') as tmp:
                p=Path(tmp)/'source.pdf';p.write_bytes(data)
                proc=subprocess.run([pdftotext,'-layout',str(p),'-'],stdin=subprocess.DEVNULL,capture_output=True,timeout=min(600,max(90,pages)))
                text=proc.stdout.decode('utf-8',errors='replace') if proc.returncode==0 else ''
        else:text=''
        if not text.strip():
            try:
                from pypdf import PdfReader
                extractor='pypdf.PdfReader.extract_text'
                text='\n\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(data)).pages)
            except Exception as exc:raise ValueError('PDF 正文提取失败，原件已保留') from exc
        if not text.strip():text=PDF_NOTICE;extractor+=' (visual reading required; no OCR)'
    elif ext == '.xlsx':
        from .workbook_figures import workbook_text
        try:text=workbook_text(data)
        except (KeyError,zipfile.BadZipFile,ET.ParseError) as exc:
            raise ValueError('XLSX 无法读取工作簿，文件可能已损坏') from exc
        extractor='XLSX cells and saved formula values (no recalculation)'
    elif ext == '.docx':
        extractor='DOCX word/document.xml paragraph text'
        try:
            from .media import office_archive
            with office_archive(data) as z:
                doc=ET.fromstring(z.read('word/document.xml'))
                text='\n'.join(''.join(n.itertext()) for n in doc.iter() if n.tag.endswith('}p'))
        except (KeyError,zipfile.BadZipFile,ET.ParseError) as exc:
            raise ValueError('DOCX 无法读取正文，文件可能已损坏') from exc
    elif ext in ('.html','.htm'):
        extractor='briefloop.sources.TextHTML (utf-8)'
        text=html_text(data.decode('utf-8',errors='replace'))
    else:
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('gb18030')
    if not text.strip(): raise ValueError('未能读取正文')
    return (text,extractor) if with_extractor else text


def _office_suffix(name, data, content_type=''):
    mime=content_type.split(';',1)[0].strip().lower()
    known={'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'.xlsx',
           'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'.docx'}
    if mime in known:return known[mime]
    suffix=Path(name).suffix.lower()
    if suffix in ('.xlsx','.docx'):return suffix
    if data.startswith(b'PK'):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names=set(archive.namelist())
                if 'xl/workbook.xml' in names:return '.xlsx'
                if 'word/document.xml' in names:return '.docx'
        except zipfile.BadZipFile:pass
    return None


def _source_content(store, name, data, *, content_type='', encoding='utf-8'):
    from . import media
    kind=media.detect_media_type(name,data,content_type)
    metadata={'media_type':kind,'needs_visual':False,'pages':None}
    if kind.startswith('image/'):
        metadata.update(media.prepare_image(store,data))
        return media.IMAGE_NOTICE,'Pillow image validation / EXIF transpose (no OCR)',metadata
    if kind=='application/pdf':
        metadata.update(media.pdf_metadata(data))
        text,extractor=extract('source.pdf',data,with_extractor=True)
        metadata['needs_visual']=text==media.PDF_NOTICE
        return text,extractor,metadata
    office=_office_suffix(name,data,content_type)
    if office:
        text,extractor=extract('source'+office,data,with_extractor=True)
        return text,extractor,metadata
    if data.startswith(b'PK'):
        raise ValueError('不支持的 ZIP 文件，不能作为文本读取')
    if content_type and kind=='text/html':
        return html_text(data.decode(encoding,errors='replace')),'briefloop.sources.TextHTML ('+encoding+')',metadata
    if content_type:
        return data.decode(encoding,errors='replace'),'text decode ('+encoding+')',metadata
    text,extractor=extract(name,data,with_extractor=True)
    return text,extractor,metadata


def upload(store, name, data):
    from .store import uid,now,content_hash,dump
    from .media import detect_media_type,safe_source_path
    sid=uid('src');name=Path(name).name
    original=safe_source_path(store,'sources/'+sid+'.original'+Path(name).suffix.lower(),must_exist=False)
    original.write_bytes(data)
    metadata={'original_path':str(original.relative_to(store.root)), 'original_kind':'uploaded_file',
              'uploaded_at':now(),'raw_sha256':hashlib.sha256(data).hexdigest(),
              'media_type':detect_media_type(name,data),'needs_visual':False,'pages':None}
    text='';error=None;extractor='source extraction'
    try:
        text,extractor,details=_source_content(store,name,data)
        metadata.update(details)
        if not text.strip():raise ValueError('未能读取正文')
    except (ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:error=str(exc);text=''
    metadata.update({'extractor':extractor,'text_sha256':content_hash(text),'extraction_status':'failed' if error else 'ready'})
    if error:metadata['error']=error
    safe_source_path(store,'sources/'+sid+'.provenance.json',must_exist=False).write_text(dump(metadata),encoding='utf-8')
    return store.add_source(name,text,error=error,source_id=sid)


MAX_REDIRECTS=5
_NAT64=ipaddress.ip_network('64:ff9b::/96')
# Clash/Surge fake-IP DNS answers public names from the benchmarking range.
_PROXY_FAKE_IP=ipaddress.ip_network('198.18.0.0/15')
# Intranet pages a user adds by hand; never loopback, link-local or metadata.
_PRIVATE_NETWORKS=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','fc00::/7'))
# Octal, hex, integer or short IPv4 spellings are parsed differently by
# resolvers and curl; only canonical address literals are accepted.
_NUMERIC_HOST=re.compile(r'(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}\.?',re.I)


def _allowed_ip(value,allow_private=False):
    ip=ipaddress.ip_address(value.split('%',1)[0])
    if ip.version==6:
        embedded=ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is None and ip in _NAT64:embedded=ipaddress.IPv4Address(int(ip)&0xffffffff)
        if embedded is not None:ip=embedded
    if ip.is_multicast:return False
    if ip.is_global or ip.version==4 and ip in _PROXY_FAKE_IP:return True
    return allow_private and any(ip.version==n.version and ip in n for n in _PRIVATE_NETWORKS)


def _checked_addresses(host,port,allow_private=False):
    """Resolve once and refuse this machine, private networks and other non-global addresses.

    Source URLs may come from search results or model tool calls; the local
    BriefLoop API and LAN services must not become research sources. Only a
    user's own request may reach private (intranet) networks.
    """
    if _NUMERIC_HOST.fullmatch(host):
        try:ipaddress.IPv4Address(host)
        except ValueError:raise ValueError('来源地址的 IP 写法不规范，已拒绝读取') from None
    try:infos=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except (OSError,UnicodeError,ValueError) as exc:raise ValueError('无法解析来源地址') from exc
    addresses=list(dict.fromkeys(info[4][0] for info in infos))
    # Every answer must be public: a mixed answer can still connect locally.
    if not addresses or not all(_allowed_ip(address,allow_private) for address in addresses):
        raise ValueError('来源地址指向本机或内网，已拒绝读取')
    return addresses


def _public_target(url,allow_private=False):
    parts=urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname:raise ValueError('请输入 HTTP(S) 来源地址')
    try:port=parts.port or (443 if parts.scheme=='https' else 80)
    except ValueError as exc:raise ValueError('无法解析来源地址') from exc
    return parts.hostname,port,_checked_addresses(parts.hostname,port,allow_private)


def _checked_connection(base,allow_private):
    """An http.client connection that connects only to the addresses it just checked."""
    class Connection(base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self._create_connection=self._connect_checked
        def _connect_checked(self,address,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None):
            host,port=address;error=None
            for checked in _checked_addresses(host,port,allow_private):
                try:return socket.create_connection((checked,port),timeout,source_address)
                except OSError as exc:error=exc
            raise error
    return Connection


class _CheckedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self,allow_private=False):
        super().__init__();self.allow_private=allow_private
    def http_open(self,req):
        # A configured proxy resolves the target itself; the URL was checked before sending.
        if req.has_proxy():return super().http_open(req)
        return self.do_open(_checked_connection(http.client.HTTPConnection,self.allow_private),req)


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self,allow_private=False,context=None):
        super().__init__(context=context);self.allow_private=allow_private
    def https_open(self,req):
        if req.has_proxy() or req._tunnel_host:return super().https_open(req)
        return self.do_open(_checked_connection(http.client.HTTPSConnection,self.allow_private),req,context=self._context)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections=MAX_REDIRECTS
    def __init__(self,allow_private=False):
        self.allow_private=allow_private
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _public_target(newurl,self.allow_private)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _fetch_bytes(url,*,allow_private=False):
    host,port,addresses=_public_target(url,allow_private)
    curl=find_host_bin('curl')
    if curl:
        env=dict(os.environ)
        for key,value in urllib.request.getproxies().items():
            if key in ('http','https','all'):env.setdefault(key+'_proxy',value)
        with tempfile.TemporaryDirectory(prefix='briefloop-web-') as tmp:
            path=Path(tmp)/'response'
            for hop in range(MAX_REDIRECTS+1):
                if hop:host,port,addresses=_public_target(url,allow_private)
                command=[curl,'--fail','--silent','--show-error','--max-redirs','0','--proto','=http,https','--connect-timeout','12','--max-time','40','--max-filesize',str(15*1024*1024),'-A',f'BriefLoop/{__version__} (local research reader)','-o',str(path),'-w','%{http_code}\\n%{redirect_url}\\n%{content_type}']
                try:ipaddress.ip_address(host)
                except ValueError:
                    # Connect to the addresses just checked, not a second DNS answer.
                    command+=['--resolve',f'{host}:{port}:'+','.join(f'[{a}]' if ':' in a else a for a in addresses)]
                proc=subprocess.run(command+[url],stdin=subprocess.DEVNULL,capture_output=True,text=True,env=env,timeout=45)
                if proc.returncode:raise ValueError(proc.stderr.strip() or '网页读取失败')
                status,location,content_type=(proc.stdout.split('\n',2)+['',''])[:3]
                if not (status.startswith('3') and location):break
                url=location
            else:raise ValueError('网页重定向次数过多')
            data=path.read_bytes() if path.exists() else b'';encoding='utf-8'
    else:
        req=urllib.request.Request(url,headers={'User-Agent':f'BriefLoop/{__version__} (local research reader)'})
        from .websearch import ssl_context
        opener=urllib.request.build_opener(_PublicRedirects(allow_private),_CheckedHTTPHandler(allow_private),_CheckedHTTPSHandler(allow_private,context=ssl_context()))
        with opener.open(req,timeout=40) as response:
            data=response.read(15*1024*1024+1)
            content_type=response.headers.get('Content-Type','')
            encoding=response.headers.get_content_charset() or 'utf-8'
    if len(data)>15*1024*1024:raise ValueError('网页过大，请下载后上传')
    # curl reports Content-Type but does not separately report the charset.
    if 'charset=' in content_type.lower():
        from email.message import Message
        header=Message();header['Content-Type']=content_type
        encoding=header.get_content_charset() or encoding
    return data,content_type,encoding


def _fetch_suffix(name,data,content_type):
    from .media import detect_media_type
    kind=detect_media_type(name,data,content_type)
    office=_office_suffix(name,data,content_type)
    if office:return office
    return {'application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp',
            'image/gif':'.gif','image/tiff':'.tiff','image/bmp':'.bmp','text/html':'.html','text/plain':'.txt'}.get(kind,'.bin')


def _fetch(store, url, *, allow_private=False):
    from .store import uid,now,content_hash,dump
    from .media import detect_media_type,safe_source_path
    from urllib.parse import urlsplit,unquote
    data,content_type,encoding=_fetch_bytes(url,allow_private=allow_private)
    sid=uid('src');raw_name=Path(unquote(urlsplit(url).path)).name or '网页'
    title=html_title(data,content_type,encoding)
    name=title or raw_name
    suffix=_fetch_suffix(raw_name,data,content_type)
    original=safe_source_path(store,'sources/'+sid+'.original'+suffix,must_exist=False)
    original.write_bytes(data)
    provenance={'url':url,'title':title or None,'content_type':content_type,'fetched_at':now(),
                'raw_sha256':hashlib.sha256(data).hexdigest(),'original_kind':'http_response',
                'original_path':str(original.relative_to(store.root)),
                'media_type':detect_media_type(raw_name,data,content_type),'needs_visual':False,'pages':None}
    text='';error=None;extractor='source extraction'
    try:
        blocked=_html_block_reason(data,content_type,encoding)
        if blocked:raise ValueError(blocked)
        text,extractor,details=_source_content(store,raw_name,data,content_type=content_type,encoding=encoding)
        provenance.update(details)
        if not text.strip():raise ValueError('网页没有可读取正文')
    except (ValueError,LookupError,OSError,subprocess.SubprocessError) as exc:text='';error=str(exc)
    provenance.update({'extractor':extractor,'text_sha256':content_hash(text),'extraction_status':'failed' if error else 'ready'})
    if error:provenance['error']=error
    safe_source_path(store,'sources/'+sid+'.provenance.json',must_exist=False).write_text(dump(provenance),encoding='utf-8')
    return store.add_source(name,text,url=url,error=error,source_id=sid)


def fetch(store, url, *, allow_private=False):
    url=url.strip()
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    try:return _fetch(store,url,allow_private=allow_private)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return store.add_source(url.rsplit('/',1)[-1] or url,'',url=url,error=str(exc))


def retry_source(store, source_id):
    from .media import source_files
    old,_,original=source_files(store,source_id)
    if old['url']:return fetch(store,old['url'],allow_private=True)
    if original is None:raise ValueError('原始文件未保留，请重新上传；原失败记录仍保留')
    return upload(store,old['name'],original.read_bytes())


def existing_for_run(store,run_id,url):
    """Best-effort reuse inside this run only; no cross-run freshness assumptions."""
    from .research_budget import canonical_url
    url=canonical_url(url)
    for sid in reversed(store.source_ids(run_id)):
        source=store.one('sources',sid)
        if source['url'] and canonical_url(source['url'])==url and source['status']=='ready':
            try:store.source_text(sid)  # validate the retained snapshot still exists
            except (ValueError,OSError):continue
            return source
    return None


def fetch_for_run(store,run_id,url):
    import json
    run=store.one('runs',run_id)
    if not json.loads(run['requirements']).get('allow_web'):raise ValueError('本轮仅允许本地来源')
    from .research_budget import canonical_url
    canonical=canonical_url(url)
    previous=existing_for_run(store,run_id,canonical)
    from . import research_budget as budget
    if previous:return {**previous,'reused':True,'budget':budget.snapshot(store,run_id)}
    key=(str(store.root),run_id,canonical)
    with _INFLIGHT_FETCHES_LOCK:
        future=_INFLIGHT_FETCHES.get(key)
        leader=future is None
        if leader:
            future=Future()
            _INFLIGHT_FETCHES[key]=future
    if not leader:
        shared=future.result()
        return {**shared,'reused':True,'shared':True,'budget':budget.snapshot(store,run_id)}
    try:
        # A prior caller can finish after our first lookup and before this
        # caller obtains the in-flight slot.  Recheck as the slot owner.
        previous=existing_for_run(store,run_id,canonical)
        if previous:
            result={**previous,'reused':True,'shared':False,'budget':budget.snapshot(store,run_id)}
        else:
            try:reservation=budget.reserve_pages(store,run_id,[canonical])
            except budget.BudgetExhausted as exc:
                result={**exc.result,'url':url}
            else:
                source=fetch(store,url)
                store.attach_source(run_id,source['id'])
                if reservation.get('round_id'):
                    from .research_plan import settle_request
                    settle_request(store,run_id,reservation['request_id'],'completed' if source.get('status')=='ready' else 'failed')
                result={**source,'reused':False,'shared':False,'budget':budget.snapshot(store,run_id),
                        'round_id':reservation.get('round_id'),'local_request_id':reservation.get('request_id')}
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _INFLIGHT_FETCHES_LOCK:
            _INFLIGHT_FETCHES.pop(key,None)
