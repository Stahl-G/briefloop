"""#858: a previous report's tracked changes become the first edits to classify."""
import json
from io import BytesIO

from docx import Document
from docx.oxml import parse_xml

from briefloop import revision_edits
from briefloop.learning import _eligible_cases
from briefloop.previous_report import docx_versions, import_previous
from briefloop.store import Store

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def tracked_docx():
    doc = Document()
    doc.add_heading('组件价格周报', level=1)
    doc.add_heading('价格', level=2)
    p = doc.add_paragraph()
    p._p.append(parse_xml(f'<w:r {NS}><w:t xml:space="preserve">美国组件均价约每瓦</w:t></w:r>'))
    p._p.append(parse_xml(f'<w:del {NS} w:id="1" w:author="主管" w:date="2026-09-01T00:00:00Z"><w:r><w:delText>0.290</w:delText></w:r></w:del>'))
    p._p.append(parse_xml(f'<w:ins {NS} w:id="2" w:author="主管" w:date="2026-09-01T00:00:00Z"><w:r><w:t>0.310</w:t></w:r></w:ins>'))
    p._p.append(parse_xml(f'<w:r {NS}><w:t xml:space="preserve">美元。</w:t></w:r>'))
    doc.add_paragraph('东南亚价格保持平稳。')
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def test_tracked_changes_split_into_before_and_after():
    before, after, changed = docx_versions(tracked_docx())
    assert changed
    assert '# 组件价格周报' in before and '## 价格' in before
    assert '每瓦0.290美元' in before and '0.310' not in before
    assert '每瓦0.310美元' in after and '0.290' not in after


def test_import_creates_original_and_revision_feedback_but_no_comparison_case(tmp_path):
    store = Store(tmp_path / 'ws')
    result = import_previous(store, '周报.docx', tracked_docx())
    assert result['tracked_changes'] and result['title'] == '组件价格周报'
    briefs = store.rows('SELECT author,markdown FROM briefs WHERE run_id=? ORDER BY rowid', (result['run_id'],))
    assert [b['author'] for b in briefs] == ['import', 'user']
    feedback = store.rows("SELECT * FROM feedback WHERE kind='revision'")
    assert len(feedback) == 1
    items = revision_edits.pending(store, [feedback[0]['id']])
    assert [(e['before'], e['after']) for e in items[0]['edits']] == [('美国组件均价约每瓦0.290美元。', '美国组件均价约每瓦0.310美元。')]
    provenance = json.loads((store.root / 'sources' / (result['source_id'] + '.provenance.json')).read_text())
    assert provenance['usage'] == 'previous_report'
    cases, skipped = _eligible_cases(store, [result['run_id']])
    assert cases == [] and '往期导入' in skipped[0]['reason']
    assert store.meta('requirements') is None


def test_markdown_import_has_no_revision(tmp_path):
    store = Store(tmp_path / 'ws')
    result = import_previous(store, 'weekly.md', '# 周报\n\n正文。'.encode())
    assert not result['tracked_changes']
    assert not store.rows("SELECT * FROM feedback WHERE kind='revision'")


def test_source_text_of_a_tracked_docx_reads_the_accepted_document(tmp_path):
    store = Store(tmp_path / 'ws')
    result = import_previous(store, '周报.docx', tracked_docx())
    text = store.source_text(result['source_id'])
    assert '每瓦0.310美元' in text and '0.290' not in text


