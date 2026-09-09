from io import BytesIO
from zipfile import ZipFile
import json
import pytest
from PIL import Image
from briefloop.store import Store
from briefloop.figures import register_figure
from briefloop.figure_support import export_figures,markdown_bundle
from briefloop.exports import docx_bytes


def test_insert_figure_preserves_user_prose_and_portable_downloads(tmp_path):
    store=Store(tmp_path/'workspace');source=store.add_source('dataset','x,y\n1,2')
    run=store.create_run({'title':'test','objective':'show data'},[source['id']]);base=store.publish(run['id'],{'title':'test','markdown':'## Before\n\nExact user text.\n\n## After\n\nEnd.'})
    png=store.root/'plot.png';Image.new('RGB',(400,200),'blue').save(png)
    data=store.root/'data.csv';data.write_text('x,y\n1,2')
    figure=register_figure(store,run['id'],png,'Observed chart',caption='dataset row 2',source_ids=[source['id']],data_path=data)
    text=base['markdown'].replace('## After',figure['markdown']+'\n\n## After')
    new=store.attach_figures(base['id'],text)
    assert new['parent_id']==base['id'] and not store.rows('select * from feedback')
    assert store.one('briefs',base['id'])['markdown']==base['markdown']
    assert json.loads(new['detail'])['figures']==[figure['figure_id']]
    blob=docx_bytes(new['markdown'],figures=export_figures(store,new))
    with ZipFile(BytesIO(blob)) as z:
        text=z.read('word/document.xml').decode();assert text.index('Exact user text')<text.index('w:drawing')<text.index('After')
    with ZipFile(BytesIO(markdown_bundle(store,new))) as z:
        md=z.read('report.md').decode();assert 'briefloop-figure:' not in md and 'assets/'+figure['figure_id']+'.png' in md
    with pytest.raises(ValueError,match='不改写'):store.attach_figures(new['id'],new['markdown'].replace('Exact user text','Different'))


def test_xlsx_text_and_embedded_pixels(tmp_path):
    from briefloop.sources import upload
    from briefloop.workbook_figures import extract_workbook_figures
    png=BytesIO();Image.new('RGB',(12,7),'green').save(png,format='PNG')
    data=BytesIO()
    with ZipFile(data,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row><c r="A1"><v>12</v></c></row></sheetData><drawing r:id="d1"/></worksheet>')
        z.writestr('xl/worksheets/_rels/sheet1.xml.rels','<Relationships><Relationship Id="d1" Target="../drawings/drawing1.xml"/></Relationships>')
        z.writestr('xl/drawings/drawing1.xml','<wsDr xmlns="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><oneCellAnchor><from><col>0</col><row>2</row></from><pic><blipFill><a:blip r:embed="i1"/></blipFill></pic></oneCellAnchor></wsDr>')
        z.writestr('xl/drawings/_rels/drawing1.xml.rels','<Relationships><Relationship Id="i1" Target="../media/image1.png"/></Relationships>');z.writestr('xl/media/image1.png',png.getvalue())
    store=Store(tmp_path/'workspace');source=upload(store,'book.xlsx',data.getvalue());assert source['status']=='ready' and 'A1: 12' in store.source_text(source['id'])
    figures=extract_workbook_figures(store,source['id'])['figures'];assert len(figures)==1
    from pathlib import Path
    assert Path(figures[0]['image_path']).read_bytes()==png.getvalue()
