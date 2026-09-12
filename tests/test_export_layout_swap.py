"""The same saved version exports under any ready template: content identical, layout swapped."""
import io
import threading
from briefloop.store import Store
from briefloop.templates import import_builtin
from briefloop.export_jobs import enqueue_export, generate_word, output_path, export_input


def test_layout_swap_exports_same_content_under_different_templates(tmp_path):
    store = Store(tmp_path)
    import_builtin(store)
    rows = {r['name']: r['id'] for r in store.rows('SELECT id,name FROM templates')}
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '一、摘要'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '同一份正文，两种版式。'}]}]}
    source = store.add_source('S', 'evidence')
    # The run itself was created without any template: the swap is per export.
    run = store.create_run({'title': '周报', 'objective': 'x'}, [source['id']])
    brief = store.publish(run['id'], {'title': '周报', 'editor_document': document})

    generic = enqueue_export(store, brief['id'])
    swapped = enqueue_export(store, brief['id'], template_override=rows['商业报告·珊瑚红'])
    assert generic['id'] != swapped['id'], 'a layout swap is a new export job, not a cache hit'
    assert export_input(store, brief)[0]['renderer'] == 6
    assert export_input(store, brief, template_override=rows['商业报告·珊瑚红'])[0]['renderer'] == 5

    generate_word(store, swapped, threading.Event())
    with __import__('zipfile').ZipFile(output_path(store, swapped)) as archive:
        styles = archive.read('word/styles.xml').decode()
        document_xml = archive.read('word/document.xml').decode()
    assert 'w:eastAsia="微软雅黑"' in styles, 'the swapped export carries the coral theme fonts'
    assert '同一份正文，两种版式。' in document_xml, 'content is byte-identical across layouts'

    default_job = enqueue_export(store, brief['id'])
    result = generate_word(store, default_job, threading.Event())
    assert result['version_id'] == brief['id']
    with __import__('zipfile').ZipFile(output_path(store, default_job)) as archive:
        plain_styles = archive.read('word/styles.xml').decode()
    assert 'w:eastAsia="微软雅黑"' not in plain_styles, 'without an override the generic renderer stays in charge'
