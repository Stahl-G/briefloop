import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from briefloop import media, sources
from briefloop.scout_tools import read_source
from briefloop.store import Store


def image_bytes(format='PNG', size=(8,4), orientation=None):
    image=Image.new('RGB',size,(90,140,180));out=io.BytesIO()
    kwargs={}
    if orientation:
        exif=Image.Exif();exif[274]=orientation;kwargs['exif']=exif
    image.save(out,format=format,**kwargs)
    return out.getvalue()


def blank_pdf(pages=2):
    writer=PdfWriter()
    for _ in range(pages):writer.add_blank_page(width=200,height=100)
    output=io.BytesIO();writer.write(output);return output.getvalue()


def test_image_upload_preserves_original_normalizes_orientation_and_rejects_bad_data(tmp_path,monkeypatch):
    store=Store(tmp_path);data=image_bytes('JPEG',orientation=6)
    source=sources.upload(store,'chart.jpeg',data)
    assert source['status']=='ready'
    metadata=json.loads((store.root/'sources'/f"{source['id']}.provenance.json").read_text())
    assert metadata['media_type']=='image/jpeg' and metadata['needs_visual'] is True
    assert metadata['raw_sha256']==hashlib.sha256(data).hexdigest()
    attachment=media.source_attachment(store,source['id'])
    assert attachment['status']=='ready' and attachment['media_type']=='image/jpeg'
    assert Path(attachment['original_path']).read_bytes()==data
    assert (attachment['width'],attachment['height'])==(4,8)
    with Image.open(attachment['image_path']) as image:
        assert image.format=='PNG' and image.size==(4,8)
        assert image.getexif().get(274) is None
    text=read_source(store,source['id'])
    assert attachment['image_path'] in text and 'OCR' in text
    broken=sources.upload(store,'broken.png',b'\x89PNG\r\n\x1a\nnot an image')
    assert broken['status']=='failed'
    assert media.source_attachment(store,broken['id'])['image_path'] is None
    monkeypatch.setattr(media,'MAX_IMAGE_PIXELS',4)
    assert sources.upload(store,'large.png',image_bytes())['status']=='failed'


def test_scan_pdf_is_visual_ready_and_renders_only_requested_pages(tmp_path,monkeypatch,capsys):
    from briefloop.cli import main
    store=Store(tmp_path);source=sources.upload(store,'scan.pdf',blank_pdf())
    assert source['status']=='ready' and store.source_text(source['id'])==media.PDF_NOTICE
    attachment=media.source_attachment(store,source['id'])
    assert attachment['pages']==2 and attachment['needs_visual'] is True
    assert attachment['image_path'] is None and attachment['rendered_pages']==[]
    assert media.rendered_page_path(store,source['id'],1) is None
    with pytest.raises(ValueError):media.render_source_pages(store,source['id'],[1,3])
    assert media.rendered_page_path(store,source['id'],1) is None
    monkeypatch.setattr('sys.argv',['briefloop','tool','--workspace',str(store.root),'render-source','--id',source['id'],'--pages','2'])
    main();result=json.loads(capsys.readouterr().out)
    assert result['source_id']==source['id'] and [p['page'] for p in result['pages']]==[2]
    path=Path(result['pages'][0]['path']);assert path.is_relative_to(store.root/'sources'/'media')
    with Image.open(path) as image:assert image.format=='PNG' and max(image.size)<=media.MAX_PAGE_SIDE
    assert media.rendered_page_path(store,source['id'],1) is None
    assert media.rendered_page_path(store,source['id'],2)==path
    assert [p['page'] for p in media.source_attachment(store,source['id'])['rendered_pages']]==[2]
    with pytest.raises(ValueError):media.render_source_pages(store,source['id'],[1,1])
    with pytest.raises(ValueError):media.render_source_pages(store,source['id'],[1]*5)
    corrupted=sources.upload(store,'invalid.pdf',b'%PDF-invalid')
    assert corrupted['status']=='failed'
    # Old text-extraction failure can still expose a valid visual attachment.
    legacy=store.add_source('old-scan.pdf','',error='未能读取正文')
    (store.root/'sources'/f"{legacy['id']}.pdf").write_bytes(blank_pdf(1))
    assert media.source_attachment(store,legacy['id'])['status']=='ready'
    assert media.source_attachment(store,legacy['id'])['needs_visual'] is True


def test_web_image_content_type_and_magic_do_not_decode_binary_as_text(tmp_path,monkeypatch):
    store=Store(tmp_path);data=image_bytes('WEBP')
    monkeypatch.setattr(sources,'_fetch_bytes',lambda url:(data,'image/webp','utf-8'))
    source=sources.fetch(store,'https://example.test/download?file=chart')
    attachment=media.source_attachment(store,source['id'])
    assert source['status']=='ready' and attachment['media_type']=='image/webp'
    assert attachment['needs_visual'] and attachment['original_path'].endswith('.webp')
    assert Path(attachment['original_path']).read_bytes()==data
    assert store.source_text(source['id'])==media.IMAGE_NOTICE
    assert sources._fetch_suffix('download',data,'application/octet-stream')=='.webp'


def test_source_metadata_and_cache_cannot_escape_or_silently_drift(tmp_path):
    store=Store(tmp_path/'workspace');source=sources.upload(store,'chart.png',image_bytes())
    sid=source['id'];record=store.root/'sources'/f'{sid}.provenance.json'
    original=json.loads(record.read_text());outside=tmp_path/'private.txt';outside.write_text('outside')
    record.write_text(json.dumps({**original,'original_path':str(outside)}))
    with pytest.raises(ValueError):media.source_attachment(store,sid)
    record.write_text(json.dumps({**original,'image_path':str(outside)}))
    with pytest.raises(ValueError):media.source_attachment(store,sid)
    record.write_text(json.dumps(original))
    Path(store.root/original['image_path']).write_bytes(image_bytes(size=(2,2)))
    with pytest.raises(ValueError):media.source_attachment(store,sid)
    record.unlink();record.symlink_to(outside)
    with pytest.raises(ValueError):media.source_attachment(store,sid)
