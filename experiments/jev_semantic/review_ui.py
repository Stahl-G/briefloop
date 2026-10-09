import html
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from assessment import review_queue
from dataset import digest, dump, json_lines, load_dataset, now
from engine import TASKS, append_event

STYLE = '''
:root{--paper:#faf9f6;--ink:#1e2320;--muted:#6a706b;--line:#dedfd8;--green:#2448B8;--green-hover:#1B3A8F;--danger:#a5472e;--surface:#ffffff;--soft:#f4f6ef;--radius:9px;--font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}body{background:var(--paper);color:var(--ink);font:15px/1.7 var(--font);margin:0}main{max-width:1100px;margin:auto;padding:24px}h1{font-size:23px}h2{font-size:17px}a{color:var(--green)}.muted{color:var(--muted)}.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:24px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft);padding:16px;font:13px/1.7 var(--mono);max-height:560px;overflow:auto}label{display:block;margin:12px 0 4px}select,input,textarea,button{font:inherit;border-radius:6px;padding:8px 12px}select,input,textarea{width:100%;border:1px solid var(--line);background:var(--surface);color:var(--ink)}button{background:var(--green);color:var(--surface);border:0;cursor:pointer;margin-top:16px}button:hover{background:var(--green-hover)}button:active{transform:translateY(1px)}button:disabled{opacity:.6;cursor:wait}:focus-visible{outline:2px solid var(--green);outline-offset:3px}.success{color:var(--green)}.error{color:var(--danger)}nav{display:flex;gap:16px;flex-wrap:wrap}mark{background:var(--soft);text-decoration:underline;text-decoration-color:var(--green);text-decoration-thickness:3px}summary{cursor:pointer}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:8px;text-align:left}@media(max-width:780px){main{padding:16px}.panel{padding:16px}}
'''
ACTIONS = {'pending': '待复核', 'confirmed': '确认提示，提交后续修订',
           'rejected_with_basis': '依据原件驳回提示', 'needs_material': '需补充材料',
           'revision_verified': '已对照新版本核实修订'}


def escape(value):
    return html.escape(str(value), quote=True)


