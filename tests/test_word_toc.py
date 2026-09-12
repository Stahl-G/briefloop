"""Word exports carry heading bookmarks, and report profiles carry a live TOC."""
import io
import threading
from zipfile import ZipFile

from briefloop.store import Store
from briefloop.templates import import_builtin
from briefloop.export_jobs import enqueue_export, generate_word, output_path
from briefloop.exports import docx_bytes

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def test_industry_periodic_export_has_toc_and_heading_bookmarks(tmp_path):
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 1, 'blockId': 'block_a'}, 'content': [{'type': 'text', 'text': '一、行业动态'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '正文。'}]},
        {'type': 'heading', 'attrs': {'level': 2, 'blockId': 'block_b'}, 'content': [{'type': 'text', 'text': '1.1 政策'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '正文。'}]},
        {'type': 'heading', 'attrs': {'level': 2, 'blockId': 'block_c'}, 'content': [{'type': 'text', 'text': '1.2 价格'}]},
    ]}
    blob = docx_bytes(document=document, report_profile='industry_periodic', title='T', language='zh-CN')
    with ZipFile(io.BytesIO(blob)) as archive:
        document_xml = archive.read('word/document.xml').decode()
        settings_xml = archive.read('word/settings.xml').decode()
    assert ' TOC ' in document_xml and '目录' in document_xml
    assert 'w:updateFields' in settings_xml
    from lxml import etree
    tree = etree.fromstring(document_xml.encode())
    starts = tree.findall('.//' + W + 'bookmarkStart')
    ids = [node.get(W + 'id') for node in starts]
    assert len(set(ids)) == len(ids), 'each heading must have a unique Word bookmark identity'
    assert ids == [node.get(W + 'id') for node in tree.findall('.//' + W + 'bookmarkEnd')]
    toc = next(field for field in tree.findall('.//' + W + 'fldSimple') if ' TOC ' in field.get(W + 'instr', ''))
    assert [link.get(W + 'anchor') for link in toc.findall(W + 'hyperlink')] == ['block_a', 'block_b', 'block_c']
    assert '目录将在打开文档时自动生成' not in document_xml
    names = [line for line in document_xml.split('w:bookmarkStart ') if 'w:name=' in line]
    assert len(names) == 3
    assert 'w:name="block_a"' in document_xml and 'w:name="block_c"' in document_xml


def test_brief_profile_stays_without_toc(tmp_path):
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 1, 'blockId': 'block_a'}, 'content': [{'type': 'text', 'text': '要点'}]}]}
    blob = docx_bytes(document=document, report_profile='brief', title='T')
    with ZipFile(io.BytesIO(blob)) as archive:
        document_xml = archive.read('word/document.xml').decode()
        settings_xml = archive.read('word/settings.xml').decode()
    assert ' TOC ' not in document_xml and 'w:updateFields' not in settings_xml
    assert 'w:name="block_a"' in document_xml


def test_builtin_template_export_keeps_toc_and_bookmarks_content(tmp_path):
    store = Store(tmp_path)
    import_builtin(store)
    from briefloop.templates import template
    record = template(store, next(r['id'] for r in store.rows('SELECT id,name FROM templates') if r['name'] == '通用报告·品牌绿'))
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '一、摘要'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '结论。'}]},
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '1.1 细分'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '细节。'}]}]}
    source = store.add_source('S', 'evidence')
    run = store.create_run({'title': '周报', 'objective': 'x', 'template_id': record['id']}, [source['id']])
    brief = store.publish(run['id'], {'title': '周报', 'editor_document': document})
    job = enqueue_export(store, brief['id'])
    generate_word(store, job, threading.Event())
    with ZipFile(output_path(store, job)) as archive:
        document_xml = archive.read('word/document.xml').decode()
        settings_xml = archive.read('word/settings.xml').decode()
        styles_xml = archive.read('word/styles.xml').decode()
    assert document_xml.count(' TOC ') == 1 and '目录' in document_xml
    assert 'w:updateFields' in settings_xml
    assert 'w:name="block_' in document_xml
    heading2 = styles_xml.split('w:styleId="Heading2"')[1].split('</w:style>')[0]
    assert 'w:outlineLvl' in heading2, 'TOC \\o collects styled headings only when outline levels survive'
