import hashlib
import io
import json
import threading
import time
from pathlib import Path

import pytest
from pypdf import PdfWriter

from briefloop import media
from briefloop.runtime import Worker
from briefloop.source_ingestion import receive_upload, recover_uploads, retry_upload
from briefloop.source_extraction import extract_job
from briefloop.store import Store


def pdf_bytes():
    writer=PdfWriter()
    for _ in range(3):writer.add_blank_page(width=200,height=100)
    stream=io.BytesIO();writer.write(stream);return stream.getvalue()


def upload(store,name,data):
    def receive(sink):
        for start in range(0,len(data),65536):sink.write(data[start:start+65536])
    return receive_upload(store,name,len(data),receive)


def test_raw_receipt_is_durable_before_real_pdf_extraction_and_progress(tmp_path,monkeypatch):
    store=Store(tmp_path);data=pdf_bytes();source=upload(store,'扫描.pdf',data)
    assert source['status']=='queued'
    assert store.source_text(source['id'])==''
    sid=source['id'];job_id=source['extraction_job_id']
    original=store.root/'sources'/f'{sid}.original.pdf'
    assert original.read_bytes()==data
    assert not list((store.root/'sources').glob('*.upload.*'))
    # A detail view must not parse pending documents or invent ready state.
    def forbidden(*args,**kwargs):raise AssertionError('pending source must not parse PDF')
    monkeypatch.setattr(media,'pdf_metadata',forbidden)
    assert media.source_attachment(store,sid)['status']=='queued'
    store.update_job(job_id,'running')
    result=extract_job(store,store.one('jobs',job_id),threading.Event())
    assert result=={'source_id':sid}
    assert store.one('jobs',job_id)['status']=='complete'
    assert store.one('sources',sid)['status']=='ready'
    assert store.source_text(sid)==media.PDF_NOTICE
    metadata=json.loads((store.root/'sources'/f'{sid}.provenance.json').read_text())
    assert metadata['raw_sha256']==hashlib.sha256(data).hexdigest()
    assert metadata['pages']==3 and metadata['needs_visual'] is True
    events=[json.loads(row['data']) for row in store.rows("SELECT * FROM events WHERE job_id=? AND kind='source_extraction_progress' ORDER BY seq",(job_id,))]
    assert any(event.get('pages_completed')==3 for event in events)
    assert events[0]['phase']=='validating' and events[-1]['phase']=='ready'


def test_disconnect_never_admits_partial_and_complete_receipt_recovers(tmp_path,monkeypatch):
    store=Store(tmp_path)
    def disconnect(sink):sink.write(b'partial');raise ConnectionError('disconnected')
    with pytest.raises(ConnectionError):receive_upload(store,'partial.pdf',100,disconnect)
    assert not store.rows('SELECT * FROM sources')
    assert not list((store.root/'sources').glob('*.upload.*'))
    assert store.rows("SELECT * FROM events WHERE kind='source_upload_interrupted'")
    accept=store.accept_source_upload
    def crash(*args):raise RuntimeError('database admission interrupted')
    monkeypatch.setattr(store,'accept_source_upload',crash)
    data=pdf_bytes()
    with pytest.raises(RuntimeError):upload(store,'recover.pdf',data)
    assert len(list((store.root/'sources').glob('*.original.pdf')))==1
    assert not store.rows('SELECT * FROM sources')
    monkeypatch.setattr(store,'accept_source_upload',accept)
    recover_uploads(store);recover_uploads(store)
    assert len(store.rows('SELECT * FROM sources'))==1
    assert len(store.rows("SELECT * FROM jobs WHERE kind='source_extract'"))==1
    assert not list((store.root/'sources').glob('*.upload.*'))


