from io import BytesIO
from zipfile import ZipFile
import json
from PIL import Image
from pypdf import PdfWriter
from briefloop.store import Store, semantic_signature
from briefloop.sources import upload
from briefloop.runtime import generation_prompt, assessment_prompt, source_context
from briefloop.figures import register_figure
from briefloop.figure_support import markdown_bundle


def test_failed_pdf_is_a_gap_but_legacy_visual_pdf_still_works(tmp_path):
    store=Store(tmp_path)
    good=store.add_source('normal','Revenue 12 USD.')
    bad=upload(store,'broken.pdf',b'%PDF-invalid')
    run=store.create_run({'title':'Report','objective':'Summarize'},[good['id'],bad['id']])
    folder=store.root/'jobs'/'probe';folder.mkdir()
    generation_prompt(store,run,folder)
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue 12 USD.', 'citations':[{'source_id':good['id']}]})
    assessment_prompt(store,brief,folder)
    failed=source_context(store,bad['id'])
    assert failed['status']=='failed' and failed['error']
    assert failed['image_path'] is None and failed['original_path'] is None
    assert store.one('sources',bad['id'])['status']=='failed'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    data=BytesIO();writer.write(data)
    scan=upload(store,'scan.pdf',data.getvalue())
    with store.tx() as c:
        c.execute("UPDATE sources SET status='failed',error='legacy extraction failure' WHERE id=?",(scan['id'],))
    visual=source_context(store,scan['id'])
    assert visual['status']=='ready' and visual['needs_visual'] and visual['pages']==1


def test_same_title_figure_replacement_learns_and_bundle_keeps_notes(tmp_path):
    store=Store(tmp_path)
    src=store.add_source('Synthetic source','Evidence')
    run=store.create_run({'title':'Report','objective':'Compare'},[src['id']])
    path=store.root/'plot.png';figures=[]
    caption='Units: USD; as of 2026-09-09; illustrative only'
    for color in ('red','blue'):
        Image.new('RGB',(20,20),color).save(path)
        figures.append(register_figure(store,run['id'],path,'Same title',caption,source_ids=[src['id']]))
    b=store.publish(run['id'],{'title':'Report','markdown':figures[0]['markdown']})
    before=len(store.rows('SELECT * FROM feedback'))
    revised=store.revise(b['id'],figures[1]['markdown'])
    assert len(store.rows('SELECT * FROM feedback'))==before+1
    assert json.loads(revised['detail'])['figures']==[figures[1]['figure_id']]
    fid=figures[1]['figure_id']
    assert semantic_signature(figures[1]['markdown'])==semantic_signature(f'![Same title](/api/figure?id={fid}&version=other)')
    assert semantic_signature(figures[1]['markdown'])!=semantic_signature(f'![Same title](https://example.com/api/figure?id={fid})')
    with ZipFile(BytesIO(markdown_bundle(store,revised))) as archive:
        md=archive.read('report.md').decode()
        assert caption in md and src['name'] in md
        assert f'assets/{fid}.png' in md
        assert archive.read(f'assets/{fid}.png')
        assert 'briefloop-figure:' not in md
