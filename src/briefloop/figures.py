"""Agent-authored figure snapshots; registration never generates a chart or runs code."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .media import MAX_IMAGE_PIXELS, MAX_IMAGE_SIDE
from .store import dump, now, uid

SCHEMA_VERSION = 'briefloop.figure.v1'
_FIGURE_ID = re.compile(r'fig_[0-9a-f]{16}')
_FIGURE_MARKER = re.compile(r'!\[(?:\\.|[^\]\\])*\]\(\s*briefloop-figure:([A-Za-z0-9_-]+)(?:\s+"[^"]*")?\s*\)')
_IMAGE_EXTENSIONS = {'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}


def _id(value):
    if not isinstance(value,str) or not _FIGURE_ID.fullmatch(value):
        raise ValueError('无效图表 ID')
    return value


def _workspace_file(store,value):
    if not isinstance(value,(str,Path)) or not str(value):raise ValueError('图表输入路径无效')
    path=Path(value).expanduser()
    path=path if path.is_absolute() else store.root/path
    resolved=path.resolve()
    if not resolved.is_relative_to(store.root) or not resolved.is_file():
        raise ValueError('图表、数据和脚本必须是当前工作区内的实际文件')
    return resolved


def _figure_directory(store,figure_id,*,must_exist=True):
    figure_id=_id(figure_id)
    base=store.root/'figures'
    directory=base/figure_id
    if base.is_symlink() or directory.is_symlink():raise ValueError('图表目录不能是符号链接')
    if not directory.resolve().is_relative_to(store.root):raise ValueError('图表目录不能越界')
    if must_exist and not directory.is_dir():raise ValueError('图表记录不存在')
    return directory


def _snapshot_file(store,figure_id,relative):
    if not isinstance(relative,str):raise ValueError('图表快照路径无效')
    directory=_figure_directory(store,figure_id)
    path=store.root/relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.parent!=directory or path.is_symlink() or not path.is_file():
        raise ValueError('图表快照路径必须位于所属 figures 目录')
    return path


def _source_bindings(store,run_id,source_ids):
    run=store.one('runs',run_id)
    references=set(json.loads(run['requirements']).get('reference_source_ids',[]))
    allowed=set(store.source_ids(run_id))-references
    if not isinstance(source_ids,(list,tuple)) or any(not isinstance(sid,str) for sid in source_ids):
        raise ValueError('图表来源 ID 列表无效')
    ordered=list(dict.fromkeys(source_ids))
    hashes={}
    for sid in ordered:
        if sid in references:raise ValueError('风格参考不能作为图表数据来源')
        if sid not in allowed:raise ValueError('图表来源未登记在本轮报告中')
        source=store.one('sources',sid)
        if source['status']!='ready':raise ValueError('图表只能引用可读取的本轮来源')
        store.source_text(sid)
        hashes[sid]=source['hash']
    return ordered,hashes


def _structure_caption(caption):
    return bool(re.search(r'无数值|不含数值|no numeric data|no numerical data',caption,re.I))


def _normalized_image(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                extension=_IMAGE_EXTENSIONS.get(probe.format)
                if extension is None:raise ValueError('图表只支持 PNG、JPEG 和 WebP')
                width,height=probe.size
                if width*height>MAX_IMAGE_PIXELS or max(width,height)>MAX_IMAGE_SIDE:
                    raise ValueError('图表图片尺寸超过上限')
                probe.verify()
            with Image.open(BytesIO(data)) as original:
                image=ImageOps.exif_transpose(original)
                image=image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                image.info.clear()
                output=BytesIO();image.save(output,format='PNG')
                return output.getvalue(),extension,image.width,image.height
    except (UnidentifiedImageError,OSError,SyntaxError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise ValueError('图表图片损坏、无法解码或尺寸过大') from exc


def _sha(data):return hashlib.sha256(data).hexdigest()


def _markdown(figure_id,title):
    alt=' '.join(title.split()).replace('\\','\\\\').replace('[','\\[').replace(']','\\]')
    return f'![{alt}](briefloop-figure:{figure_id})'


def register_figure(store,run_id,image_path,title,caption='',source_ids=None,data_path=None,script_path=None):
    """Freeze an existing image and optional data/script, without executing the script."""
    if not isinstance(title,str) or not title.strip():raise ValueError('图表标题不能为空')
    if not isinstance(caption,str):raise ValueError('图表说明必须为文本')
    title=title.strip();caption=caption.strip()
    sources,source_hashes=_source_bindings(store,run_id,[] if source_ids is None else source_ids)
    if not sources and not _structure_caption(caption):
        raise ValueError('没有数值来源的纯结构图，请在说明中明确“无数值”或“no numeric data”')
    original=_workspace_file(store,image_path).read_bytes()
    png,original_extension,width,height=_normalized_image(original)
    snapshots={}
    for key,value in [('data',data_path),('script',script_path)]:
        if value is not None:
            file=_workspace_file(store,value)
            extension=file.suffix if re.fullmatch(r'\.[A-Za-z0-9_-]{1,20}',file.suffix) else '.bin'
            snapshots[key]=(key+extension,file.read_bytes())
    figure_id=uid('fig');destination=_figure_directory(store,figure_id,must_exist=False)
    base=destination.parent;base.mkdir(exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.register-',dir=base))
    prefix=f'figures/{figure_id}/'
    manifest={'schema_version':SCHEMA_VERSION,'figure_id':figure_id,'run_id':run_id,'title':title,'caption':caption,
              'source_ids':sources,'source_hashes':source_hashes,'image_path':prefix+'image.png',
              'original_path':prefix+'original'+original_extension,'data_path':None,'script_path':None,
              'hashes':{'image':_sha(png),'original':_sha(original),'data':None,'script':None},
              'width':width,'height':height,'created_at':now(),'markdown':_markdown(figure_id,title)}
    try:
        (temporary/'image.png').write_bytes(png)
        (temporary/('original'+original_extension)).write_bytes(original)
        for key,(filename,data) in snapshots.items():
            (temporary/filename).write_bytes(data)
            manifest[key+'_path']=prefix+filename;manifest['hashes'][key]=_sha(data)
        content=dump(manifest).encode('utf-8')
        (temporary/'manifest.json').write_bytes(content)
        (temporary/'manifest.sha256').write_text(_sha(content),encoding='ascii')
        if destination.exists():raise ValueError('图表 ID 已存在，不能覆盖')
        os.rename(temporary,destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return read_figure(store,figure_id,run_id=run_id)


def read_figure(store,figure_id,run_id=None):
    """Read one immutable figure; an explicit run_id requires exact ownership."""
    directory=_figure_directory(store,figure_id)
    path=_snapshot_file(store,figure_id,f'figures/{figure_id}/manifest.json')
    digest_path=_snapshot_file(store,figure_id,f'figures/{figure_id}/manifest.sha256')
    if path.stat().st_size>128_000:raise ValueError('图表记录过大')
    payload=path.read_bytes()
    if _sha(payload)!=digest_path.read_text(encoding='ascii').strip():raise ValueError('图表登记记录哈希不匹配')
    manifest=json.loads(payload)
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=SCHEMA_VERSION or manifest.get('figure_id')!=figure_id:
        raise ValueError('图表登记记录无效')
    if run_id is not None and manifest.get('run_id')!=run_id:raise ValueError('图表不属于本轮报告')
    sources,source_hashes=_source_bindings(store,manifest.get('run_id'),manifest.get('source_ids'))
    if source_hashes!=manifest.get('source_hashes'):raise ValueError('图表来源快照已变化')
    if not sources and not _structure_caption(manifest.get('caption','')):raise ValueError('无来源结构图缺少无数值说明')
    hashes=manifest.get('hashes')
    if not isinstance(hashes,dict):raise ValueError('图表快照哈希缺失')
    for key in ('image','original','data','script'):
        relative=manifest.get(key+'_path')
        if relative is None:
            if key in ('image','original') or hashes.get(key) is not None:raise ValueError('图表快照缺失')
            continue
        file=_snapshot_file(store,figure_id,relative)
        expected=hashes.get(key)
        if not isinstance(expected,str) or _sha(file.read_bytes())!=expected:raise ValueError(f'图表 {key} 快照哈希不匹配')
    image_path=_snapshot_file(store,figure_id,manifest['image_path'])
    try:
        with Image.open(image_path) as image:
            if image.format!='PNG' or image.size!=(manifest.get('width'),manifest.get('height')):raise ValueError('图表显示图像格式或尺寸不匹配')
            image.verify()
    except (UnidentifiedImageError,OSError) as exc:raise ValueError('图表显示图像无法读取') from exc
    if manifest.get('markdown')!=_markdown(figure_id,manifest.get('title','')):raise ValueError('图表 Markdown 标记不匹配')
    return manifest


def figure_ids(markdown):
    """Return figure IDs used by Markdown image markers, once in appearance order."""
    if not isinstance(markdown,str):raise ValueError('图表引用正文必须为文本')
    return list(dict.fromkeys(_id(match.group(1)) for match in _FIGURE_MARKER.finditer(markdown)))
