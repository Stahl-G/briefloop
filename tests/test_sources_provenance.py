import hashlib
import json
from briefloop import sources
from briefloop.store import Store


def test_web_snapshot_preserves_response_and_failed_extraction(tmp_path,monkeypatch):
    store=Store(tmp_path)
    raw=b'<html><table><tr><th>Company</th><th>USD</th><th>MW</th></tr><tr><td>A</td><td>120</td><td>45</td></tr></table></html>'
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url:(raw,'text/html; charset=utf-8','utf-8'))
    record=sources.fetch(store,'https://example.test/report')
    assert record['status']=='ready'
    text=store.source_text(record['id'])
    assert 'A\t120\t45' in text
    metadata=json.loads((store.root/'sources'/(record['id']+'.provenance.json')).read_text())
    assert (store.root/metadata['original_path']).read_bytes()==raw
    assert metadata['raw_sha256']==hashlib.sha256(raw).hexdigest()
    assert metadata['text_sha256']==record['hash']
    assert metadata['url']==record['url']
    assert metadata['extractor']=='briefloop.sources.TextHTML (utf-8)'
    blank=b'<html><script>not body text</script></html>'
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url:(blank,'text/html','utf-8'))
    failed=sources.fetch(store,'https://example.test/blank')
    assert failed['status']=='failed' and failed['error']
    failure_meta=json.loads((store.root/'sources'/(failed['id']+'.provenance.json')).read_text())
    assert failure_meta['extraction_status']=='failed'
    assert (store.root/failure_meta['original_path']).read_bytes()==blank
    assert store.source_text(failed['id'])==''

def test_corrupt_docx_becomes_a_failed_source(tmp_path):
    import io,zipfile
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('other.xml','x')
    store=Store(tmp_path)
    record=sources.upload(store,'broken.docx',buffer.getvalue())
    assert record['status']=='failed' and 'DOCX' in record['error']
    assert store.source_text(record['id'])==''


def test_run_url_reuse_and_bounded_source_reader(tmp_path,monkeypatch,capsys):
    from briefloop.cli import main
    from briefloop.scout_tools import read_source
    store=Store(tmp_path);calls=[]
    def response(url):
        calls.append(url)
        if url.endswith('/retry') and calls.count(url)==1:return b'<html></html>','text/html','utf-8'
        return b'first\nsecond long line\nthird\nfourth\n','text/plain','utf-8'
    monkeypatch.setattr(sources,'_fetch_bytes',response)
    req={'title':'test','objective':'research','allow_web':True}
    run=store.create_run(req,[])['id']
    first=sources.fetch_for_run(store,run,'https://example.test/report')
    metadata=(store.root/'sources'/(first['id']+'.provenance.json')).read_text()
    reused=sources.fetch_for_run(store,run,'https://example.test/report')
    assert reused['id']==first['id'] and reused['reused'] is True
    assert calls.count('https://example.test/report')==1
    assert (store.root/'sources'/(first['id']+'.provenance.json')).read_text()==metadata
    other=store.create_run(req,[])['id']
    assert sources.fetch_for_run(store,other,'https://example.test/report')['id']!=first['id']
    assert calls.count('https://example.test/report')==2
    failure=sources.fetch_for_run(store,run,'https://example.test/retry')
    repaired=sources.fetch_for_run(store,run,'https://example.test/retry')
    assert failure['status']=='failed' and repaired['status']=='ready'
    assert store.one('sources',failure['id'])['status']=='failed'
    assert read_source(store,first['id'])==store.source_text(first['id'])
    monkeypatch.setattr('sys.argv',['briefloop','tool','--workspace',str(store.root),'read-source','--id',first['id'],'--start-line','2','--end-line','3','--max-chars','6'])
    main();output=capsys.readouterr().out
    assert '共 4 行' in output and '第 2–2 行' in output
    assert '2: second' in output and '末行仅显示部分字符' in output
    assert 'third' not in output


def test_html_title_is_used_as_a_readable_source_label():
    from briefloop.sources import html_title
    assert html_title(b'<html><head><title>  Hello   World </title></head>', 'text/html; charset=utf-8') == 'Hello World'
    assert html_title(b'<html><head></head></html>', 'text/html') == ''
    assert html_title(b'<title>x</title>', 'application/pdf') == ''
    assert html_title(b'<title>Just a moment...</title>', 'text/html') == ''
    assert html_title(('<title>'+'长'*300+'</title>').encode(), 'text/html') == '长'*200


def test_snapshot_run_includes_attached_sources_in_count(tmp_path):
    from briefloop.store import Store
    store=Store(tmp_path/'ws')
    run=store.create_run({'title':'r','objective':'o','allow_web':True},[])
    a=store.add_source('a','text');b=store.add_source('b','text')
    store.attach_source(run['id'],a['id']);store.attach_source(run['id'],b['id'])
    row=next(r for r in store.snapshot()['runs'] if r['id']==run['id'])
    assert row['source_count']==2 and set(row['all_source_ids'])=={a['id'],b['id']}
