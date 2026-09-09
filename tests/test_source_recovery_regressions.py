from io import BytesIO
import json
from unittest.mock import patch
from zipfile import ZipFile
import pytest
from briefloop.store import Store, Conflict
from briefloop.sources import upload,fetch
from briefloop.learning import learn


def test_crlf_hash_preserves_original_text_and_detects_real_change(tmp_path):
    store=Store(tmp_path);data=b'date,price\r\n2026-09-09,12\r\n'
    source=upload(store,'windows.csv',data)
    assert store.source_text(source['id'])==data.decode()
    store.add_source('windows.csv',data.decode(),source_id=source['id'])
    (store.root/source['path']).write_bytes(data.replace(b'12',b'99'))
    with pytest.raises(Conflict):store.source_text(source['id'])


def test_remote_office_uses_same_reader_and_corrupt_archive_is_failed(tmp_path):
    store=Store(tmp_path);out=BytesIO()
    with ZipFile(out,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:p>Revenue 12 USD.</w:p></w:document>')
    data=out.getvalue();a=upload(store,'report.docx',data)
    with patch('briefloop.sources._fetch_bytes',return_value=(data,'application/vnd.openxmlformats-officedocument.wordprocessingml.document','utf-8')):
        b=fetch(store,'https://example.test/download')
    assert store.source_text(a['id'])==store.source_text(b['id'])=='Revenue 12 USD.'
    meta=json.loads((store.root/'sources'/f"{b['id']}.provenance.json").read_text())
    assert meta['original_path'].endswith('.docx')
    bad=upload(store,'bad.xlsx',data)
    assert bad['status']=='failed'


def test_old_study_cannot_replace_new_wiki(tmp_path):
    store=Store(tmp_path);job=store.enqueue('learn',{'feedback_ids':[],'skill_id':None,'k':1,'targets':['analyst']})
    root=store.root/'jobs'/job['id'];root.mkdir();(root/'study').mkdir()
    (root/'context.json').write_text('{"feedback":[],"cases":[]}')
    store.set_meta('last_study','new-study')
    wiki=store.root/'wiki/index.md';wiki.write_text('Newer patterns')
    with pytest.raises(ValueError,match='后续学习'):
        learn(store,None,job)
    assert store.meta('last_study')=='new-study' and wiki.read_text()=='Newer patterns'
