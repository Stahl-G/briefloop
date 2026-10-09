"""Local article bundles: preserve text, image occurrences and explicit gaps.

No network fetches, OCR or claims of model inspection happen during admission.
The immutable uploaded JSON/ZIP is the original; previews are derived caches.
"""
import hashlib
import json
from pathlib import PurePosixPath
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import re
import stat

from . import media
from .platform_support import filesystem_path

ARTICLE_MIME = 'application/vnd.briefloop.article+json'
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('图文包路径必须为包内相对路径')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('图文包路径不能越界')
    return str(path)


def _context(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or value.get('project') != 'Link2Context':
        return None
    if value.get('schema_version') != '0.1':
        raise ValueError('尚不支持这个图文材料版本；原件已保留')
    quality=value.get('quality')
    if isinstance(quality,dict) and quality.get('status') in ('error','failed','blocked'):
        raise ValueError('上游记录文章获取未成功；原件已保留')
    for field in ('source', 'article', 'content', 'media'):
        if not isinstance(value.get(field), dict):
            raise ValueError('图文材料缺少 ' + field)
    return value



def _document_context(name, data):
    """Ordinary article exports need no hand-authored manifest."""
    suffix=PurePosixPath(name).suffix.lower()
    if suffix not in ('.md','.markdown','.html','.htm'):return None
    try:raw=data.decode('utf-8-sig')
    except UnicodeDecodeError:raw=data.decode('gb18030')
    title=PurePosixPath(name).stem
    images=[];source_url=None
    def add(url,alt=None):
        if not isinstance(url,str) or not url:return
        parsed=urlsplit(url)
        local=unquote(parsed.path) if not parsed.scheme and not parsed.netloc and parsed.path else None
        images.append({'index':len(images)+1,'url':url,'alt':alt,**({'local_path':local} if local else {})})
    if suffix in ('.html','.htm'):
        from .sources import html_text, html_title, _html_block_reason
        reason=_html_block_reason(raw.encode('utf-8'),'text/html')
        if reason:raise ValueError(reason)
        class ArticleHTML(HTMLParser):
            def handle_starttag(self,tag,attrs):
                nonlocal source_url
                attrs=dict(attrs)
                if tag=='img':add(attrs.get('data-src') or attrs.get('src'),attrs.get('alt'))
                if tag=='link' and attrs.get('rel')=='canonical':source_url=attrs.get('href')
        ArticleHTML().feed(raw)
        text=html_text(raw);title=html_title(raw.encode('utf-8'),'text/html') or title
    else:
        from markdown_it import MarkdownIt
        tokens=MarkdownIt('commonmark').parse(raw)
        for index,token in enumerate(tokens):
            if token.type=='heading_open' and token.tag=='h1' and index+1<len(tokens):
                title=tokens[index+1].content;break
        for token in tokens:
            for child in token.children or []:
                if child.type=='image':add(child.attrGet('src'),child.content)
        text=raw
    return {'source':{'url':source_url},'article':{'title':title},
            'content':{'plain_text':text},'media':{'images':images}}

def extract_article(store, name, data):
    """Return None for unrelated files; recognized invalid bundles fail visibly."""
    archive = None
    mapping = {}
    names = []
    base = PurePosixPath('.')
    if name.lower().endswith('.zip'):
        archive = media.office_archive(data)  # Bounded actual inflation, not ZIP sizes.
        try:
            for item in archive.infolist():
                _relative(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('图文包不能包含符号链接')
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
            if len(names) != len(set(names)):
                raise ValueError('图文包有重名文件，请重新导出')
            contexts = [p for p in names if PurePosixPath(p).name == 'context.json']
            documents=contexts or [p for p in names if PurePosixPath(p).suffix.lower() in ('.md','.markdown','.html','.htm')]
            if len(documents) != 1:
                raise ValueError('图文包应包含一篇文章正文（context.json、Markdown 或 HTML）及图片；请按文章分别上传')
            document=documents[0]
            if archive.getinfo(document).file_size > MAX_MANIFEST_BYTES:
                raise ValueError('图文材料清单过大')
            context = _context(archive.read(document)) if contexts else _document_context(document,archive.read(document))
            if context is None:
                raise ValueError('未识别图文资料包格式，原件已保留')
            base = PurePosixPath(document).parent
            maps = [p for p in names if PurePosixPath(p).name == 'media-map.json']
            if len(maps) > 1:
                raise ValueError('图文包有多个图片对应清单')
            if maps:
                if archive.getinfo(maps[0]).file_size > MAX_MANIFEST_BYTES:
                    raise ValueError('图片对应清单过大')
                mapping = json.loads(archive.read(maps[0]))
                if not isinstance(mapping, dict):
                    raise ValueError('图片对应清单格式无效')
            return _extract(store, data, context, archive, names, base, mapping)
        finally:
            archive.close()
    if not name.lower().endswith('.json'):
        context=_document_context(name,data)
        # Preserve legacy plain document extraction when there are no pictures.
        if not context or not context['media']['images']:return None
    else:context = _context(data)
    return _extract(store, data, context, None, [], base, {}) if context else None


def _extract(store, data, context, archive, names, base, mapping):
    article, content = context['article'], context['content']
    title = article.get('title') or '图文材料'
    if not isinstance(title, str):
        raise ValueError('文章标题格式无效')
    text = content.get('plain_text') or content.get('markdown') or ''
    if not isinstance(text, str):
        raise ValueError('文章正文格式无效')
    originals = context['media'].get('images', [])
    if not isinstance(originals, list) or len(originals) > 1000:
        raise ValueError('图片清单格式无效或超过 1000 项')
    if any(not isinstance(row,dict) or type(row.get('index')) is not int or row['index']<1 for row in originals):
        raise ValueError('正文图片必须具有从 1 开始的原始图号')
    originals = sorted(originals,key=lambda row:row['index'])
    if context['media'].get('cover_image'):
        originals.insert(0, {'index': 0, 'url': context['media']['cover_image'], 'role': 'cover'})
    mapped = mapping.get(title, [])
    if not isinstance(mapped, list) or any(not isinstance(p, str) for p in mapped):
        raise ValueError('图片对应清单格式无效')
    # Existing Link2Context exports name cache files doc<ID>-image<index>-<hash>.
    # Use only the provided article map, never infer ownership from a folder.
    map_by_index = {}
    for path in mapped:
        _relative(path)
        match = re.fullmatch(r'doc\d+-image(\d+)-[^/]+', PurePosixPath(path).name)
        if match:
            index = int(match[1])
            if index in map_by_index:
                raise ValueError('图片对应清单中的图号重复')
            map_by_index[index] = path
    if not text.strip() and not originals:
        raise ValueError('图文材料没有正文或图片清单；原件已保留')
    rows, hashes, indices = [], {}, set()
    for number, entry in enumerate(originals, 1):
        if not isinstance(entry, dict) or type(entry.get('index')) is not int or entry['index'] < 0 or entry['index'] in indices:
            raise ValueError('图片必须具有不重复的原始图号')
        indices.add(entry['index'])
        row = {'number': number, 'original_index': entry['index'], 'role': entry.get('role', 'body'),
               'url': entry.get('url'), 'alt': entry.get('alt'), 'status': 'missing',
               'note': '未找到可核对的对应原图；未联网下载', 'ocr_status': 'not_run'}
        path = entry.get('local_path') or map_by_index.get(entry['index'])
        if path:
            path = _relative(path)
            candidates = [str(base / path)] if entry.get('local_path') else [n for n in names if PurePosixPath(n).name == PurePosixPath(path).name]
            present = [p for p in candidates if p in names]
            if len(present) > 1:
                row['note'] = '存在多个同名原图，无法确定对应关系'
            elif archive and present:
                raw = archive.read(present[0])
                row.update(member_path=present[0], raw_sha256=hashlib.sha256(raw).hexdigest())
                try:
                    prepared = media.prepare_image(store, raw)
                    row.update({k: prepared[k] for k in ('image_path', 'image_sha256', 'width', 'height', 'frame_count')})
                    row.update(status='available', note=prepared['image_note'])
                except ValueError as exc:
                    row.update(status='unreadable', note=str(exc) + '；原图仍在上传原件内')
                if row['raw_sha256'] in hashes:
                    row['same_bytes_as'] = hashes[row['raw_sha256']]
                else:
                    hashes[row['raw_sha256']] = number
        rows.append(row)
    digest = hashlib.sha256(data).hexdigest()
    manifest = {'title': title, 'url': context['source'].get('url'), 'author': article.get('author'),
                'publisher': article.get('account_name'), 'published_at': article.get('published_at'),
                'fetched_at': context['source'].get('fetched_at'), 'source_sha256': digest,
                'images': rows, 'upstream_quality': context.get('quality'),
                'note': '图片可用仅表示本地校验成功；未执行 OCR，未记录模型是否已查看。发表时间不等于材料所述期间。'}
    payload = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError('图文材料清单过大')
    path = media._cache_directory(store, digest) / 'article.json'
    media._atomic_bytes(path, payload)
    text = '# ' + title + '\n\n' + text
    text += '\n\n[图片清单；状态不是内容核实结果]\n'
    for row in rows:
        text += f"图像 {row['number']}（原图号 {row['original_index']}，{row['role']}）：{row['status']}；{row['note']}"
        if row.get('same_bytes_as'):
            text += f"；与图像 {row['same_bytes_as']} 字节相同，不能据此认定是不同业务图"
        text += '\n'
    return text, 'local article admission (no OCR/network)', {
        'media_type': ARTICLE_MIME, 'needs_visual': bool(rows), 'pages': None,
        'article_manifest_path': str(path.relative_to(store.root)),
        'article_manifest_sha256': hashlib.sha256(payload).hexdigest(),
        'image_count': len(rows), 'image_available': sum(r['status'] == 'available' for r in rows),
    }


def attachment(store, metadata, digest):
    path = media.safe_source_path(store, metadata.get('article_manifest_path'))
    if filesystem_path(path).stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('图文材料清单过大')
    payload = filesystem_path(path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != metadata.get('article_manifest_sha256'):
        raise ValueError('图文材料清单已变化')
    manifest = json.loads(payload)
    if manifest.get('source_sha256') != digest:
        raise ValueError('图文材料与原件绑定不匹配')
    for row in manifest['images']:
        if row.get('image_path'):
            row['image_path']=str(media.safe_source_path(store,row['image_path']))
    return manifest


def image_path(store, sid, number):
    if type(number) is not int or number < 1:
        raise ValueError('图号必须为正整数')
    source, metadata, original = media.source_files(store, sid)
    if source['status'] != 'ready' or not original or (metadata or {}).get('media_type') != ARTICLE_MIME:
        raise ValueError('图文材料尚不可读取')
    manifest = attachment(store, metadata, metadata['raw_sha256'])
    row = next((r for r in manifest['images'] if r['number'] == number), None)
    if not row or row['status'] != 'available':
        raise ValueError('该原图缺失或无法读取；请补充原图')
    return media._validated_cached_image(store, row['image_path'], row['image_sha256'])[0]