def test_failure_retry_and_cancel_preserve_originals_and_prevent_late_admission(tmp_path):
    store=Store(tmp_path);source=upload(store,'broken.pdf',b'%PDF-broken')
    job_id=source['extraction_job_id'];store.update_job(job_id,'running')
    with pytest.raises(ValueError):extract_job(store,store.one('jobs',job_id),threading.Event())
    assert store.finish_source_extraction(source['id'],job_id,'',None,status='failed',error='读取失败')
    retry=retry_upload(store,source['id'])
    assert retry['id']!=source['id'] and store.one('sources',source['id'])['status']=='failed'
    assert len(list((store.root/'sources').glob('*.original.pdf')))==2
    worker=Worker(store);job_id=retry['extraction_job_id']
    store.update_job(job_id,'running');worker.extraction_current=job_id
    worker.stop_job(job_id)
    assert store.one('sources',retry['id'])['status']=='cancelled'
    assert not store.finish_source_extraction(retry['id'],job_id,'late text',{},status='ready')
    assert store.source_text(retry['id'])==''
    with pytest.raises(ValueError,match='仍在停止'):worker.resume(job_id)
    worker.extraction_current=None;worker.resume(job_id)
    assert store.one('sources',retry['id'])['status']=='queued'
    assert len(store.rows("SELECT * FROM jobs WHERE json_extract(payload,'$.source_id')=?",(retry['id'],)))==1


