from io import BytesIO
from zipfile import ZipFile
import json
import threading
from docx import Document
from docx.shared import Pt, RGBColor
from briefloop.store import Store
from briefloop.templates import import_template, prepare, export_template
from briefloop.export_jobs import enqueue_export, generate_word, output_path, export_input


def test_rebuild_keeps_local_run_overrides_out_of_body_defaults(tmp_path):
    from docx.enum.style import WD_STYLE_TYPE
    from briefloop.templates import rebuild_template_version
    original=Document()
    base=original.styles.add_style('Report Body',WD_STYLE_TYPE.PARAGRAPH)
    base.base_style=original.styles['Normal']
    base.font.color.rgb=RGBColor.from_string('222222');base.font.size=Pt(11);base.font.name='Calibri'
    original.add_paragraph('Monthly Report');original.add_paragraph('1. Summary')
    sample=original.add_paragraph(style=base)
    lead=sample.add_run('Key point: ');lead.bold=True;lead.font.color.rgb=RGBColor.from_string('CC0000')
    lead.font.size=Pt(16);lead.font.name='Cambria';lead.font.all_caps=True
    sample.add_run('This ordinary paragraph inherits the dark body style; only the lead is red and enlarged.')
    stream=BytesIO();original.save(stream);store=Store(tmp_path)
    record=import_template(store,'Monthly.docx',stream.getvalue(),prepare_job=False)
    prepared=prepare(store,record['id'],{'keep_blocks':[0],'paragraph_index':2,
        'sections':[{'section_id':'summary','title':'1. Summary','index':1}],
        'fields':[{'old':'Monthly Report','field':'title'}]})
    previous_path=store.root/'templates'/prepared['id']/'prepared.docx';previous_bytes=previous_path.read_bytes()
    rebuilt=rebuild_template_version(store,prepared['id'])
    source=store.add_source('Current material','The current operating result.')
    run=store.create_run({'title':'Current Report','objective':'Explain','template_id':rebuilt['id']},[source['id']])
    document={'type':'doc','content':[{'type':'paragraph','content':[
        {'type':'text','text':'Ordinary current text. '},
        {'type':'text','text':'Explicit blue text.','marks':[{'type':'textStyle','attrs':{'color':'#0000FF'}}]}]}]}
    brief=store.publish(run['id'],{'title':'Current Report','editor_document':document})
    rendered=Document(BytesIO(export_template(store,brief,document,{})))
    body=next(p for p in rendered.paragraphs if p.text.startswith('Ordinary current text.'))
    style=body.style
    assert style.font.color.rgb is None and style.font.size is None and style.font.name is None
    assert style.font.all_caps is None and style.font.bold is None
    assert style.base_style.name=='Report Body'
    assert str(style.base_style.font.color.rgb)=='222222' and style.base_style.font.size==Pt(11)
    assert style.base_style.font.name=='Calibri'
    assert str(body.runs[-1].font.color.rgb)=='0000FF'
    assert previous_path.read_bytes()==previous_bytes


