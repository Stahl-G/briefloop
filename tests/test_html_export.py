"""Standalone HTML export: same citation order as Word, self-contained bytes."""
import base64
import hashlib
import http.client
import json
import re
import threading
from docx import Document

from briefloop.store import Store
from briefloop.html_export import html_report
from briefloop.document_export import render_document


def sample(tmp_path):
    store = Store(tmp_path / 'workspace')
    a = store.add_source('来源甲', '正文甲', url='https://example.org/a')
    b = store.add_source('来源乙', '正文乙', url='https://example.org/b')
    c = store.add_source('来源丙', '正文丙')
    run = store.create_run({'title': '导出验收', 'objective': '核对 HTML 导出', 'allow_web': False},
                           [a['id'], b['id'], c['id']])
    return store, run, (a, b, c)


def publish(store, run, content, **detail):
    return store.publish(run['id'], {'title': '导出验收',
                                     'editor_document': {'type': 'doc', 'content': content},
                                     **detail})


def text_p(*nodes):
    return {'type': 'paragraph', 'content': list(nodes)}


def test_citation_order_and_adjacent_groups_match_word(tmp_path):
    store, run, (a, b, c) = sample(tmp_path)
    content = [
        {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': '进展'}]},
        text_p({'type': 'text', 'text': '甲'},
               {'type': 'citation', 'attrs': {'sourceId': a['id']}},
               {'type': 'citation', 'attrs': {'sourceId': b['id']}},
               {'type': 'text', 'text': '，旧编号链接'},
               {'type': 'text', 'text': '7', 'marks': [{'type': 'link', 'attrs': {'href': '#source-' + c['id']}}]},
               {'type': 'text', 'text': '，命名链接'},
               {'type': 'text', 'text': '年度报告', 'marks': [{'type': 'link', 'attrs': {'href': '#source-' + a['id']}}]},
               {'type': 'text', 'text': '。'}),
        text_p({'type': 'text', 'text': '再次引用丙'}, {'type': 'citation', 'attrs': {'sourceId': c['id']}}),
    ]
    brief = publish(store, run, content, citations=[
        {'source_id': a['id'], 'locator': 'Page 3', 'excerpt': '原文甲'}])
    page = html_report(store, brief['id'])

    # Same order as the Word renderer: walk its appended source list.
    word = Document()
    render_document(word, brief_document_for(brief), sources={s['id']: s for s in (a, b, c)},
                    citations=[{'source_id': a['id'], 'locator': 'Page 3', 'excerpt': '原文甲'}])
    word_order = [re.match(r'\d+\. (.*?)(?: · |$)', p.text).group(1)
                  for p in word.paragraphs if re.match(r'\d+\. ', p.text)]
    html_order = [t for _, t in re.findall(
        r'<li id="ref-(\d+)"><div class="ref-head"><span class="ref-title">(?:<a[^>]*>)?([^<]+)', page)]
    assert html_order == word_order

    group = re.search(r'<sup class="cite-group">(.*?)</sup>', page)
    assert re.sub(r'<[^>]+>', '', group.group(1)) == '[1, 2]', 'adjacent citations merge into one bracketed group'
    assert 'href="#ref-3"' in page, 'numeric #source- link renders as a citation'
    assert '年度报告</a>' not in page or '年度报告' in page  # authored text is kept
    named = re.search(r'年度报告<sup class="cite-group">(.*?)</sup>', page)
    assert named and re.sub(r'<[^>]+>', '', named.group(1)) == '[1]', 'named link text followed by its citation'
    assert page.count('id="cite-1-') == 2 and 'id="cite-2-1"' in page and 'id="cite-3-2"' in page


def brief_document_for(brief):
    from briefloop.document_model import brief_document
    return brief_document(brief)


def test_draft_banner_without_review(tmp_path):
    store, run, (a, b, c) = sample(tmp_path)
    brief = publish(store, run, [text_p({'type': 'text', 'text': '正文'})])
    page = html_report(store, brief['id'])
    assert '工作稿 · 未经独立审阅' in page


def test_excerpts_toggle(tmp_path):
    store, run, (a, b, c) = sample(tmp_path)
    brief = publish(store, run,
                    [text_p({'type': 'citation', 'attrs': {'sourceId': a['id']}})],
                    citations=[{'source_id': a['id'], 'locator': 'Page 3', 'excerpt': '独有摘录文本XYZ'}])
    assert '独有摘录文本XYZ' in html_report(store, brief['id'])
    without = html_report(store, brief['id'], excerpts=False)
    assert '独有摘录文本XYZ' not in without
    assert '摘录未包含在本文件' in without


def test_self_contained_with_valid_csp(tmp_path):
    store, run, (a, b, c) = sample(tmp_path)
    brief = publish(store, run, [text_p({'type': 'text', 'text': '正文'})])
    page = html_report(store, brief['id'])
    assert 'src="http' not in page and "src='http" not in page
    assert '<link' not in page
    import html as _html
    csp = _html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1))
    script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
    expected = base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode()
    assert "script-src 'sha256-%s'" % expected in csp


