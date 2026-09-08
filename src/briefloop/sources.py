"""Small source readers. Preserve originals; extraction failures stay visible."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import hashlib
import subprocess
import shutil
import os
import tempfile
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET


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


def extract(name, data, *, with_extractor=False):
    extractor="text decode utf-8-sig/gb18030"
    ext=Path(name).suffix.lower()
    if ext == '.pdf':
        extractor='pdftotext -layout'
        if shutil.which('pdftotext'):
            with tempfile.TemporaryDirectory(prefix='briefloop-read-') as tmp:
                p=Path(tmp)/'source.pdf';p.write_bytes(data)
                proc=subprocess.run(['pdftotext','-layout',str(p),'-'],capture_output=True,timeout=90)
                text=proc.stdout.decode('utf-8',errors='replace') if proc.returncode==0 else ''
        else:text=''
        if not text.strip():
            try:
                from pypdf import PdfReader
                extractor='pypdf.PdfReader.extract_text'
                text='\n\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(data)).pages)
            except Exception as exc:raise ValueError('PDF 无法提取正文，可能是扫描件或加密文档') from exc
    elif ext == '.docx':
        extractor='DOCX word/document.xml paragraph text'
        with zipfile.ZipFile(BytesIO(data)) as z:
            doc=ET.fromstring(z.read('word/document.xml'))
            text='\n'.join(''.join(n.itertext()) for n in doc.iter() if n.tag.endswith('}p'))
    elif ext in ('.html','.htm'):
        extractor='briefloop.sources.TextHTML (utf-8)'
        text=html_text(data.decode('utf-8',errors='replace'))
    else:
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('gb18030')
    if not text.strip(): raise ValueError('未能读取正文')
    return (text,extractor) if with_extractor else text


def upload(store, name, data):
    from .store import uid
    sid=uid('src');name=Path(name).name
    original=store.root/'sources'/(sid+Path(name).suffix.lower())
    if original.suffix=='.txt':original=original.with_suffix('.original.txt')
    original.write_bytes(data)
    try:return store.add_source(name, extract(name,data), source_id=sid)
    except (ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        return store.add_source(name,'',error=str(exc),source_id=sid)


def _fetch_bytes(url):
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    if shutil.which('curl'):
        env=dict(os.environ)
        for key,value in urllib.request.getproxies().items():
            if key in ('http','https','all'):env.setdefault(key+'_proxy',value)
        with tempfile.TemporaryDirectory(prefix='briefloop-web-') as tmp:
            path=Path(tmp)/'response'
            command=['curl','--fail','--silent','--show-error','--location','--proto','=http,https','--proto-redir','=http,https','--connect-timeout','12','--max-time','40','--max-filesize',str(15*1024*1024),'-A','BriefLoop/0.1 (local research reader)','-o',str(path),'-w','%{content_type}',url]
            proc=subprocess.run(command,capture_output=True,text=True,env=env,timeout=45)
            if proc.returncode:raise ValueError(proc.stderr.strip() or '网页读取失败')
            data=path.read_bytes();content_type=proc.stdout;encoding='utf-8'
    else:
        req=urllib.request.Request(url,headers={'User-Agent':'BriefLoop/0.1 (local research reader)'})
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


def _fetch(store, url):
    from .store import uid,now,content_hash,dump
    data,content_type,encoding=_fetch_bytes(url)
    sid=uid('src');name=url.rsplit('/',1)[-1] or '网页'
    suffix='.pdf' if 'pdf' in content_type.lower() or data.startswith(b'%PDF-') else '.html' if 'html' in content_type.lower() else '.bin'
    original=store.root/'sources'/(sid+'.original'+suffix)
    original.write_bytes(data)
    provenance={'url':url,'content_type':content_type,'fetched_at':now(),
                'raw_sha256':hashlib.sha256(data).hexdigest(),
                'original_path':str(original.relative_to(store.root))}
    text='';error=None
    extractor='PDF extraction' if suffix=='.pdf' else 'briefloop.sources.TextHTML ('+encoding+')' if suffix=='.html' else 'text decode ('+encoding+')'
    try:
        if suffix=='.pdf':text,extractor=extract('source.pdf',data,with_extractor=True)
        else:
            decoded=data.decode(encoding,errors='replace')
            text=html_text(decoded) if suffix=='.html' else decoded
        if not text.strip():raise ValueError('网页没有可读取正文')
    except (ValueError,LookupError,OSError,subprocess.SubprocessError) as exc:
        text='';error=str(exc)
    provenance.update({'extractor':extractor,'text_sha256':content_hash(text),'extraction_status':'failed' if error else 'ready'})
    if error:provenance['error']=error
    (store.root/'sources'/(sid+'.provenance.json')).write_text(dump(provenance))
    return store.add_source(name,text,url=url,error=error,source_id=sid)


def fetch(store, url):
    url=url.strip()
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    try:return _fetch(store,url)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return store.add_source(url.rsplit('/',1)[-1] or url,'',url=url,error=str(exc))


def retry_source(store, source_id):
    old=store.one('sources',source_id)
    if old['url']:return fetch(store,old['url'])
    originals=[p for p in (store.root/'sources').glob(source_id+'.*') if p!=store.root/old['path']]
    if not originals:raise ValueError('原始文件未保留，请重新上传；原失败记录仍保留')
    return upload(store,old['name'],originals[0].read_bytes())
