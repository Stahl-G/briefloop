from io import BytesIO
from zipfile import ZipFile
import json
from docx import Document
from docx.shared import Pt, RGBColor
from PIL import Image
from briefloop.store import Store
from briefloop.exports import docx_bytes
from briefloop.word_import import import_revision
from briefloop.templates import import_template, prepare, export_template
from briefloop.figures import register_figure
from briefloop.figure_support import markdown_bundle


def paragraph(text):return {'type':'paragraph','content':[{'type':'text','text':text}]}
def heading(text):return {'type':'heading','attrs':{'level':2,'blockId':'summary'},'content':[{'type':'text','text':text}]}
def setup(tmp_path):
    store=Store(tmp_path);source=store.add_source('Evidence','Revenue data')
    run=store.create_run({'title':'Report','objective':'Read'},[source['id']])
    return store,source,run


def test_word_roundtrip_preserves_all_preheading_blocks(tmp_path):
    store,source,run=setup(tmp_path)
    table={'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[paragraph('Intro table 12')]}]}]}
    document={'type':'doc','content':[paragraph('Important introduction 12'),table,heading('Summary'),paragraph('Body')]}
    brief=store.publish(run['id'],{'title':'Report','editor_document':document})
    result=import_revision(store,brief['id'],'unchanged.docx',docx_bytes(document=document))
    assert result['status']=='imported'
    nodes=json.loads(result['version']['editor_document'])['content']
    assert [n['type'] for n in nodes]==['paragraph','table','heading','paragraph']
    assert 'Important introduction 12' in result['version']['markdown']
    assert 'Intro table 12' in result['version']['markdown']


def test_markdown_bundle_uses_each_node_caption_without_registered_stale_caption(tmp_path):
    store,source,run=setup(tmp_path);path=store.root/'image.png';Image.new('RGB',(20,20),'red').save(path)
    figure=register_figure(store,run['id'],path,'Revenue','USD millions',[source['id']])
    nodes=[{'type':'image','attrs':{'src':'briefloop-figure:'+figure['figure_id'],'caption':caption}} for caption in ['USD thousands','']]
    brief=store.publish(run['id'],{'title':'Report','editor_document':{'type':'doc','content':nodes}})
    with ZipFile(BytesIO(markdown_bundle(store,brief))) as archive:
        text=archive.read('report.md').decode()
        assert 'USD millions' not in text and text.count('USD thousands')==1
        assert text.count('来源：Evidence')==2