def test_extraction_lane_cancels_real_child_and_does_not_claim_export_lane(tmp_path,monkeypatch):
    import sys
    from briefloop import platform_support
    store=Store(tmp_path);source=upload(store,'slow.pdf',pdf_bytes())
    real=platform_support.OwnedProcess;children=[]
    def slow(command,**kwargs):
        process=real([sys.executable,'-c','import time; time.sleep(60)'],**kwargs)
        children.append(process);return process
    monkeypatch.setattr(platform_support,'OwnedProcess',slow)
    worker=Worker(store);thread=threading.Thread(target=worker.extraction_loop,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not children and time.monotonic()<deadline:time.sleep(.02)
        assert children and worker.extraction_current==source['extraction_job_id']
        assert worker.file_current is None
        worker.stop_job(source['extraction_job_id'])
        deadline=time.monotonic()+5
        while worker.extraction_current and time.monotonic()<deadline:time.sleep(.02)
        assert worker.extraction_current is None and children[0].poll() is not None
        assert store.one('sources',source['id'])['status']=='cancelled'
        assert (store.root/'sources'/f"{source['id']}.original.pdf").exists()
    finally:worker.stopping.set();worker.wake();thread.join(5)


def test_service_restart_exposes_interrupted_source_and_explicit_resume_reads_original(tmp_path):
    store=Store(tmp_path);source=upload(store,'restart.pdf',pdf_bytes())
    job_id=source['extraction_job_id'];store.update_job(job_id,'running')
    interrupted_settlement=upload(store,'old-failure.pdf',pdf_bytes())
    store.update_job(interrupted_settlement['extraction_job_id'],'failed',error='读取中断于状态保存')
    class LocalRuntime:
        cancelled=threading.Event()
        def cancel(self):self.cancelled.set()
    worker=Worker(store,LocalRuntime());worker.opened_paused=True;worker.start()
    try:
        assert store.one('jobs',job_id)['status']=='interrupted'
        assert store.one('sources',source['id'])['status']=='interrupted'
        assert store.one('sources',interrupted_settlement['id'])['status']=='failed'
        metadata=json.loads((store.root/'sources'/f"{source['id']}.provenance.json").read_text())
        assert metadata['extraction_status']=='interrupted'
        worker.resume(job_id)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and store.one('jobs',job_id)['status'] in ('queued','running'):time.sleep(.05)
        assert store.one('sources',source['id'])['status']=='ready'
        assert store.source_text(source['id'])==media.PDF_NOTICE
    finally:worker.close()


def test_pdf_fallback_reads_path_without_original_byte_copy(tmp_path,monkeypatch):
    from briefloop import host_bins
    from briefloop.source_extraction import _extract_pdf
    original=tmp_path/'source.pdf';original.write_bytes(pdf_bytes());output=tmp_path/'text.txt';events=[]
    monkeypatch.setattr(host_bins,'find',lambda name:None)
    read_bytes=Path.read_bytes
    def no_original_bytes(path):
        if path==original:raise AssertionError('PDF fallback must use its on-disk path')
        return read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',no_original_bytes)
    result=_extract_pdf(original,output,events.append)
    assert result['extractor']=='pypdf.PdfReader.extract_text'
    assert result['pages']==3 and events[-1]['pages_completed']==3
    assert output.read_text()==media.PDF_NOTICE


@pytest.mark.parametrize('phase',['final_progress','process_cleanup','after_commit'])
def test_completed_extraction_survives_ancillary_failure_and_next_upload_runs(tmp_path,monkeypatch,phase):
    from briefloop import source_extraction,platform_support
    store=Store(tmp_path)
    first=upload(store,'first.pdf',pdf_bytes());second=upload(store,'second.pdf',pdf_bytes())
    worker=Worker(store)
    # Bound the real worker loop to the two admitted jobs, without idle waiting.
    monkeypatch.setattr(worker,'_queued',lambda *args:iter([[store.one('jobs',source['extraction_job_id'])] for source in (first,second)]))
    if phase=='final_progress':
        progress=source_extraction._progress
        def failing_progress(store,job,data):
            if data.get('phase')=='ready':raise OSError('synthetic final progress failure')
            return progress(store,job,data)
        monkeypatch.setattr(source_extraction,'_progress',failing_progress)
    elif phase=='process_cleanup':
        close=platform_support.OwnedProcess.close_tree
        def failing_cleanup(process,*args,**kwargs):
            close(process,*args,**kwargs)
            raise OSError('synthetic post-cleanup failure')
        monkeypatch.setattr(platform_support.OwnedProcess,'close_tree',failing_cleanup)
    else:
        extract=source_extraction.extract_job
        def failing_after_commit(*args,**kwargs):
            extract(*args,**kwargs)
            raise OSError('synthetic post-commit failure')
        monkeypatch.setattr(source_extraction,'extract_job',failing_after_commit)
    worker.extraction_loop()
    for source in (first,second):
        assert store.one('sources',source['id'])['status']=='ready'
        job=store.one('jobs',source['extraction_job_id'])
        assert job['status']=='complete' and not job['error']
        assert json.loads(job['result'])=={'source_id':source['id']}
        warnings=store.rows("SELECT data FROM events WHERE job_id=? AND kind='source_extraction_warning'",(job['id'],))
        assert json.loads(warnings[-1]['data'])['phase']==phase
        assert store.source_text(source['id'])==media.PDF_NOTICE


def test_source_status_exposes_scanned_pdf_metadata_without_reading_original_or_body(tmp_path,monkeypatch):
    import http.client
    from briefloop.server import make_server
    server=make_server(tmp_path,port=0,paused=True)
    source=upload(server.store,'scan.pdf',pdf_bytes());jid=source['extraction_job_id']
    server.store.update_job(jid,'running')
    extract_job(server.store,server.store.one('jobs',jid),threading.Event())
    def forbidden(*args,**kwargs):raise AssertionError('status must not read original or extracted text')
    monkeypatch.setattr(media,'_hash',forbidden)
    monkeypatch.setattr(server.store,'source_text',forbidden)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        connection.request('GET','/api/source-status?id='+source['id'])
        response=connection.getresponse();result=json.loads(response.read());connection.close()
        assert response.status==200
        assert result['source']['status']=='ready'
        assert result['source']['needs_visual'] is True
        assert result['source']['pages']==3
        assert result['source']['media_type']=='application/pdf'
        assert '未执行 OCR' in result['progress']['message']
    finally:server.shutdown();server.server_close()
