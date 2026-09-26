"""Durable raw-file admission; HTTP upload never performs document extraction."""
import hashlib
import json
import os
from pathlib import Path

from .media import detect_media_type, safe_source_path, _atomic_bytes
from .store import uid, now, dump


def _record(path, metadata):
    _atomic_bytes(path, dump(metadata).encode('utf-8'))


class _UploadSink:
    def __init__(self, stream):
        self.stream=stream;self.digest=hashlib.sha256();self.size=0;self.head=b''

    def write(self, chunk):
        self.stream.write(chunk);self.digest.update(chunk);self.size+=len(chunk)
        if len(self.head)<512:self.head=(self.head+chunk)[:512]


def receive_upload(store, name, expected_bytes, receive, *, retry_of_source_id=None):
    """Receive into a same-volume temporary file, then admit one durable job.

    A complete receipt remains recoverable if DB admission or the response fails.
    Incomplete request bodies are never source originals and are removed explicitly.
    """
    sid=uid('src');name=Path(name).name
    part=safe_source_path(store,f'sources/{sid}.upload.part',must_exist=False)
    manifest=safe_source_path(store,f'sources/{sid}.upload.json',must_exist=False)
    original=safe_source_path(store,f'sources/{sid}.original{Path(name).suffix.lower()}',must_exist=False)
    metadata={'source_id':sid,'name':name,'original_path':str(original.relative_to(store.root)),
              'original_kind':'uploaded_file','uploaded_at':now(),'expected_bytes':expected_bytes,
              'extraction_status':'receiving','needs_visual':False,'pages':None}
    if retry_of_source_id:metadata['retry_of_source_id']=retry_of_source_id
    _record(manifest,metadata)
    complete=False
    try:
        with part.open('xb') as handle:
            sink=_UploadSink(handle);receive(sink)
            if sink.size!=expected_bytes:raise ValueError('请求未接收完整，请重新上传')
            handle.flush();os.fsync(handle.fileno())
        metadata.update(raw_sha256=sink.digest.hexdigest(),uploaded_bytes=sink.size,
                        media_type=detect_media_type(name,sink.head),extraction_status='queued')
        _record(manifest,metadata);complete=True
        os.replace(part,original)
        _record(original.parent/f'{sid}.provenance.json',metadata)
        source=store.accept_source_upload(sid,name,metadata)
        manifest.unlink(missing_ok=True)
        return source
    except BaseException:
        if not complete:
            part.unlink(missing_ok=True);manifest.unlink(missing_ok=True)
            store.event(None,'source_upload_interrupted',{'name':name,'expected_bytes':expected_bytes,
                        'received_bytes':part.stat().st_size if part.exists() else getattr(locals().get('sink'),'size',0),
                        'message':'上传未完成，请重新上传；未登记为可用来源'})
        raise


def recover_uploads(store):
    """Recover only completed receipts; record incomplete bodies before cleanup."""
    for manifest in sorted((store.root/'sources').glob('src_*.upload.json')):
        try:
            metadata=json.loads(manifest.read_text(encoding='utf-8'));sid=metadata['source_id']
            if manifest.name!=sid+'.upload.json':raise ValueError('上传记录与来源 ID 不匹配')
            part=safe_source_path(store,f'sources/{sid}.upload.part',must_exist=False)
            if metadata.get('extraction_status')=='receiving':
                store.event(None,'source_upload_interrupted',{'name':metadata.get('name'),'source_id':sid,
                            'message':'服务中断时上传尚未完成，请重新上传'})
                part.unlink(missing_ok=True);manifest.unlink();continue
            original=safe_source_path(store,metadata['original_path'],must_exist=False)
            if original.parent!=store.root/'sources' or not original.name.startswith(sid+'.original'):
                raise ValueError('原件路径与来源 ID 不匹配')
            saved=original if original.exists() else part
            with saved.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
            if digest!=metadata['raw_sha256'] or saved.stat().st_size!=metadata['expected_bytes']:
                raise ValueError('完整上传记录与原件不一致，文件已保留')
            if saved==part:os.replace(part,original)
            if not store.rows('SELECT id FROM sources WHERE id=?',(sid,)):
                _record(original.parent/f'{sid}.provenance.json',metadata)
                store.accept_source_upload(sid,metadata['name'],metadata)
                store.event(None,'source_upload_recovered',{'source_id':sid,'name':metadata['name']})
            manifest.unlink()
        except (OSError,ValueError,KeyError) as exc:
            store.event(None,'source_upload_recovery_failed',{'receipt':manifest.name,'error':str(exc)})


def retry_upload(store, source_id):
    from .media import source_files
    old,_,original=source_files(store,source_id)
    if old['status'] not in ('failed','cancelled','interrupted'):
        raise ValueError('只有读取失败、中断或已取消的来源可以重试')
    if original is None:raise ValueError('原始文件未保留，请重新上传；原失败记录仍保留')
    def copy(sink):
        with original.open('rb') as handle:
            while chunk:=handle.read(65536):sink.write(chunk)
    return receive_upload(store,old['name'],original.stat().st_size,copy,retry_of_source_id=source_id)
