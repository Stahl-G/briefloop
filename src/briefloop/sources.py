"""Small source readers. Preserve originals; extraction failures stay visible."""
from html.parser import HTMLParser
from html import unescape
from . import __version__
from .host_bins import find as find_host_bin
from io import BytesIO
from pathlib import Path
import hashlib
import re
import subprocess
import os
import tempfile
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET

TITLE_MAX_CHARS=200
# Interstitial/anti-bot/error titles are not source labels.
_GENERIC_TITLE_RE=re.compile(r'^(?:just a moment|attention required|access denied|access to this page has been denied|are you a robot|verify you are human|checking your browser|enable javascript|403 forbidden|404 not found|429 too many requests|too many requests|service unavailable|bad gateway)\b',re.I)


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


def extract(name, data, *, with_extractor=False):
    extractor="text decode utf-8-sig/gb18030"
    ext=Path(name).suffix.lower()
    if ext == '.pdf':
        from .media import pdf_metadata,PDF_NOTICE
        pdf_metadata(data)
        extractor='pdftotext -layout'
        pdftotext=find_host_bin('pdftotext')
        if pdftotext:
            with tempfile.TemporaryDirectory(prefix='briefloop-read-') as tmp:
                p=Path(tmp)/'source.pdf';p.write_bytes(data)
                proc=subprocess.run([pdftotext,'-layout',str(p),'-'],capture_output=True,timeout=90)
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
            with zipfile.ZipFile(BytesIO(data)) as z:
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


def _fetch_bytes(url):
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    curl=find_host_bin('curl')
    if curl:
        env=dict(os.environ)
        for key,value in urllib.request.getproxies().items():
            if key in ('http','https','all'):env.setdefault(key+'_proxy',value)
        with tempfile.TemporaryDirectory(prefix='briefloop-web-') as tmp:
            path=Path(tmp)/'response'
            command=[curl,'--fail','--silent','--show-error','--location','--proto','=http,https','--proto-redir','=http,https','--connect-timeout','12','--max-time','40','--max-filesize',str(15*1024*1024),'-A',f'BriefLoop/{__version__} (local research reader)','-o',str(path),'-w','%{content_type}',url]
            proc=subprocess.run(command,capture_output=True,text=True,env=env,timeout=45)
            if proc.returncode:raise ValueError(proc.stderr.strip() or '网页读取失败')
            data=path.read_bytes();content_type=proc.stdout;encoding='utf-8'
    else:
        req=urllib.request.Request(url,headers={'User-Agent':f'BriefLoop/{__version__} (local research reader)'})
        with urllib.request.urlopen(req,timeout=40) as response:
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


def _fetch(store, url):
    from .store import uid,now,content_hash,dump
    from .media import detect_media_type,safe_source_path
    from urllib.parse import urlsplit,unquote
    data,content_type,encoding=_fetch_bytes(url)
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
        text,extractor,details=_source_content(store,raw_name,data,content_type=content_type,encoding=encoding)
        provenance.update(details)
        if not text.strip():raise ValueError('网页没有可读取正文')
    except (ValueError,LookupError,OSError,subprocess.SubprocessError) as exc:text='';error=str(exc)
    provenance.update({'extractor':extractor,'text_sha256':content_hash(text),'extraction_status':'failed' if error else 'ready'})
    if error:provenance['error']=error
    safe_source_path(store,'sources/'+sid+'.provenance.json',must_exist=False).write_text(dump(provenance),encoding='utf-8')
    return store.add_source(name,text,url=url,error=error,source_id=sid)


def fetch(store, url):
    url=url.strip()
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    try:return _fetch(store,url)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return store.add_source(url.rsplit('/',1)[-1] or url,'',url=url,error=str(exc))


def retry_source(store, source_id):
    from .media import source_files
    old,_,original=source_files(store,source_id)
    if old['url']:return fetch(store,old['url'])
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
    previous=existing_for_run(store,run_id,url)
    from . import research_budget as budget
    if previous:return {**previous,'reused':True,'budget':budget.snapshot(store,run_id)}
    try:reservation=budget.reserve_pages(store,run_id,[url])
    except budget.BudgetExhausted as exc:return {**exc.result,'url':url}
    source=fetch(store,url)
    store.attach_source(run_id,source['id'])
    if reservation.get('round_id'):
        from .research_plan import settle_request
        settle_request(store,run_id,reservation['request_id'],'completed' if source.get('status')=='ready' else 'failed')
    return {**source,'reused':False,'budget':budget.snapshot(store,run_id),
            'round_id':reservation.get('round_id'),'local_request_id':reservation.get('request_id')}
