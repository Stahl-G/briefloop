import hashlib
import io
import json
import threading
import zipfile

from PIL import Image
import pytest

from briefloop.article_materials import image_path, ARTICLE_MIME
from briefloop.media import source_attachment
from briefloop.source_ingestion import receive_upload
from briefloop.source_extraction import extract_job
from briefloop.store import Store


def package(*,unsafe=False):
    image=io.BytesIO();Image.new('RGB',(40,25),'white').save(image,format='PNG')
    context={'project':'Link2Context','schema_version':'0.1','source':{'url':'https://example.org/article'},
             'article':{'title':'合成经营复盘','published_at':'2026-10-09'},
             'content':{'plain_text':'这是结构参考，图中的示例数字不是本期事实。'},
             'media':{'images':[{'index':3,'url':'https://example.org/3','local_path':'missing.png'},
                                {'index':2,'url':'https://example.org/2','local_path':'images/2.png'},
                                {'index':1,'url':'https://example.org/1','local_path':'../outside.png' if unsafe else 'images/1.png'}]}}
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('article/context.json',json.dumps(context,ensure_ascii=False))
        z.writestr('article/images/1.png',image.getvalue());z.writestr('article/images/2.png',image.getvalue())
    return data.getvalue(),context


def admit(store,name,data):
    source=receive_upload(store,name,len(data),lambda sink:sink.write(data))
    job=source['extraction_job_id'];store.update_job(job,'running')
    extract_job(store,store.one('jobs',job),threading.Event())
    return source['id']


def test_article_upload_preserves_order_gaps_duplicates_and_verified_pixels(tmp_path):
    store=Store(tmp_path);data,_=package();sid=admit(store,'文章.zip',data)
    attachment=source_attachment(store,sid)
    assert attachment['media_type']==ARTICLE_MIME and attachment['needs_visual']
    assert attachment['raw_sha256']==hashlib.sha256(data).hexdigest()
    rows=attachment['article']['images']
    assert [r['original_index'] for r in rows]==[1,2,3]
    assert [r['status'] for r in rows]==['available','available','missing']
    assert rows[1]['same_bytes_as']==1
    assert rows[0]['ocr_status']=='not_run'
    assert 'missing' in store.source_text(sid)
    assert image_path(store,sid,1).read_bytes().startswith(b'\x89PNG')
    with pytest.raises(ValueError,match='缺失'):image_path(store,sid,3)
    image_path(store,sid,1).write_bytes(b'changed')
    with pytest.raises(ValueError,match='哈希不匹配'):image_path(store,sid,1)


def test_json_without_images_stays_explicitly_incomplete_and_unsafe_archive_keeps_original(tmp_path):
    store=Store(tmp_path);_,context=package()
    sid=admit(store,'context.json',json.dumps(context).encode())
    assert all(r['status']=='missing' for r in source_attachment(store,sid)['article']['images'])
    assert not source_attachment(store,sid)['needs_visual']
    data,_=package(unsafe=True)
    with pytest.raises(ValueError,match='越界'):admit(store,'unsafe.zip',data)
    assert any(p.read_bytes()==data for p in (tmp_path/'sources').glob('*.original.zip'))
    assert not (tmp_path/'outside.png').exists()


def test_plain_article_export_uses_relative_image_links_without_a_manifest(tmp_path):
    store=Store(tmp_path)
    photo=io.BytesIO();Image.new('RGB',(30,20),'blue').save(photo,format='PNG')
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('report/article.md','# 合成正文\n\n当期情况。\n\n![表格](images/table%20one.png)\n\n![未缓存](https://example.org/missing.png)')
        z.writestr('report/images/table one.png',photo.getvalue())
    sid=admit(store,'普通文章.zip',data.getvalue())
    rows=source_attachment(store,sid)['article']['images']
    assert rows[0]['status']=='available' and rows[0]['member_path']=='report/images/table one.png'
    assert rows[1]['status']=='missing'
    assert source_attachment(store,sid)['article']['title']=='合成正文'
    assert store.source_text(sid).count('# 合成正文')==1


def test_standalone_documents_with_logos_remain_text(tmp_path):
    store=Store(tmp_path)
    for name,data in [('article.md',b'# Title\n\nBody\n![logo](https://example.org/logo.png)'),
                      ('article.html',b'<html><title>Title</title><body><h1>Title</h1><p>Body</p><img src="https://example.org/pixel"></body></html>'),
                      ('local.md',b'# Local\nBody\n![chart](neighbor.png)')]:
        sid=admit(store,name,data)
        attachment=source_attachment(store,sid)
        assert attachment.get('media_type')!=ARTICLE_MIME and not attachment.get('needs_visual')
        assert 'Body' in store.source_text(sid)
        assert store.source_text(sid).count('# Title')<=1
        assert '缺少原图' not in store.source_text(sid)
