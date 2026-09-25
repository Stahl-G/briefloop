"""Word exports and shipped templates keep property children in OOXML schema order."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from briefloop.exports import docx_bytes
from briefloop.ooxml_order import add_ordered, order_violations

ASSETS = Path(__file__).resolve().parent.parent / 'src' / 'briefloop' / 'template_assets'


def violations(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: order_violations(etree.fromstring(archive.read(name)))
                for name in archive.namelist() if name.startswith('word/') and name.endswith('.xml')}


def test_insert_follows_schema_order_and_replaces_duplicates():
    ppr = parse_xml('<w:pPr %s><w:spacing/><w:jc w:val="center"/></w:pPr>' % nsdecls('w'))
    add_ordered(ppr, 'shd', val='clear', fill='006838')
    add_ordered(ppr, 'pStyle', val='Title')
    add_ordered(ppr, 'shd', val='clear', fill='FFFFFF')
    assert [c.tag.split('}')[1] for c in ppr] == ['pStyle', 'shd', 'spacing', 'jc']
    borders = parse_xml('<w:tblBorders %s/>' % nsdecls('w'))
    for edge in ('top', 'bottom', 'left', 'right', 'insideH', 'insideV'): add_ordered(borders, edge, val='single')
    assert [c.tag.split('}')[1] for c in borders] == ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']
    tcpr = parse_xml('<w:tcPr %s><w:tcW w:w="0" w:type="auto"/></w:tcPr>' % nsdecls('w'))
    for tag in ('shd', 'tcMar', 'tcBorders'): add_ordered(tcpr, tag)
    assert [c.tag.split('}')[1] for c in tcpr] == ['tcW', 'tcBorders', 'shd', 'tcMar']


def test_templates_and_exports_have_no_order_violations():
    for path in sorted(ASSETS.glob('*.docx')):
        assert not any(violations(path.read_bytes()).values()), path.name
    text = lambda s: {'type': 'paragraph', 'content': [{'type': 'text', 'text': s}]}
    cell = lambda kind, s, **attrs: {'type': kind, 'attrs': attrs, 'content': [text(s)]}
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '一、摘要'}]},
        {'type': 'horizontalRule'},
        {'type': 'table', 'content': [
            {'type': 'tableRow', 'content': [cell('tableHeader', '指标'), cell('tableHeader', '本期')]},
            {'type': 'tableRow', 'content': [cell('tableCell', '装机', backgroundColor='#FFEEAA'), cell('tableCell', '12')]}]}]}
    markdown = '# 标题\n\n## 一、概览\n\n| a | b |\n|---|---|\n| 1 | 2 |\n'
    exports = [docx_bytes(document=document, title='T'),
               docx_bytes(document=document, report_profile='industry_periodic', title='T', industry='AI'),
               docx_bytes(markdown, report_profile='industry_periodic', title='T', industry='AI')]
    for data in exports:
        assert not any(violations(data).values()), violations(data)
    with ZipFile(BytesIO(exports[1])) as archive:
        settings = etree.fromstring(archive.read('word/settings.xml'))
    tags = [c.tag.split('}')[1] for c in settings]
    assert tags.index('updateFields') < tags.index('compat')
