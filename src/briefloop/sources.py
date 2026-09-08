"""Small source readers. Preserve originals; extraction failures stay visible."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import subprocess
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
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript') and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def html_text(data):
    p=TextHTML();p.feed(data)
    return '\n'.join(line.strip() for line in ''.join(p.parts).splitlines() if line.strip())


def extract(name, data):
    ext=Path(name).suffix.lower()
    if ext == '.pdf':
        with tempfile.TemporaryDirectory(prefix='briefloop-read-') as tmp:
            p=Path(tmp)/'source.pdf';p.write_bytes(data)
            proc=subprocess.run(['pdftotext','-layout',str(p),'-'],capture_output=True,timeout=90)
            if proc.returncode: raise ValueError('PDF 无法提取正文，可尝试联网寻找原始发布版本')
            text=proc.stdout.decode('utf-8',errors='replace')
    elif ext == '.docx':
        with zipfile.ZipFile(BytesIO(data)) as z:
            doc=ET.fromstring(z.read('word/document.xml'))
            text='\n'.join(''.join(n.itertext()) for n in doc.iter() if n.tag.endswith('}p'))
    elif ext in ('.html','.htm'):
        text=html_text(data.decode('utf-8',errors='replace'))
    else:
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('gb18030')
    if not text.strip(): raise ValueError('未能读取正文')
    return text


def upload(store, name, data):
    from .store import uid
    sid=uid('src');name=Path(name).name
    original=store.root/'sources'/(sid+Path(name).suffix.lower())
    if original.suffix=='.txt':original=original.with_suffix('.original.txt')
    original.write_bytes(data)
    try:return store.add_source(name, extract(name,data), source_id=sid)
    except (ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        return store.add_source(name,'',error=str(exc),source_id=sid)


def _fetch(store, url):
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    req=urllib.request.Request(url,headers={'User-Agent':'BriefLoop/0.1 (local research reader)'})
    with urllib.request.urlopen(req,timeout=40) as response:
        data=response.read(15*1024*1024+1)
        if len(data)>15*1024*1024:raise ValueError('网页过大，请下载后上传')
        content_type=response.headers.get('Content-Type','')
        name=url.rsplit('/',1)[-1] or '网页'
        if 'pdf' in content_type:text=extract('source.pdf',data)
        else:
            decoded=data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
            text=html_text(decoded) if 'html' in content_type else decoded
    return store.add_source(name,text,url=url)


def fetch(store, url):
    url=url.strip()
    if not url.startswith(('https://','http://')):raise ValueError('请输入 HTTP(S) 来源地址')
    try:return _fetch(store,url)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return store.add_source(url.rsplit('/',1)[-1] or url,'',url=url,error=str(exc))
