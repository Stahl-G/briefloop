"""Validated local image attachments and explicitly requested PDF page renders."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

IMAGE_NOTICE = '图片原件有效；需视觉读取，无 OCR 文本。请查看本来源的图像附件。'
PDF_NOTICE = 'PDF 原件有效，但未提取到正文；需视觉读取，无 OCR 文本。请按需渲染并查看指定页面。'
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 16_384
MAX_DISPLAY_SIDE = 4096
MAX_RENDER_PAGES = 4
MAX_PAGE_SIDE = 2200
_IMAGE_TYPES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
_RENDER_LOCK = threading.Lock()


def _source_id(sid):
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
        raise ValueError('无效来源 ID')
    return sid


def safe_source_path(store, value, *, must_exist=True):
    """No source/metadata/cache path may escape sources or traverse a symlink."""
    base = store.root / 'sources'
    if not isinstance(value,(str,Path)) or not str(value):raise ValueError('来源路径格式无效')
    path = Path(value)
    if '..' in path.parts:
        raise ValueError('来源路径不能越界')
    path = path if path.is_absolute() else store.root / path
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise ValueError('来源路径必须位于 sources 目录') from exc
    cursor = base
    if cursor.is_symlink():
        raise ValueError('来源目录不能是符号链接')
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('来源路径不能包含符号链接')
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('来源路径不能越界')
    if must_exist and not path.is_file():
        raise ValueError('来源文件不存在或不是普通文件')
    return path


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(store, sid):
    """Shared source-original lookup; all caller-supplied metadata is untrusted."""
    sid = _source_id(sid)
    source = store.one('sources', sid)
    safe_source_path(store, source['path'])
    record = safe_source_path(store, f'sources/{sid}.provenance.json', must_exist=False)
    provenance = None
    if record.exists():
        safe_source_path(store, record)
        if record.stat().st_size > 128_000:
            raise ValueError('来源元数据过大')
        provenance = json.loads(record.read_text(encoding='utf-8'))
        if not isinstance(provenance, dict):
            raise ValueError('来源元数据格式无效')
    original = None
    if provenance and provenance.get('original_path'):
        original = safe_source_path(store, provenance['original_path'])
        if original.parent != store.root / 'sources' or not original.name.startswith(sid + '.'):
            raise ValueError('原件路径与来源 ID 不匹配')
        digest = provenance.get('raw_sha256')
        if digest and (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or _hash(original) != digest):
            raise ValueError('来源原件哈希不匹配')
    elif not source.get('url'):
        extracted = safe_source_path(store, source['path'])
        for path in sorted((store.root / 'sources').glob(sid + '.*')):
            if path == extracted or path.suffix == '.json':
                continue
            original = safe_source_path(store, path)
            break
    return source, provenance, original


def detect_media_type(name, data=b'', content_type=''):
    if not isinstance(content_type,str):raise ValueError('来源类型元数据无效')
    mime = content_type.split(';', 1)[0].strip().lower()
    suffix = Path(name).suffix.lower()
    if data.startswith(b'%PDF-') or mime == 'application/pdf' or suffix == '.pdf':
        return 'application/pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':return 'image/webp'
    images = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp', '.gif':'image/gif','.tif':'image/tiff','.tiff':'image/tiff','.bmp':'image/bmp'}
    if mime.startswith('image/'):return mime
    if suffix in images:return images[suffix]
    return mime or ('text/html' if suffix in ('.html','.htm') else 'text/plain')


def pdf_metadata(data):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('PDF 已加密，无法读取')
        count = len(reader.pages)
        if count < 1 or count > 10_000:
            raise ValueError('PDF 页数无效或超过上限')
        return {'media_type':'application/pdf', 'pages':count}
    except Exception as exc:
        raise ValueError('PDF 原件无效、已损坏或无法解密') from exc


def _cache_directory(store, digest):
    directory = safe_source_path(store, f'sources/media/{digest}', must_exist=False)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def _png_bytes(image):
    output = BytesIO();image.save(output, format='PNG');return output.getvalue()


def prepare_image(store, data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as test:
                media_type = _IMAGE_TYPES.get(test.format)
                if media_type is None:raise ValueError('仅支持 PNG、JPEG 和 WebP 图片')
                width, height = test.size
                if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS or max(width,height) > MAX_IMAGE_SIDE:
                    raise ValueError('图片尺寸超过读取上限')
                test.verify()
            with Image.open(BytesIO(data)) as original:
                frame_count = getattr(original, 'n_frames', 1)
                corrected = ImageOps.exif_transpose(original)
                image = corrected.convert('RGBA' if 'A' in corrected.getbands() else 'RGB')
                image.thumbnail((MAX_DISPLAY_SIDE, MAX_DISPLAY_SIDE), Image.Resampling.LANCZOS)
                image.info.clear()
                payload = _png_bytes(image)
                digest = hashlib.sha256(data).hexdigest()
                path = _cache_directory(store, digest) / 'image.png'
                safe_source_path(store, path, must_exist=False)
                _atomic_bytes(path, payload)
                return {'media_type':media_type, 'needs_visual':True, 'pages':None,
                        'image_path':str(path.relative_to(store.root)), 'image_sha256':hashlib.sha256(payload).hexdigest(),
                        'width':image.width, 'height':image.height, 'original_width':width, 'original_height':height,
                        'frame_count':frame_count, 'image_note':'EXIF 已校正；动画仅读取首帧；未执行 OCR'}
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('图片无法解码、已损坏或尺寸过大') from exc


def _validated_cached_image(store, path, expected_hash=None):
    path = safe_source_path(store, path)
    if expected_hash and _hash(path) != expected_hash:raise ValueError('缓存图像哈希不匹配')
    try:
        with Image.open(path) as image:
            if image.format != 'PNG':raise ValueError('缓存图像不是 PNG')
            width,height=image.size
            if width*height > MAX_IMAGE_PIXELS:raise ValueError('缓存图像尺寸过大')
            image.verify()
        return path,width,height
    except (UnidentifiedImageError,OSError) as exc:
        raise ValueError('缓存图像无法读取') from exc


def source_attachment(store, sid):
    source, provenance, original = source_files(store, sid)
    metadata = dict(provenance or {})
    result = {'source_id':sid,'name':source['name'], 'text_path':str(safe_source_path(store,source['path'])),
              'original_path':str(original) if original else None, 'media_type':metadata.get('media_type') or 'text/plain',
              'image_path':None,'width':None,'height':None,'pages':None,'needs_visual':False,
              'status':source['status'],'error':source.get('error'),'rendered_pages':[]}
    if original is None:return result
    data=original.read_bytes()
    kind=detect_media_type(original.name,data,metadata.get('content_type') or metadata.get('media_type') or '')
    result['media_type']=kind
    result['raw_sha256']=hashlib.sha256(data).hexdigest()
    if kind.startswith('image/'):
        if source['status']=='failed' and metadata.get('media_type'):
            result['needs_visual']=True
            return result
        digest=hashlib.sha256(data).hexdigest()
        expected=safe_source_path(store,f'sources/media/{digest}/image.png',must_exist=False)
        if metadata.get('image_path') and safe_source_path(store,metadata['image_path'])!=expected:raise ValueError('图像缓存路径与原件不匹配')
        if expected.exists() and metadata.get('image_sha256'):
            image,width,height=_validated_cached_image(store,expected,metadata['image_sha256'])
        else:
            prepared=prepare_image(store,data)
            image,width,height=_validated_cached_image(store,prepared['image_path'],prepared['image_sha256'])
        result.update(status='ready',error=None,image_path=str(image),width=width,height=height,needs_visual=True)
    elif kind=='application/pdf':
        details=pdf_metadata(data)
        text=store.source_text(sid).strip()
        result.update(status='ready',error=None,pages=details['pages'],needs_visual=bool(metadata.get('needs_visual')) or not text or text==PDF_NOTICE)
        digest=hashlib.sha256(data).hexdigest()
        directory=safe_source_path(store,f'sources/media/{digest}',must_exist=False)
        if directory.exists():
            for path in sorted(directory.glob('page-*.png')):
                match=re.fullmatch(r'page-(\d+)\.png',path.name)
                if not match:continue
                page=int(match.group(1))
                if not 1<=page<=details['pages']:continue
                found=_rendered_page(store,digest,page)
                if found:result['rendered_pages'].append(found)
    return result


def _rendered_page(store, digest, page):
    path=safe_source_path(store,f'sources/media/{digest}/page-{page:04d}.png',must_exist=False)
    record=path.with_suffix('.json')
    if not path.exists() or not record.exists():return None
    safe_source_path(store,record)
    metadata=json.loads(record.read_text())
    if not isinstance(metadata,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(metadata.get('image_sha256',''))):raise ValueError('PDF 页面缓存元数据无效')
    if metadata.get('source_sha256')!=digest or metadata.get('page')!=page:raise ValueError('PDF 页面缓存绑定不匹配')
    image,width,height=_validated_cached_image(store,path,metadata.get('image_sha256'))
    return {'page':page,'path':str(image),'width':width,'height':height}


def rendered_page_path(store, sid, page):
    if type(page) is not int or page<1:raise ValueError('页码必须为从 1 开始的整数')
    _,_,original=source_files(store,sid)
    if original is None or detect_media_type(original.name,original.read_bytes())!='application/pdf':raise ValueError('该来源不是 PDF')
    details=pdf_metadata(original.read_bytes())
    if page>details['pages']:raise ValueError('页码超出 PDF 范围')
    result=_rendered_page(store,_hash(original),page)
    return Path(result['path']) if result else None


def render_source_pages(store, sid, pages):
    if not isinstance(pages,(list,tuple)) or not 1<=len(pages)<=MAX_RENDER_PAGES or any(type(p) is not int or p<1 for p in pages):
        raise ValueError(f'请指定 1 至 {MAX_RENDER_PAGES} 个从 1 开始的 PDF 页码')
    if len(set(pages))!=len(pages):raise ValueError('页码不能重复')
    _,_,original=source_files(store,sid)
    if original is None or detect_media_type(original.name,original.read_bytes())!='application/pdf':raise ValueError('该来源不是 PDF')
    data=original.read_bytes();details=pdf_metadata(data)
    if any(p>details['pages'] for p in pages):raise ValueError('页码超出 PDF 范围')
    digest=hashlib.sha256(data).hexdigest();results=[]
    try:import pypdfium2 as pdfium
    except ImportError as exc:raise ValueError('缺少 PDF 页面渲染依赖 pypdfium2，请更新 BriefLoop 安装') from exc
    with _RENDER_LOCK:
        document=pdfium.PdfDocument(data)
        try:
            for number in pages:
                cached=_rendered_page(store,digest,number)
                if cached:results.append(cached);continue
                page=document[number-1]
                try:
                    width,height=page.get_size()
                    if not all(math.isfinite(v) and v>0 for v in (width,height)):raise ValueError('PDF 页面尺寸无效')
                    scale=min(2.0,MAX_PAGE_SIDE/max(width,height))
                    bitmap=page.render(scale=scale)
                    try:image=bitmap.to_pil().convert('RGB');payload=_png_bytes(image)
                    finally:bitmap.close()
                finally:page.close()
                path=_cache_directory(store,digest)/f'page-{number:04d}.png'
                safe_source_path(store,path,must_exist=False);_atomic_bytes(path,payload)
                metadata={'source_sha256':digest,'page':number,'width':image.width,'height':image.height,'image_sha256':hashlib.sha256(payload).hexdigest()}
                record=path.with_suffix('.json');safe_source_path(store,record,must_exist=False)
                _atomic_bytes(record,json.dumps(metadata,sort_keys=True).encode())
                results.append({'page':number,'path':str(path),'width':image.width,'height':image.height})
        finally:document.close()
    return {'source_id':sid,'pages':results}
