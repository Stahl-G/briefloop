"""One cancellable local extraction process; PDF originals remain on disk."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from .store import content_hash, dump

MAX_TEXT_BYTES=32*1024*1024
EXTRACTION_TIMEOUT=600


def _progress(store, job, data):
    store.event(job['id'],'source_extraction_progress',{'source_id':json.loads(job['payload'])['source_id'],**data})


def record_extraction_warning(store, job, phase, error):
    """An ancillary failure cannot undo a committed source or kill its queue."""
    data={'source_id':json.loads(job['payload'])['source_id'],'phase':phase,
          'error_type':type(error).__name__,'error':str(error)}
    try:store.event(job['id'],'source_extraction_warning',data)
    except Exception:
        # A full/locked event store must still leave an operational diagnostic.
        import logging
        logging.getLogger(__name__).exception('Source extraction warning could not be persisted: %s',data)


def extract_job(store, job, cancelled):
    from .media import source_files
    from .platform_support import OwnedProcess
    sid=json.loads(job['payload'])['source_id']
    source,metadata,original=source_files(store,sid)
    if original is None:raise ValueError('原始文件未保留，请重新上传')
    metadata=dict(metadata or {})
    folder=store.root/'jobs'/job['id'];folder.mkdir(exist_ok=True)
    text_path=folder/'extracted.txt';result_path=folder/'extraction.json';progress_path=folder/'extraction-progress.jsonl'
    for path in (text_path,result_path,progress_path):path.unlink(missing_ok=True)
    _progress(store,job,{'phase':'validating','message':'原件已保存，正在检查文件'})
    command=[sys.executable,'-m','briefloop.source_extraction',str(store.root),str(original),source['name'],str(folder)]
    with (folder/'extraction.log').open('wb') as log:
        process=OwnedProcess(command,parent_death=True,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        started=time.monotonic();offset=0;committed=False
        try:
            while True:
                if cancelled.is_set() or store.one('jobs',job['id'])['status']!='running':
                    raise InterruptedError('文件读取已取消，原件已保留，可重新读取')
                if time.monotonic()-started>EXTRACTION_TIMEOUT:raise ValueError('文件读取超时，原件已保留，可重新读取')
                if any(path.exists() and path.stat().st_size>MAX_TEXT_BYTES for path in (text_path,text_path.with_suffix('.chunk.txt'))):
                    raise ValueError('提取正文超过 32 MiB 上限，原件已保留')
                if progress_path.exists():
                    with progress_path.open('r',encoding='utf-8') as events:
                        events.seek(offset)
                        while line:=events.readline():
                            if not line.endswith('\n'):break
                            offset=events.tell();_progress(store,job,json.loads(line))
                if process.poll() is not None:break
                cancelled.wait(.1)
            if process.returncode or not result_path.exists():
                reason='文件读取进程退出，可能超过可用内存或文件已损坏；原件已保留'
                if result_path.exists():reason=json.loads(result_path.read_text(encoding='utf-8')).get('error') or reason
                raise ValueError(reason)
            result=json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('error'):raise ValueError(result['error'])
            if text_path.stat().st_size>MAX_TEXT_BYTES:raise ValueError('提取正文超过 32 MiB 上限，原件已保留')
            text=text_path.read_text(encoding='utf-8')
            metadata.update(result,text_sha256=content_hash(text),extraction_status='ready')
            metadata.pop('error',None)
            if not store.finish_source_extraction(sid,job['id'],text,metadata):
                raise InterruptedError('文件读取已停止，原件已保留')
            committed=True
            try:
                _progress(store,job,{'phase':'ready','pages_completed':metadata.get('pages'),'pages_total':metadata.get('pages'),
                                    'message':'读取完成' if not metadata.get('needs_visual') else '文件已检查，需要视觉读取（未执行 OCR）'})
            except Exception as error:record_extraction_warning(store,job,'final_progress',error)
            return {'source_id':sid}
        finally:
            primary_error=sys.exc_info()[0] is not None
            try:process.close_tree(timeout=.25)
            except Exception as error:
                record_extraction_warning(store,job,'process_cleanup',error)
                if not committed and not primary_error:raise


def sync_terminal_source(store, job, status, error=None):
    if job['kind']!='source_extract' or status not in ('failed','cancelled','interrupted'):return
    sid=json.loads(job['payload'])['source_id']
    store.finish_source_extraction(sid,job['id'],'',None,status=status,error=error)


def _memory_limit():
    if os.name!='nt':
        import resource
        _,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
        limit=min(MAX_TEXT_BYTES,hard) if hard!=resource.RLIM_INFINITY else MAX_TEXT_BYTES
        resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))
    # RLIMIT_AS is enforceable on Linux. macOS/Windows extraction is still isolated
    # and cancellable, but the OS does not expose this same portable hard limit.
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
        return 1024**3
    return None


def _extract_pdf(path, output, emit):
    from pypdf import PdfReader
    from .host_bins import find as find_host_bin
    from .media import PDF_NOTICE
    reader=PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''):raise ValueError('PDF 已加密，无法读取；原件已保留')
    pages=len(reader.pages)
    if not 1<=pages<=10000:raise ValueError('PDF 页数无效或超过 10000 页上限；原件已保留')
    emit({'phase':'extracting','pages_completed':0,'pages_total':pages,'message':'正在提取 PDF 正文'})
    pdftotext=find_host_bin('pdftotext');extractor='pdftotext -layout' if pdftotext else 'pypdf.PdfReader.extract_text'
    size=0;has_text=False
    def write(handle,text):
        nonlocal size,has_text
        data=text.encode('utf-8');size+=len(data);has_text=has_text or bool(text.strip())
        if size>MAX_TEXT_BYTES:raise ValueError('提取正文超过 32 MiB 上限，原件已保留')
        handle.write(data)
    with output.open('wb') as handle:
        if pdftotext:
            chunk=output.with_suffix('.chunk.txt')
            for first in range(1,pages+1,16):
                last=min(first+15,pages)
                result=subprocess.run([pdftotext,'-layout','-f',str(first),'-l',str(last),str(path),str(chunk)],
                                      stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
                if result.returncode:raise ValueError('PDF 正文提取失败，原件已保留')
                if chunk.stat().st_size+size>MAX_TEXT_BYTES:raise ValueError('提取正文超过 32 MiB 上限，原件已保留')
                write(handle,chunk.read_text(encoding='utf-8',errors='replace'))
                emit({'phase':'extracting','pages_completed':last,'pages_total':pages,'message':'正在提取 PDF 正文'})
            chunk.unlink(missing_ok=True)
        else:
            for index,page in enumerate(reader.pages,1):
                write(handle,(page.extract_text() or '')+'\n\n')
                if index%16==0 or index==pages:
                    emit({'phase':'extracting','pages_completed':index,'pages_total':pages,'message':'正在提取 PDF 正文'})
    if not has_text:output.write_text(PDF_NOTICE,encoding='utf-8')
    return {'media_type':'application/pdf','pages':pages,'needs_visual':not has_text,'extractor':extractor,
            'max_text_bytes':MAX_TEXT_BYTES}


def _child(root, original, name, folder):
    from .sources import _source_content
    from .media import detect_media_type
    def emit(data):
        with (folder/'extraction-progress.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(dump(data)+'\n');stream.flush()
    result={}
    try:
        memory_limit=_memory_limit()
        with original.open('rb') as stream:head=stream.read(512)
        output=folder/'extracted.txt'
        if detect_media_type(name,head)=='application/pdf':result=_extract_pdf(original,output,emit)
        else:
            emit({'phase':'extracting','message':'正在读取文件正文'})
            if original.stat().st_size>18*1024*1024:raise ValueError('非 PDF 文件超过 18 MiB 上限')
            text,extractor,metadata=_source_content(SimpleNamespace(root=root),name,original.read_bytes())
            if len(text.encode('utf-8'))>MAX_TEXT_BYTES:raise ValueError('提取正文超过 32 MiB 上限')
            output.write_text(text,encoding='utf-8');result={**metadata,'extractor':extractor}
        result['process_memory_limit_bytes']=memory_limit
    except Exception as exc:result={'error':str(exc) or '读取文件失败，原件已保留'}
    (folder/'extraction.json').write_text(dump(result),encoding='utf-8')


if __name__=='__main__':
    _child(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]))
