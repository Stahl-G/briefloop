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


def test_workspace_action_export_word_accepts_template_override(tmp_path):
    import json
    from briefloop.chat_tools import workspace_action
    store = Store(tmp_path)
    import_builtin(store)
    rows = {r['name']: r['id'] for r in store.rows('SELECT id,name FROM templates')}
    document = {'type': 'doc', 'content': [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '一、摘要'}]},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '正文。'}]}]}
    source = store.add_source('S', 'evidence')
    run = store.create_run({'title': 'x', 'objective': 'y'}, [source['id']])
    brief = store.publish(run['id'], {'title': 'x', 'editor_document': document})
    result = workspace_action(store, {'action': 'export_word', 'version_id': brief['id'],
                                      'template_id': rows['商业报告·珊瑚红']})
    payload = json.loads(store.one('jobs', result['job_id'])['payload'])
    assert payload['template_id'] == rows['商业报告·珊瑚红'] and result['template_id'] == rows['商业报告·珊瑚红']
    result = workspace_action(store, {'action': 'export_word', 'version_id': brief['id']})
    assert 'template_id' not in json.loads(store.one('jobs', result['job_id'])['payload'])
    capabilities = workspace_action(store, {'action': 'capabilities'})
    assert '同稿换版式' in capabilities['export_word.template_id']