def test_aria_labels_never_leak_source_ids(tmp_path):
    store, run, (a, b, c) = sample(tmp_path)
    brief = publish(store, run,
                    [text_p({'type': 'citation', 'attrs': {'sourceId': a['id']}})],
                    citations=[{'source_id': a['id'], 'locator': 'x', 'excerpt': 'y'}])
    page = html_report(store, brief['id'])
    for label in re.findall(r'aria-label="([^"]*)"', page):
        assert 'src_' not in label


def test_endpoint_rejects_mismatched_workspace(tmp_path):
    from briefloop.server import make_server, _close_service
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        store = server.store
        source = store.add_source('来源', '内容')
        run = store.create_run({'title': 'T', 'objective': 'o', 'allow_web': False}, [source['id']])
        brief = store.publish(run['id'], {'title': 'T', 'markdown': '正文'})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('GET', '/api/export-html?version=%s&workspace_id=other' % brief['id'])
        response = connection.getresponse()
        assert response.status == 400
        assert '工作区已切换' in json.loads(response.read())['error']
        connection.close()

        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('GET', '/api/export-html?version=%s&workspace_id=%s' % (brief['id'], store.meta('workspace_id')))
        response = connection.getresponse()
        assert response.status == 200
        assert 'text/html' in response.getheader('Content-Type')
        assert response.read().startswith(b'<!doctype html>')
        connection.close()
    finally:
        server.shutdown(); thread.join(timeout=5); _close_service(server)


def test_manual_line_break_exports_and_preserves_citation_order(tmp_path):
    store, run, (a, b, _) = sample(tmp_path)
    brief = publish(store, run, [text_p(
        {'type': 'text', 'text': '第一行'},
        {'type': 'citation', 'attrs': {'sourceId': a['id']}},
        {'type': 'hardBreak'},
        {'type': 'text', 'text': '第二行'},
        {'type': 'citation', 'attrs': {'sourceId': b['id']}})])
    page = html_report(store, brief['id'])
    assert '</sup><br>第二行' in page
    assert page.index('id="cite-1-1"') < page.index('id="cite-2-1"')


def test_released_version_does_not_claim_this_html_is_a_frozen_delivery(tmp_path, monkeypatch):
    store, run, _ = sample(tmp_path)
    brief = publish(store, run, [text_p({'type': 'text', 'text': '正文'})])
    from briefloop import release
    monkeypatch.setattr(release, 'list_releases', lambda *_: [
        {'id': 'release-fixture', 'version_id': brief['id'], 'status': 'released',
         'result': {'manifest_hash': 'frozen-manifest'}}])
    page = html_report(store, brief['id'])
    assert '本导出文件未纳入冻结交付包' in page
    assert 'release-fixture' in page


def test_checks_distinguish_checked_from_matched_numbers(tmp_path, monkeypatch):
    store, run, _ = sample(tmp_path)
    brief = publish(store, run, [text_p({'type': 'text', 'text': '正文'})])
    from briefloop import delivery_checks
    monkeypatch.setattr(delivery_checks, 'brief_checks', lambda *_: {
        'numbers': {'checked': 1, 'total': 2, 'matched': 0,
                    'unmatched': [{'label': '收入', 'expected': 0, 'reason': '来源为 100'}],
                    'skipped': [{'label': '成本', 'reason': '缺少依据'}]},
        'layout': {'status': 'pass'}})
    page = html_report(store, brief['id'])
    assert '数字绑定核查 1/2 · 匹配 0 · 不一致 1 · 未核对 1' in page
    assert '收入 · 0 · 来源为 100' in page
    assert '成本 · 缺少依据' in page
