"""Loopback application: real save/command API, no static feedback façade."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit, parse_qs
import base64
import json
import secrets
import threading
import os
import signal
import fcntl
from markdown_it import MarkdownIt
from pydantic import ValidationError
from .models import Requirements, Settings, SaveRevision, Comment
from .runtime import Worker
from .store import Store, Conflict, dump
from . import sources


def make_server(workspace, port=8765):
    store=Store(workspace);worker=Worker(store);token=secrets.token_urlsafe(24)
    assets=files('briefloop').joinpath('static')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,format,*args): pass
        def send(self,status,data,content_type='application/json; charset=utf-8'):
            payload=data if isinstance(data,bytes) else dump(data).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.end_headers();self.wfile.write(payload)
        def error(self,exc):
            self.send(409 if isinstance(exc,Conflict) else 400,{'error':str(exc)})
        def do_GET(self):
            try:
                u=urlsplit(self.path);q=parse_qs(u.query)
                if u.path=='/api/state':self.send(200,store.snapshot())
                elif u.path=='/api/session':self.send(200,{'token':token})
                elif u.path=='/api/runtime':
                    proc=worker.runtime.process
                    self.send(200,{'worker_alive':worker.thread.is_alive(),'job_id':worker.current,'pid':proc.pid if proc else None,'returncode':proc.poll() if proc else None})
                elif u.path=='/api/source':
                    sid=q['id'][0];self.send(200,{'source':store.one('sources',sid),'text':store.source_text(sid)})
                elif u.path=='/api/events':
                    jid=q['job'][0];self.send(200,store.rows('SELECT * FROM events WHERE job_id=? ORDER BY seq',(jid,)))
                elif u.path=='/api/learning-details':
                    job=store.one('jobs',q['job'][0]);root=store.root/'jobs'/job['id']
                    rounds=[]
                    for f in sorted(root.glob('round-*/comparison/input.json')):
                        comparison=f.with_name('comparison.json')
                        rounds.append({'cases':json.loads(f.read_text()),'result':json.loads(comparison.read_text()) if comparison.exists() else None})
                    self.send(200,{'job':job,'rounds':rounds})
                elif u.path=='/api/download':
                    b=store.one('briefs',q['version'][0])
                    from .exports import reader_markdown,docx_bytes
                    md=reader_markdown(store,b)
                    if q.get('format',['md'])[0]=='docx':self.send(200,docx_bytes(md),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                    else:self.send(200,md.encode(),'text/markdown; charset=utf-8')
                elif u.path in ('/','/index.html'):
                    self.send(200,assets.joinpath('index.html').read_bytes(),'text/html; charset=utf-8')
                elif u.path in ('/app.js','/style.css'):
                    self.send(200,assets.joinpath(u.path[1:]).read_bytes(),'text/javascript' if u.path.endswith('.js') else 'text/css')
                else:self.send(404,{'error':'未找到页面'})
            except (ValueError,KeyError,OSError) as exc:self.error(exc)
        def do_POST(self):
            try:
                origin=self.headers.get('Origin')
                expected=f'http://127.0.0.1:{self.server.server_port}'
                if self.headers.get('X-BriefLoop-Token')!=token or origin and origin!=expected:
                    self.send(403,{'error':'页面会话已过期，请刷新后重试'});return
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<25*1024*1024:raise ValueError('请求为空或过大')
                body=json.loads(self.rfile.read(n));path=urlsplit(self.path).path
                if path=='/api/upload':
                    data=base64.b64decode(body['data'],validate=True)
                    result=sources.upload(store,body['name'],data)
                elif path=='/api/source-url':result=sources.fetch(store,body['url'])
                elif path=='/api/generate':
                    req=Requirements.model_validate(body['requirements'])
                    run=store.create_run(req.model_dump(),body.get('source_ids',[]))
                    result=store.enqueue('generate',{'run_id':run['id']})
                elif path=='/api/save':
                    value=SaveRevision.model_validate(body)
                    result=store.revise(value.base_version,value.markdown,value.editor_document)
                elif path=='/api/comment':
                    value=Comment.model_validate(body);result=store.comment(value.version_id,value.text)
                elif path=='/api/settings':
                    settings=Settings.model_validate({**store.settings(),**body})
                    store.set_meta('settings',settings.model_dump());result=settings.model_dump()
                elif path=='/api/assess':
                    store.one('briefs',body['version_id']);result=store.enqueue('assess',{'version_id':body['version_id']})
                elif path=='/api/learn':
                    from .learning import enqueue_feedback
                    result=enqueue_feedback(store)
                elif path=='/api/stop':worker.stop_job(body['job_id']);result={'ok':True}
                elif path=='/api/resume':worker.resume(body['job_id']);result={'ok':True}
                elif path=='/api/rollback':store.bind_skill(body.get('skill_id'));result={'ok':True}
                elif path=='/api/render':result={'html':MarkdownIt('commonmark',{'html':False}).enable('table').render(body['markdown'])}
                else:self.send(404,{'error':'未知操作'});return
                self.send(200,result)
            except (ValueError,KeyError,OSError,ValidationError) as exc:self.error(exc)
            except Exception as exc:
                self.send(500,{'error':str(exc)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    lock=(store.root/'.server.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();server.server_close();raise RuntimeError('这个工作区已有本地服务在运行')
    server.workspace_lock=lock
    server.store=store;server.worker=worker
    return server


def serve(workspace,port=8765):
    server=make_server(workspace,port)
    server.worker.start()
    url=f'http://127.0.0.1:{server.server_port}'
    (server.store.root/'server.json').write_text(dump({'pid':os.getpid(),'url':url}))
    print(f'BriefLoop: {url}',flush=True)
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.worker.close();server.server_close();server.workspace_lock.close()
