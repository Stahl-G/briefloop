from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

from lxml import etree
from PIL import Image
import pytest

from briefloop.exports import docx_bytes


NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}


def test_registered_figure_stays_at_markdown_position_and_fits_page():
    image = BytesIO()
    Image.new('RGB', (100, 2000), '#17466B').save(image, 'PNG')
    figures = {'fig_fixture': {'image_bytes': image.getvalue(), 'title': '图 1 数据比较',
                              'caption': '同口径实际值。', 'source_labels': ['公开数据表']}}
    markdown = '正文前段。\n\n![指标图](briefloop-figure:fig_fixture)\n\n[正文后段](https://example.com)。'
    forecasts = [dict(metric='A', unit='GW', category='forecast', as_of='2026-09-09',
                      current_date=f'{year}-12-31', current=value) for year, value in [(2026, 10), (2027, 12)]]
    archive = ZipFile(BytesIO(docx_bytes(markdown, figures=figures, report_profile='industry_periodic', report_data={'records': forecasts})))
    document = etree.fromstring(archive.read('word/document.xml'))
    assert len([n for n in archive.namelist() if n.startswith('word/media/')]) == 1
    paragraphs = document.xpath('//w:body/w:p', namespaces=NS)
    texts = [''.join(p.xpath('.//w:t/text()', namespaces=NS)) for p in paragraphs]
    drawing = next(i for i, p in enumerate(paragraphs) if p.xpath('.//w:drawing', namespaces=NS))
    assert texts.index('正文前段。') < drawing < next(i for i, t in enumerate(texts) if '图 1 数据比较' in t) < texts.index('正文后段。')
    assert '同口径实际值。来源：公开数据表' in ''.join(texts)
    extent = document.xpath('//wp:extent', namespaces=NS)[0]
    assert 0 < int(extent.get('cx')) <= 160 * 36000
    assert 0 < int(extent.get('cy')) <= 180 * 36000
    assert paragraphs[drawing].xpath('./w:pPr/w:spacing/@w:lineRule', namespaces=NS) == ['auto']
    # New caller opt-in suppresses the legacy automatic end chart even when no figure is present.
    empty = ZipFile(BytesIO(docx_bytes('正文。', figures={}, report_profile='industry_periodic', report_data={'records': forecasts})))
    assert not any(n.startswith('word/media/') for n in empty.namelist())
    with pytest.raises(ValueError, match='未提供已授权'):
        docx_bytes(markdown, figures={})
    with pytest.raises(ValueError, match='PNG 数据无效'):
        docx_bytes(markdown, figures={'fig_fixture': {'image_bytes': b'not a PNG'}})


def test_actual_editor_markdown_roundtrip_keeps_figure_and_citation():
    repo = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    if not node or not (repo / 'node_modules/@tiptap/markdown').is_dir():
        pytest.skip('Frontend source check requires npm ci and Node.js')
    script = r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {MarkdownManager} from '@tiptap/markdown';
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {TableKit} from '@tiptap/extension-table';
import Image from '@tiptap/extension-image';
const app=readFileSync('frontend/app.js','utf8');
const mapping=app.split('// BEGIN_FIGURE_EDITOR_MAPPING')[1].split('\n').slice(1).join('\n').split('// END_FIGURE_EDITOR_MAPPING')[0];
const context={URL,window:{location:{origin:'http://127.0.0.1:8765'}},state:{sources:[{id:'src_fixture'}]},current:{id:'brief_fixture'}};
vm.createContext(context);vm.runInContext(mapping+'\nglobalThis.convert={toEditor,fromEditor};',context);
const original='前段。[@src_fixture]\n\n![指标说明](briefloop-figure:fig_fixture "图题")\n\n后段。';
const displayed=context.convert.toEditor(original);
assert.match(displayed,/\/api\/figure\?id=fig_fixture&version=brief_fixture/);
assert.match(app,/Image\.configure\(/);
const extensions=[StarterKit,TableKit,Image.configure({HTMLAttributes:{class:'briefloop-figure'},allowBase64:false})];
const manager=new MarkdownManager({extensions});
const doc=manager.parse(displayed);
getSchema(extensions).nodeFromJSON(doc).check();
const image=doc.content.find(node=>node.type==='image');
assert.equal(image.attrs.src,'/api/figure?id=fig_fixture&version=brief_fixture');
assert.equal(image.attrs.alt,'指标说明');
const saved=context.convert.fromEditor(manager.serialize(doc));
assert.match(saved,/!\[指标说明\]\(briefloop-figure:fig_fixture "图题"\)/);
assert.ok(saved.indexOf('前段')<saved.indexOf('briefloop-figure:fig_fixture'));
assert.ok(saved.indexOf('briefloop-figure:fig_fixture')<saved.indexOf('后段'));
assert.ok(saved.includes('[@src_fixture]'));
assert.ok(!saved.includes('/api/figure'));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=repo, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout
