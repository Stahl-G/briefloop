from io import BytesIO
from zipfile import ZipFile
import json
import threading
from docx import Document
from docx.shared import Pt, RGBColor
from briefloop.store import Store
from briefloop.templates import import_template, prepare, export_template
from briefloop.export_jobs import enqueue_export, generate_word, output_path


def test_template_reuses_direct_styles_replaces_old_facts_and_locks_version(tmp_path):
    original=Document();original.sections[0].header.paragraphs[0].text='Example Corp · August 2025'
    original.add_paragraph('Monthly Report')
    original.add_paragraph('August 2025')
    heading=original.add_paragraph('一、核心摘要');heading.runs[0].bold=True;heading.runs[0].font.size=Pt(17);heading.runs[0].font.color.rgb=RGBColor.from_string('17466B')
    body=original.add_paragraph('OLD FACT REVENUE 987654');body.runs[0].font.size=Pt(11)
    original.add_paragraph('二、经营影响')
    original.add_paragraph('OLD FACT 2025')
    stream=BytesIO();original.save(stream)
    store=Store(tmp_path);record=import_template(store,'Monthly.docx',stream.getvalue())
    prepared=prepare(store,record['id'],{'keep_blocks':[0,1],'paragraph_index':3,
        'sections':[{'section_id':'summary','title':'一、核心摘要','index':2,'purpose':'变化'},
                    {'section_id':'impact','title':'二、经营影响','index':4,'purpose':'影响'}],
        'fields':[{'old':'Monthly Report','field':'title'},{'old':'August 2025','field':'period'}]})
    assert prepared['status']=='ready'
    source=store.add_source('Current','Current revenue 12')
    run=store.create_run({'title':'August Report','objective':'Explain','period':'August 2026','template_id':record['id']},[source['id']])
    assert json.loads(run['requirements'])['sections'][0]['section_id']=='summary'
    doc={'type':'doc','content':[{'type':'heading','attrs':{'level':2,'blockId':'summary'},'content':[{'type':'text','text':'一、核心摘要'}]},
        {'type':'paragraph','content':[{'type':'text','text':'CURRENT FACT 12'}]},
        {'type':'heading','attrs':{'level':2,'blockId':'impact'},'content':[{'type':'text','text':'二、经营影响'}]},
        {'type':'paragraph','content':[{'type':'text','text':'Impact on customers'}]}]}
    brief=store.publish(run['id'],{'title':'August Report','editor_document':doc})
    job=enqueue_export(store,brief['id']);result=generate_word(store,job,threading.Event())
    with ZipFile(output_path(store,job)) as archive:
        xml=archive.read('word/document.xml').decode();styles=archive.read('word/styles.xml').decode()
        header=archive.read('word/header1.xml').decode()
        assert 'CURRENT FACT 12' in xml and 'OLD FACT' not in xml and 'August 2025' not in xml
        assert 'August 2026' in xml and 'August 2026' in header
        assert '17466B' in styles and 'w:sz w:val="34"' in styles
    store.set_meta('settings',{**store.settings(),'default_template_id':'new-default'})
    assert json.loads(store.one('runs',run['id'])['requirements'])['template_id']==record['id']
    assert result['version_id']==brief['id']
