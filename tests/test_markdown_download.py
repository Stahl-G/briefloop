import http.client
import threading

from briefloop.server import make_server, _close_service


def test_markdown_export_is_an_attachment_and_keeps_saved_body(tmp_path):
    server = make_server(tmp_path / 'workspace', port=0, paused=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run = server.store.create_run({'title': '合成下载验收', 'objective': '验证下载'}, [])
        brief = server.store.publish(run['id'], {'title': '合成下载验收', 'markdown': '## 合成正文\n\n保存的内容。'})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/download?version=' + brief['id'])
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader('Content-Disposition') == "attachment; filename*=UTF-8''report.md"
            assert response.getheader('Content-Type') == 'text/markdown; charset=utf-8'
            assert '保存的内容。' in response.read().decode()
            connection.request('GET', '/')
            assert connection.getresponse().status == 200
        finally:
            connection.close()
        assert server.store.one('briefs', brief['id']) == brief
    finally:
        server.shutdown()
        thread.join(timeout=5)
        _close_service(server)
