from copy import deepcopy
from docx import Document
from docx.oxml.ns import qn
from briefloop.document_export import render_document, reader_source_blocks


def fixture():
    def p(text):
        return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}
    def row(values, kind):
        return {'type': 'tableRow', 'content': [{'type': kind, 'content': [p(v)]} for v in values]}
    return {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'citation', 'attrs': {'sourceId': 'src_one'}}]},
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '来源'}]},
        {'type': 'table', 'content': [row(['编号', '主体/来源', 'source_id'], 'tableHeader'),
                                    row(['1', 'Source', 'src_one'], 'tableCell')]}]}


def test_reader_export_replaces_internal_index_with_linked_title_without_mutating_draft():
    draft = fixture(); original = deepcopy(draft); doc = Document()
    render_document(doc, draft, sources={'src_one': {'name': 'Official source', 'url': 'https://example.org/report'}})
    assert draft == original
    assert len(doc.tables) == 0
    assert sum(p.text == '来源' for p in doc.paragraphs) == 1
    assert 'src_one' not in doc.element.xml
    links = doc.element.xpath('//w:hyperlink')
    assert len(links) == 1
    assert links[0].xpath('.//w:t')[0].text == 'Official source'
    relation = doc.part.rels[links[0].get(qn('r:id'))]
    assert relation.is_external and relation.target_ref == 'https://example.org/report'
    assert links[0].xpath('.//w:color')[0].get(qn('w:val')) == '0563C1'


def test_unrecognized_or_unresolved_tables_are_not_discarded():
    draft = fixture()
    assert reader_source_blocks(draft, {})[0] == draft['content']
    draft['content'][-1]['content'][0]['content'][2]['content'][0]['content'][0]['text'] = '业务指标'
    assert reader_source_blocks(draft, {'src_one': {'name': 'Source'}})[0] == draft['content']


def test_export_without_source_append_keeps_authored_index():
    doc = Document()
    render_document(doc, fixture(), sources={'src_one': {'name': 'Source'}}, append_sources=False)
    assert len(doc.tables) == 1