def test_imported_report_is_reference_only_for_new_runs(tmp_path):
    import pytest
    store = Store(tmp_path / 'ws')
    previous = import_previous(store, 'weekly.md', '# 周报\n\n历史价格0.29。'.encode())
    sid = previous['source_id']
    assert next(s for s in store.snapshot()['sources'] if s['id'] == sid)['usage'] == 'previous_report'
    with pytest.raises(ValueError, match='参考材料'):
        store.create_run({'title': '本期周报', 'objective': '本期价格'}, [sid])
    current = store.add_source('本期原件', '本期价格0.31。')
    run = store.create_run({'title': '本期周报', 'objective': '本期价格', 'reference_source_ids': [sid]}, [current['id']])
    assert store.source_ids(run['id']) == [current['id']]
    with pytest.raises(ValueError, match='本期证据'):
        store.attach_source(run['id'], sid)
    another=store.create_run({'title':'本期','objective':'摘要'},[current['id']])
    with pytest.raises(ValueError,match='事实引用'):
        store.publish(another['id'],{'title':'本期','markdown':'历史价格0.29。','citations':[{'source_id':sid,'locator':'正文'}]})
    assert store.source_ids(another['id']) == [current['id']]


def test_docx_table_keeps_structure_and_separates_tracked_prices():
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '地区'
    table.cell(0, 1).text = '每瓦美元'
    table.cell(1, 0).text = '美国'
    p = table.cell(1, 1).paragraphs[0]
    p._p.append(parse_xml(f'<w:del {NS}><w:r><w:delText>0.290</w:delText></w:r></w:del>'))
    p._p.append(parse_xml(f'<w:ins {NS}><w:r><w:t>0.310</w:t></w:r></w:ins>'))
    stream = BytesIO();doc.save(stream)
    before, after, changed = docx_versions(stream.getvalue())
    assert changed and '| 美国 | 0.290 |' in before and '| 美国 | 0.310 |' in after
    assert '| 地区 | 每瓦美元 |\n| --- | --- |\n' in after
    from markdown_it import MarkdownIt
    html = MarkdownIt('commonmark').enable('table').render(after)
    assert '<table>' in html and '<td>0.310</td>' in html and '0.290' not in html


def test_content_control_and_deleted_table_row_are_preserved_as_versions(tmp_path):
    doc=Document();table=doc.add_table(rows=3,cols=2)
    for row,values in zip(table.rows,[('地区','价格'),('历史地区','0.290'),('美国','0.310')]):
        for cell,text in zip(row.cells,values):cell.text=text
    row=table.rows[1]._tr
    properties=parse_xml(f'<w:trPr {NS}><w:del w:id="3"/></w:trPr>');row.insert(0,properties)
    last=table.rows[2]._tr
    table._tbl.remove(last)
    control=parse_xml(f'<w:sdt {NS}><w:sdtContent/></w:sdt>')
    control.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent').append(last)
    table._tbl.append(control)
    stream=BytesIO();doc.save(stream)
    before,after,changed=docx_versions(stream.getvalue())
    assert changed and '历史地区' in before and '历史地区' not in after
    assert '| 美国 | 0.310 |' in before and '| 美国 | 0.310 |' in after
    store=Store(tmp_path/'ws');result=import_previous(store,'weekly.docx',stream.getvalue())
    text=store.source_text(result['source_id'])
    assert '历史地区' not in text and '0.290' not in text and '0.310' in text


def test_rich_revision_cannot_attach_previous_report_as_fact(tmp_path):
    import pytest
    store=Store(tmp_path/'ws')
    prior=import_previous(store,'past.md','# 历史\n\n历史价格0.29。'.encode())
    current=store.add_source('Current','本期价格0.31。')
    run=store.create_run({'title':'本期','objective':'当前'},[current['id']])
    brief=store.publish(run['id'],{'title':'本期','markdown':'本期价格0.31。'})
    document={'type':'doc','content':[{'type':'paragraph','content':[
        {'type':'text','text':'历史价格0.29。'},
        {'type':'citation','attrs':{'sourceId':prior['source_id']}}]}]}
    with pytest.raises(ValueError,match='事实引用'):
        store.revise(brief['id'],editor_document=document)
    assert store.source_ids(run['id']) == [current['id']]
    assert len(store.rows('SELECT id FROM briefs WHERE run_id=?',(run['id'],))) == 1