def panel(title, value):
    return '<details class="panel" open><summary>' + escape(title) + '</summary><pre>' + escape(
        json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'


def serve(dataset, annotations, port=0, mode='label', run_folder=None):
    manifest, cases = load_dataset(dataset)
    if mode not in ('label', 'review') or mode == 'review' and not run_folder:
        raise ValueError('复核模式需指定一次实验运行')
    queue = {r['case_id']: r for r in review_queue(dataset, run_folder)} if run_folder else {}
    if mode == 'review':
        cases = [c for c in cases if c['case_id'] in queue]
    cases = sorted(cases, key=lambda c: digest(manifest['dataset_id'] + c['case_id']))
    by_id = {c['case_id']: c for c in cases}
    annotations = Path(annotations)
    annotations.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    visits = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            return host == expected and (origin is None or origin == 'http://' + expected)

        def send(self, status, body):
            data = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, '来源不允许')
            query = parse_qs(urlsplit(self.path).query)
            identity = query.get('case', [cases[0]['case_id'] if cases else ''])[0]
            selected = by_id.get(identity)
            if selected is None:
                return self.send(200, '<h1>此数据集没有可标注对象</h1>')
            visit = secrets.token_urlsafe(20)
            if len(visits) > 500:
                visits.clear()
            visits[visit] = (identity, time.monotonic())
            index = cases.index(selected)
            previous = cases[(index - 1) % len(cases)]['case_id']
            following = cases[(index + 1) % len(cases)]['case_id']
            rows = json_lines(annotations)
            saved_count = len({r['case_id'] for r in rows if r.get('kind') == mode and r.get('dataset_id') == manifest['dataset_id']})
            task = TASKS[selected['task']]
            heading = '盲标局部判断' if mode == 'label' else '实验复核清单'
            body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            body += '<title>BriefLoop 语义判断实验</title><style>' + STYLE + '</style><main><h1>' + heading + '</h1>'
            body += '<p class="muted">旁路实验，不改变报告、正式交付或 Wiki。标签模式不显示模型结果；保留全部数值与低优先级项。</p>'
            if query.get('saved'):
                body += '<p class="success" role="status">已保存记录；尚不表示生产问题已解决。</p>'
            body += f'<nav><a href="/?case={previous}">上一项</a><span>{index + 1} / {len(cases)} · 已记录 {saved_count} 项</span><a href="/?case={following}">下一项</a></nav>'
            body += '<section class="panel"><h2>' + escape(task['title']) + '</h2><p>' + escape(task['instructions']) + '</p>'
            body += '<p class="muted">材料类型：' + escape(selected['origin']) + ' · 划分：' + escape(selected['split']) + '</p>'
            state = selected['state']
            if selected['task'] == 'numeric_omission':
                text, target = state['paragraph'], state['target']
                body += '<p>' + escape(text[:target['start']]) + '<mark>' + escape(text[target['start']:target['end']]) + '</mark>' + escape(text[target['end']:]) + '</p>'
            body += '</section>' + panel('判断输入（完整保留，无隐藏截尾）', state)
            body += panel('实际采用的证据与定位核验', selected['evidence'])
            body += panel('版本身份与程序观察（不代表语义真值）', {'provenance': selected['provenance'], 'observed': selected['observed']})
            if mode == 'review':
                current_queue = {r['case_id']: r for r in review_queue(dataset, run_folder, annotations)}
                body += panel('模型候选与下一步', current_queue[identity])
            choices = task['criteria'] if mode == 'label' else ACTIONS
            body += '<form method="post" action="/record" class="panel"><h2>保存人工标签' if mode == 'label' else '<form method="post" action="/record" class="panel"><h2>保存处理记录'
            body += '</h2>'
            for name, value in {'csrf': token, 'visit': visit, 'case_id': identity}.items():
                body += f'<input type="hidden" name="{name}" value="{escape(value)}">'
            body += '<label for="reviewer">复核人标识</label><input id="reviewer" name="reviewer" required maxlength="80">'
            body += '<label for="choice">判断</label><select id="choice" name="choice" required><option value="">请选择</option>'
            body += ''.join('<option value="' + escape(k) + '">' + escape(v) + '</option>' for k, v in choices.items()) + '</select>'
            body += '<label for="reason">对照原件的依据／歧义说明</label><textarea id="reason" name="reason" required rows="4" maxlength="6000"></textarea>'
            if mode == 'review':
                body += '<label for="revision">新版本 ID 或后续任务定位（确认完成修订时必填）</label><input id="revision" name="revision" maxlength="300">'
            body += '<p class="muted">页面停留时间仅作原始记录，不等于有效工作时间或节省时间。</p><button type="submit">保存并继续</button></form></main></html>'
            self.send(200, body)

        def do_POST(self):
            if not self.allowed() or self.path != '/record':
                return self.send(403, '来源或路径不允许')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16000:
                    raise ValueError('表单大小无效')
                data = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                if not secrets.compare_digest(data.get('csrf', ''), token):
                    raise ValueError('请求身份无效')
                visit = visits.pop(data.get('visit', ''), None)
                identity = data.get('case_id')
                if not visit or visit[0] != identity or identity not in by_id:
                    raise ValueError('页面已过期，请刷新')
                spec = TASKS[by_id[identity]['task']]
                if data.get('choice') not in (spec['criteria'] if mode == 'label' else ACTIONS):
                    raise ValueError('无效判断')
                if not data.get('reviewer', '').strip() or not data.get('reason', '').strip():
                    raise ValueError('请填写复核人与依据')
                if data['choice'] == 'revision_verified' and not data.get('revision', '').strip():
                    raise ValueError('必须提供已复核的新版本定位')
                event = {'kind': mode, 'source': 'human', 'dataset_id': manifest['dataset_id'],
                    'case_id': identity, 'input_hash': manifest['cases'][identity], 'at': now(),
                    'reviewer': data['reviewer'], 'choice': data['choice'], 'reason': data['reason'],
                    'revision': data.get('revision'), 'view_wall_seconds': round(time.monotonic() - visit[1], 2),
                    'production_writeback': False}
                if mode == 'review':
                    event['run_id'] = read_json_run(run_folder)
                event['annotation_id'] = digest(event)
                append_event(annotations, event)
                following = cases[(cases.index(by_id[identity]) + 1) % len(cases)]['case_id']
                self.send_response(303)
                self.send_header('Location', '/?case=' + following + '&saved=1')
                self.end_headers()
            except (ValueError, UnicodeError) as exc:
                self.send(400, '<p class="error">' + escape(exc) + '</p><a href="/">返回</a>')

    server = HTTPServer(('127.0.0.1', port), Handler)
    return server


def read_json_run(folder):
    return json.loads((Path(folder) / 'protocol.json').read_text(encoding='utf-8'))['run_id']
