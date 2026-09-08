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
