from io import BytesIO
from zipfile import ZipFile
from lxml import etree
from briefloop.exports import docx_bytes


def parts(blob):
    archive = ZipFile(BytesIO(blob))
    return archive, etree.fromstring(archive.read('word/document.xml'))


def test_industry_layout_links_and_table():
    blob = docx_bytes('## 核心摘要\n\n一项**明确结论**。[原文](https://example.com/report)\n\n## 行业数据\n\n| 指标 | 本期 |\n|---|---|\n| 指标 A | 12 |', report_profile='industry_periodic', title='示例行业定期报告', organization='示例组织', report_date='2026-09-10')
    archive, document = parts(blob)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    assert document.xpath('count(//w:tblHeader)', namespaces=ns) == 1
    assert document.xpath('//w:shd/@w:fill', namespaces=ns)[0] == '17466B'
    assert document.xpath('count(//w:hyperlink)', namespaces=ns) == 1
    assert b'https://example.com/report' in archive.read('word/_rels/document.xml.rels')
    assert document.xpath('//w:pgSz/@w:w', namespaces=ns) == ['11906']
    assert 'PAGE' in archive.read('word/footer1.xml').decode()


def test_plain_export_remains_plain_and_does_not_fetch_images():
    archive, document = parts(docx_bytes('# 标题\n\n[链接](https://example.com) ![示意说明](file:///private/no-read.png)'))
    assert not any(name.startswith('word/media/') for name in archive.namelist())
    assert b'https://example.com' in archive.read('word/_rels/document.xml.rels')
    assert '示意说明' in ''.join(document.itertext())
    assert '行业定期报告' not in ''.join(document.itertext())


def test_chart_uses_only_comparable_forecasts():
    records = [dict(metric='指标 A',unit='GW',region='区域 A',product='产品 A',category='forecast',as_of='2026-09-09',current_date=f'{year}-12-31',current=value,source_id='source-a',locator='表 1') for year,value in [(2026,10),(2027,12),(2028,15)]]
    archive, document = parts(docx_bytes('## 需求预测\n\n预测数据。', report_profile='industry_periodic', report_data={'records':records}))
    assert len([name for name in archive.namelist() if name.startswith('word/media/')]) == 1
    assert '来源1' in ''.join(document.itertext())
    assert 'source-a' not in ''.join(document.itertext())
    named = [dict(record, source_label='示例公开数据集') for record in records]
    _, document = parts(docx_bytes('正文', report_profile='industry_periodic', report_data={'records':named}))
    assert '示例公开数据集' in ''.join(document.itertext())
    assert 'source-a' not in ''.join(document.itertext())
    for record in records: record['unit'] = record['current_date']
    archive, _ = parts(docx_bytes('正文', report_profile='industry_periodic', report_data={'records':records}))
    assert not any(name.startswith('word/media/') for name in archive.namelist())


def test_chart_rejects_missing_or_mixed_vintages_and_nonfinite_values():
    records = [dict(metric='指标 A',unit='GW',category='forecast',current_date=f'{year}-12-31',current=value) for year,value in [(2026,10),(2027,12)]]
    for variants in [records, [dict(r,as_of=r['current_date']) for r in records], [dict(r,as_of='2026-09-09',current='Infinity') for r in records], [dict(r,as_of='2026-09-09',current='NaN') for r in records], [dict(records[0],as_of='2026-09-09'),dict(records[1],as_of='not-a-date')]]:
        archive, _ = parts(docx_bytes('正文',report_profile='industry_periodic',report_data={'records':variants}))
        assert not any(name.startswith('word/media/') for name in archive.namelist())
