"""Loopback application: real save/command API, no static feedback façade."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit, parse_qs, quote
import base64
import json
import secrets
import os
import select
import signal
import sys
import threading
import time
from .platform_support import WorkspaceLock
from markdown_it import MarkdownIt
from pydantic import ValidationError
from .models import Requirements, Settings, SaveRevision, Comment
from .runtime import Worker
from .harness import HarnessManager
from .interactive_runtime import InteractiveRuntime
from .store import Store, Conflict, dump
from . import sources

MAX_REQUEST_BYTES=25*1024*1024
MAX_UPLOAD_BYTES=18*1024*1024
# Raw source uploads skip Base64/JSON copies, so PDFs (annual reports, scans) may be larger.
MAX_PDF_UPLOAD_BYTES=100*1024*1024
# Bound stalled sockets, not the total duration of a progressing large upload.
REQUEST_IDLE_TIMEOUT=30


class _RequestBodyError(ValueError):
    def __init__(self,status,code,message):
        super().__init__(message)
        self.status=status;self.code=code

def _upload_data(body):
    data=base64.b64decode(body['data'],validate=True)
    if len(data)>MAX_UPLOAD_BYTES:raise ValueError(f"{body.get('name','文件')} 超过单文件 18 MiB 限制，请压缩或拆分后重试")
    return data


def _service_status(server):
    from .chat_store import BUSY_SQL
    with server._admission:
        with server.store.tx() as connection:
            jobs=[dict(row) for row in connection.execute(
                "SELECT id,kind,status FROM jobs WHERE status IN ('queued','running') ORDER BY rowid")]
            sessions=[{'id':row['id'],'title':row['title'],'status':row['status'],
                       'backend':json.loads(row['runtime']).get('backend','codex'),'busy':True}
                      for row in connection.execute(
                          'SELECT s.id,s.title,s.status,s.runtime FROM chat_sessions s WHERE '+BUSY_SQL+' ORDER BY s.rowid')]
        return {'pid':os.getpid(),'workspace_id':server.store.meta('workspace_id'),
                'busy':bool(jobs or sessions or server._active_posts),'jobs':jobs,'sessions':sessions,
                'draining':server.draining}


def _close_service(server):
    """Attempt every owned cleanup even if an earlier transport fails."""
    operations=[('bridge:'+name,manager.close) for name,manager in server.bridge_harnesses.items()]
    operations.extend([('worker',server.worker.close),('harness',server.harness.close),
                       ('opencode',server.opencode_harness.close),('runtime_bridge',server.runtime_bridge.close),
                       ('server',server.server_close),('workspace_lock',server.workspace_lock.close)])
    errors=list(getattr(server,'shutdown_errors',[]))
    for name,close in operations:
        try:close()
        except BaseException as exc:
            errors.append({'component':name,'error_type':type(exc).__name__})
    return errors


def _begin_service_shutdown(server, *, cancel):
    """Stop admission, finish local writes, then cancel external work before drain."""
    with server._admission:
        if server.draining:return
        server.draining=True
        server.worker.opened_paused=True
        server.worker.stopping.set()
        # Incomplete bodies have not entered a write operation. Cancel their
        # receive loops without interrupting saves that already have a body.
        for handler in server._reading_posts:
            handler._body_interrupted=True
    def drain():
        with server._admission:
            while server._active_posts > server._active_connector_posts:
                server._admission.wait()
        if cancel:
            with server.worker._claim_lock:
                pending=_service_status(server)
                for job in pending['jobs']:
                    try:server.worker.stop_job(job['id'])
                    except Exception as exc:
                        server.shutdown_errors.append({'component':'job_cancel','error_type':type(exc).__name__})
            for session in pending['sessions']:
                try:server.select_harness(session_id=session['id']).cancel(session['id'])
                except Exception as exc:
                    server.shutdown_errors.append({'component':'session_cancel','error_type':type(exc).__name__})
        with server._admission:
            while server._active_posts:server._admission.wait()
        server.shutdown()
    threading.Thread(target=drain,name='briefloop-shutdown',daemon=True).start()


def _watch_desktop_owner(server, stream):
    """Only explicitly desktop-owned services stop on their private stdin EOF."""
    def watch():
        try:
            while stream.read(1):pass
        except (OSError, ValueError):pass
        _begin_service_shutdown(server,cancel=True)
    threading.Thread(target=watch,name='briefloop-desktop-owner',daemon=True).start()


def make_server(workspace, port=8765, *, paused=False, backend=None):
    lock=WorkspaceLock(workspace)
    try:return _make_server(workspace,port,paused=paused,backend=backend,lock=lock)
    except BaseException:
        lock.close();raise


def _make_server(workspace, port, *, paused, backend, lock):
    from .runtime_bridge import RuntimeBridge
    from .software_version import runtime_info
    software_identity=runtime_info()
    bridge=RuntimeBridge()
    try:
        store=Store(workspace)
        if backend is not None:
            store.update_settings({'agent_backend':backend})
    except BaseException:
        lock.close();raise
    harness=HarnessManager(store)
    # Recover stale chat state once, at service start. Notifications and normal
    # writes must never run this global recovery.
    harness.chat.recover_stale()
    from .opencode_harness import OpencodeHarness
    from .backends import validate_backend
    opencode_harness=OpencodeHarness(store)
    worker=Worker(store)
    from .bridge_harness import BridgeHarness
    from .backends import BRIDGE_BACKENDS
    bridge_harnesses={name:BridgeHarness(store,bridge,name) for name in BRIDGE_BACKENDS}
    from .native_engine import NativeEngine
    from .native_harness import NativeHarness
    native_engine=NativeEngine()
    native_harness=NativeHarness(store,native_engine)
    managers={'codex':harness,'opencode':opencode_harness,'briefloop-native':native_harness,**bridge_harnesses}
    from .chat_dispatch import ChatDispatcher
    chat_dispatch=ChatDispatcher(managers)
    chat_dispatch.recover()
    worker.runtime=InteractiveRuntime(store,backends=managers)
    worker.opened_paused=paused
    def pick_harness(runtime=None,session_id=None,*,sending=False):
        return chat_dispatch.select(runtime,session_id,sending=sending)
    def choose_runtime(store_,runtime):
        # A chat turn carries the runtime the user just picked; treat it as the choice.
        try:store_.confirm_runtime_choice((runtime or {}).get('backend'),runtime or {})
        except ValueError:pass
    def test_runtime(body):
        backend=validate_backend(body.get('backend'))
        model=str(body.get('model','')).strip()
        if not model:raise ValueError('请先选择测试模型')
        manager=managers[backend]
        root=store.root/'runtime-tests'/secrets.token_hex(8);root.mkdir(parents=True)
        runtime={'backend':backend,'model':model,'permission':'read-only' if backend in ('codex','opencode','briefloop-native') else 'runtime-native'}
        session=manager.create_session(backend+' · 连接测试',runtime,root)
        manager.chat.event(session['id'],'runtime/test',{'backend':backend,'model':model,'kind':'short_model_call'})
        message=manager.send(session['id'],'Reply with OK only. Do not use tools.',runtime=runtime,allow_web=False)
        return {'session_id':session['id'],'message_id':message['id'],'status':'submitted'}

    token=secrets.token_urlsafe(24)
    assets=files('briefloop').joinpath('static')
    # Serve one UI/backend version for this process; builds must not replace a live UI halfway.
    asset_bytes={name:assets.joinpath(name).read_bytes() for name in ('index.html','app.js','style.css','tokens.css')}
    icon_names={p.name for p in assets.iterdir() if p.name.startswith('runtime-') and p.name.endswith(('.svg','.png'))}
    asset_bytes.update({name:assets.joinpath(name).read_bytes() for name in icon_names})
    class Handler(BaseHTTPRequestHandler):
        timeout=REQUEST_IDLE_TIMEOUT
        def log_message(self,format,*args): pass
        def parse_request(self):
            if not super().parse_request():return False
            # Loopback binding alone does not prevent a foreign DNS name from
            # reaching this service. Validate authority before any route can
            # expose workspace data or the browser session token.
            expected=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get_all('Host',[]) != [expected]:
                self.close_connection=True
                self.send(403,{'error':'请通过 http://'+expected+' 打开本地工作区'})
                return False
            return True
        def send(self,status,data,content_type='application/json; charset=utf-8',download_name=None):
            payload=data if isinstance(data,bytes) else dump(data).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            if download_name:self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(download_name))
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.end_headers();self.wfile.write(payload)
        def error(self,exc):
            # Structured rejections carry a stable code (e.g. fact-check needs the
            # web grant) so clients beyond our own frontend can branch on it.
            code=getattr(exc,'code',None)
            self.send(409 if isinstance(exc,Conflict) else 400,{'error':str(exc),**({'code':code} if isinstance(code,str) and code else {})})
        def do_GET(self):
            try:
                u=urlsplit(self.path);q=parse_qs(u.query)
                public=u.path in ('/','/index.html','/app.js','/style.css','/tokens.css') or u.path[1:] in icon_names
                if not public:
                    # Browser origin protection, not authentication of local
                    # processes. Native clients and address-bar downloads omit
                    # Origin/Fetch Metadata; same-origin links need no token URL.
                    origins=self.headers.get_all('Origin',[])
                    sites=self.headers.get_all('Sec-Fetch-Site',[])
                    expected=f'http://127.0.0.1:{self.server.server_port}'
                    if (origins and origins!=[expected]) or (sites and sites not in (['same-origin'],['none'])):
                        self.send(403,{'error':'请从本地工作区页面查看或下载文件','code':'cross_origin_read_denied'});return
                if u.path=='/api/state':
                    snapshot=store.snapshot()
                    snapshot['demo']=store.meta('demo')
                    for source in snapshot['sources']:
                        sidecar=store.root/'sources'/(source['id']+'.provenance.json')
                        if sidecar.is_file():
                            try:
                                meta=json.loads(sidecar.read_text())
                                source['media_type']=meta.get('media_type');source['needs_visual']=bool(meta.get('needs_visual',False))
                            except (ValueError,OSError):pass
                    self.send(200,snapshot)
                elif u.path=='/api/brief':
                    self.send(200,store.brief_view(q['id'][0]))
                elif u.path=='/api/report-search':
                    self.send(200,{'run_ids':store.search_briefs(q.get('q',[''])[0])})
                elif u.path=='/api/software-version':
                    self.send(200,software_identity)
                elif u.path=='/api/workspaces':
                    from .workspaces import list_workspaces
                    self.send(200,list_workspaces(store))
                elif u.path=='/api/harness/sessions':self.send(200,{'sessions':harness.list_sessions(q.get('view',['active'])[0])})
                elif u.path=='/api/harness/session':
                    selected=pick_harness(session_id=q['id'][0])
                    if q.get('requests_only',['0'])[0]=='1':
                        selected.chat.session(q['id'][0])
                        pending=store.rows("SELECT id,session_id,data,status,created FROM chat_requests WHERE session_id=? AND status='pending' ORDER BY created,rowid",(q['id'][0],))
                        self.send(200,{'requests':[selected.chat.decode(r) for r in pending]})
                    else:self.send(200,selected.snapshot(q['id'][0],int(q.get('after',['0'])[0]),reasoning=q.get('reasoning',['0'])[0]=='1'))
                elif u.path=='/api/external/capabilities':
                    from .external_requests import capabilities
                    self.send(200,capabilities())
                elif u.path=='/api/session':self.send(200,{'token':token,'upload_limits':{'max_file_bytes':MAX_UPLOAD_BYTES,'max_request_bytes':MAX_REQUEST_BYTES,'max_pdf_bytes':MAX_PDF_UPLOAD_BYTES}})
                elif u.path=='/api/service-status':self.send(200,_service_status(self.server))
                elif u.path=='/api/connectors':self.send(200,{'connectors':self.server.connectors.list()})
                elif u.path=='/api/runtime':
                    with worker._claim_lock:
                        active=dict(worker._generation_jobs)
                        reviews={jid:item[1] for jid,item in worker._review_jobs.items()}
                    selected=q.get('job_id',[None])[0]
                    observed=active[selected][1] if selected in active else reviews[selected] if selected in reviews else (next(iter(reviews.values())) if reviews and not worker.current else worker.runtime)
                    if selected and selected not in active and selected not in reviews and selected!=worker.current:observed=None
                    proc=observed.process if observed else None
                    self.send(200,{'server_pid':os.getpid(),'worker_alive':worker.thread.is_alive(),'automatic_learning_paused':worker.opened_paused,'paused':worker.opened_paused,'job_id':selected if selected in active or selected in reviews else worker.current,'generation_job_ids':list(active),'pid':proc.pid if proc else None,'returncode':proc.poll() if proc else None})
                elif u.path=='/api/source':
                    from .projections import source_details
                    sid=q['id'][0];source,provenance,original=source_details(store,sid)
                    from .media import source_attachment
                    attachment=source_attachment(store,sid) if source['status']!='failed' else {'status':'failed','error':source.get('error'),'image_path':None}
                    self.send(200,{'source':source,'text':store.source_text(sid),'provenance':provenance,'attachment':attachment,
                        'image_url':'/api/source-image?id='+sid if attachment.get('image_path') and source['status']=='ready' else None,
                        'original_url':'/api/source-original?id='+sid if original else None})
                elif u.path=='/api/source-image':
                    from pathlib import Path
                    from .media import source_attachment,rendered_page_path
                    sid=q['id'][0];attachment=source_attachment(store,sid)
                    if attachment.get('status')!='ready':raise ValueError(attachment.get('error') or '来源不可读取')
                    page=int(q['page'][0]) if q.get('page') else None
                    path=rendered_page_path(store,sid,page) if page is not None else attachment.get('image_path')
                    if not path:raise ValueError('尚无图片页面，请先选择 PDF 页码并点击查看页面')
                    self.send(200,Path(path).read_bytes(),'image/png')
                elif u.path=='/api/source-original':
                    from .projections import source_details
                    source,provenance,original=source_details(store,q['id'][0])
                    if original is None:raise ValueError('该来源未保留原件')
                    self.send(200,original.read_bytes(),'application/octet-stream',download_name=original.name)
                elif u.path=='/api/figure':
                    from .figure_support import export_figures
                    brief=store.one('briefs',q['version'][0]);figures=export_figures(store,brief)
                    if q['id'][0] not in figures:raise ValueError('这张图未引用在该稿件中')
                    self.send(200,figures[q['id'][0]]['image_bytes'],'image/png')
                elif u.path=='/api/report-data-template':
                    from .industry_data import report_data_template
                    self.send(200,report_data_template(),download_name='industry-report-data.json')
                elif u.path=='/api/report-data-schema':
                    from .industry_data import IndustryData
                    self.send(200,IndustryData.model_json_schema())
                elif u.path=='/api/report-data':
                    from .report_tools import report_details
                    self.send(200,report_details(store,store.one('briefs',q['version'][0])))
                elif u.path=='/api/search-activity':
                    from .search_policy import activity
                    self.send(200,activity(store,q['run'][0]))
                elif u.path=='/api/research-budget':
                    from .research_budget import snapshot
                    self.send(200,snapshot(store,q['run'][0]))
                elif u.path=='/api/learning-candidates':
                    from .projections import learning_candidates
                    self.send(200,learning_candidates(store))
                elif u.path=='/api/zhipu-search':
                    from .zhipu import key_status
                    self.send(200,key_status())
                elif u.path=='/api/bocha':
                    from .bocha import key_status
                    self.send(200,key_status())
                elif u.path=='/api/tavily':
                    from .tavily import key_status
                    self.send(200,key_status())
                elif u.path=='/api/opencode/providers':
                    self.send(200,{'configurations':opencode_harness._client().provider_settings()})
                elif u.path=='/api/runtime/reasoning':
                    from .runtime_reasoning import options
                    self.send(200,options(q.get('backend',['codex'])[0],q.get('model',['default'])[0],store.root,bridge,native=native_harness))
                elif u.path=='/api/runtime/permissions':
                    from .runtime_permissions import catalog
                    self.send(200,catalog(q.get('backend',['codex'])[0],store.root,bridge))
                elif u.path=='/api/native/providers':
                    from .native_providers import configurations
                    self.send(200,{'configurations':configurations()})
                elif u.path=='/api/runtimes':
                    result=bridge.discover()
                    from .native_engine import discovery
                    result['runtimes'].insert(0,discovery())
                    self.send(200,result)
                elif u.path=='/api/runtime/fast-capability':
                    from .backends import validate_backend
                    backend=validate_backend(q.get('backend',[store.settings().get('agent_backend','codex')])[0])
                    runtime={'backend':backend,'model':q.get('model',[''])[0],
                             'model_provider':q.get('model_provider',[''])[0] or None}
                    if backend=='codex':
                        self.send(200,harness.fast_capability(runtime))
                    else:
                        self.send(200,{**runtime,'official_connection':False,'fast_supported':False,
                                       'account_availability':'unknown','enabled':False,
                                       'reason':'当前执行引擎尚未提供可核验的 Fast 能力'})
                elif u.path=='/api/models':
                    from .backends import validate_backend
                    backend=validate_backend(q.get('backend',[store.settings().get('agent_backend','codex')])[0])
                    if backend=='briefloop-native':
                        models=native_harness.list_models(refresh=q.get('refresh',[''])[0]=='1')
                    elif backend=='opencode':
                        models=opencode_harness.list_models(refresh=q.get('refresh',[''])[0]=='1')
                    elif backend in bridge_harnesses:
                        catalog=bridge_harnesses[backend].list_models(refresh=q.get('refresh',[''])[0]=='1')
                        self.send(200,{'backend':backend,**catalog});return
                    else:
                        catalog=bridge.call('list_models',{'runtime_id':'codex','cwd':str(store.root)})
                        self.send(200,{'backend':backend,**catalog});return
                    self.send(200,{'backend':backend,'count':len(models),'models':models})
                elif u.path=='/api/task-progress':
                    from .task_progress import summary
                    self.send(200,summary(store,q['job'][0]))
                elif u.path=='/api/events':
                    jid=q['job'][0];self.send(200,store.rows('SELECT * FROM events WHERE job_id=? ORDER BY seq',(jid,)))
                elif u.path=='/api/learning-details':
                    job=store.one('jobs',q['job'][0]);root=store.root/'jobs'/job['id']
                    rounds=[]
                    for f in sorted(root.glob('round-*/comparison/input.json')):
                        comparison=f.with_name('comparison.json')
                        from .exports import reader_markdown
                        cases=json.loads(f.read_text())
                        for case in cases:
                            for side in ('baseline','candidate'):
                                case[side]['reader_markdown']=reader_markdown(store,case[side])
                                grades=store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1',(case[side]['id'],))
                                case[side]['assessment']=json.loads(grades[0]['data']) if grades else None
                        rounds.append({'cases':cases,'result':json.loads(comparison.read_text()) if comparison.exists() else None})
                    self.send(200,{'job':job,'rounds':rounds})
                elif u.path=='/api/company-context':
                    from .company_context import snapshot
                    self.send(200,snapshot(store))
                elif u.path=='/api/research-notes':
                    from .deliverable_spec import research_record
                    self.send(200,research_record(store,store.one('briefs',q['version'][0])),download_name='research-notes.json' if q.get('download') else None)
                elif u.path=='/api/export-status':
                    job=store.one('jobs',q['job'][0])
                    if job['kind']!='export_docx':raise ValueError('不是导出任务')
                    self.send(200,job)
                elif u.path=='/api/export-file':
                    if q.get('workspace_id',[store.meta('workspace_id')])[0]!=store.meta('workspace_id'):
                        raise Conflict('工作区身份已变化，未下载文件')
                    from .export_jobs import output_path
                    job=store.one('jobs',q['job'][0])
                    if job['status']!='complete':raise ValueError('Word 尚未制作完成')
                    data=output_path(store,job).read_bytes()
                    import hashlib
                    if hashlib.sha256(data).hexdigest()!=json.loads(job['result'])['sha256']:raise ValueError('Word 文件已变化，请重新生成')
                    brief=store.one('briefs',json.loads(job['payload'])['version_id'])
                    title=json.loads(brief['detail']).get('title') or '报告'
                    import re
                    name=re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', title).strip('. ')[:120] or '报告'
                    self.send(200,data,'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_name=name+'.docx')
                elif u.path=='/api/release-state':
                    from .release import eligibility,list_releases
                    version=q['version'][0];brief=store.one('briefs',version)
                    checked=eligibility(store,version);checked.pop('input',None)
                    public=[]
                    for row in list_releases(store,brief['run_id']):
                        public.append({**{k:row.get(k) for k in ('id','version_id','status','job_id','previous_id','change_type','change_reason','created','result')},
                                       'sources':[{'id':s['id'],'name':s['name']} for s in row['data']['snapshot']['sources']]})
                    self.send(200,{'eligibility':checked,'releases':public})
                elif u.path=='/api/release-file':
                    from .release import release_file
                    self.send(200,release_file(store,q['id'][0]).read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_name='formal-report.docx')
                elif u.path=='/api/audit-file':
                    from .audit_bundle import bundle_file
                    self.send(200,bundle_file(store,q['job'][0]).read_bytes(),'application/zip',download_name='report-audit.zip')
                elif u.path=='/api/source-update-state':
                    from .source_updates import for_version
                    self.send(200,for_version(store,q['version'][0]))
                elif u.path=='/api/review-status':
                    from .review import review_status
                    self.send(200,review_status(store,q['version'][0]))
                elif u.path=='/api/fact-checks':
                    from .fact_check import view as fact_check_view
                    self.send(200,fact_check_view(store,q['version'][0]))
                elif u.path=='/api/evidence':
                    from .evidence import inspect_bindings
                    self.send(200,inspect_bindings(store,q['version'][0]))
                elif u.path=='/api/version-checks':
                    from .delivery_checks import brief_checks
                    self.send(200,brief_checks(store,q['version'][0]))
                elif u.path=='/api/download':
                    b=store.one('briefs',q['version'][0])
                    from .exports import reader_markdown,docx_bytes
                    md=reader_markdown(store,b)
                    if q.get('format',['md'])[0]=='bundle':
                        from .figure_support import markdown_bundle
                        self.send(200,markdown_bundle(store,b),'application/zip',download_name='report-with-figures.zip')
                    elif q.get('format',['md'])[0]=='docx':
                        from .figure_support import export_figures
                        req=json.loads(store.one('runs',b['run_id'])['requirements']);detail=json.loads(b['detail'])
                        report_data=detail.get('report_data')
                        if report_data and not detail.get('report_data_needs_review'):
                            report_data={**report_data,'records':[{**row,'source_label':store.one('sources',row['source_id'])['name']} for row in report_data['records']]}
                        elif detail.get('report_data_needs_review'):report_data=None
                        self.send(200,docx_bytes(md,report_profile=req.get('report_profile','brief'),title=detail.get('title',req.get('title','')),language=req.get('language'),
                            report_date=req.get('report_date',''),organization=req.get('organization',''),industry=req.get('industry',''),
                            period=req.get('period',''),report_data=report_data,figures=export_figures(store,b),
                            document=json.loads(b['editor_document']) if b.get('editor_document') else None,
                            source_records={sid:store.one('sources',sid) for sid in store.source_ids(b['run_id'])}),
                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_name='report.docx')
                    else:self.send(200,md.encode(),'text/markdown; charset=utf-8')
                elif u.path in ('/','/index.html'):
                    self.send(200,asset_bytes['index.html'],'text/html; charset=utf-8')
                elif u.path[1:] in icon_names:
                    self.send(200,asset_bytes[u.path[1:]],'image/png' if u.path.endswith('.png') else 'image/svg+xml')
                elif u.path in ('/app.js','/style.css','/tokens.css'):
                    self.send(200,asset_bytes[u.path[1:]],'text/javascript' if u.path.endswith('.js') else 'text/css')
                else:self.send(404,{'error':'未找到页面'})
            except (ValueError,KeyError,OSError,RuntimeError) as exc:self.error(exc)
        def _read_body(self,n,*,upload=False):
            self._body_interrupted=False
            with self.server._admission:
                if self.server.draining and not self._control_post:
                    raise _RequestBodyError(503,'service_draining','服务正在退出，请重新打开工作区后重试')
                self.server._reading_posts.add(self)
            timeout=self.connection.gettimeout()
            try:
                # Drain the HTTP parser's buffered bytes before waiting on the
                # socket. Nonblocking read1 avoids poisoning BufferedReader on
                # a timeout, and polling also works on Windows where shutdown
                # from another thread need not wake a timed socket read.
                self.connection.setblocking(False)
                chunks=[];remaining=n;deadline=time.monotonic()+self.timeout
                while remaining:
                    if self._body_interrupted:
                        raise _RequestBodyError(503,'service_draining','服务正在退出，未完成的请求已取消')
                    chunk=self.rfile.read1(min(remaining,65536))
                    if not chunk:
                        idle=deadline-time.monotonic()
                        if idle<=0:raise _RequestBodyError(408,'request_timeout','接收请求超时，请重试')
                        if not select.select([self.connection],[],[],min(.25,idle))[0]:continue
                        chunk=self.rfile.read1(min(remaining,65536))
                        if not chunk:break
                    chunks.append(chunk);remaining-=len(chunk)
                    deadline=time.monotonic()+self.timeout
            finally:
                self.connection.settimeout(timeout)
                with self.server._admission:
                    self.server._reading_posts.discard(self)
            if self._body_interrupted:
                raise _RequestBodyError(503,'service_draining','服务正在退出，未完成的请求已取消')
            if remaining:
                raise _RequestBodyError(400,'invalid_upload' if upload else 'incomplete_request','请求未接收完整，请重试')
            return b''.join(chunks)

        def _upload_file(self):
            # One source file as the raw request body; the name travels in the query.
            name=os.path.basename(parse_qs(urlsplit(self.path).query).get('name',[''])[0].replace('\\','/'))
            limit=MAX_PDF_UPLOAD_BYTES if name.lower().endswith('.pdf') else MAX_UPLOAD_BYTES
            try:n=int(self.headers.get('Content-Length',''))
            except ValueError:n=-1
            if not name or len(name)>255 or not 0<n<=limit:
                self.close_connection=True
                self.send(413 if name and n>limit else 400,{'error':(f'{name} 超过单文件 {limit//1048576} MiB 限制，请压缩或拆分后重试' if name and n>limit
                                                                     else '上传请求缺少文件名或文件为空'),'code':'request_too_large' if name and n>limit else 'invalid_upload'})
                return
            data=self._read_body(n,upload=True)
            self.send(200,sources.upload(store,name,data))
        def do_POST(self):
            path=urlsplit(self.path).path
            control=path in ('/api/service-stop','/api/stop','/api/harness/cancel','/api/connectors/task-revoke')
            self._control_post=control
            with self.server._admission:
                if self.server.draining and not control:
                    self.send(503,{'error':'服务正在退出，不能接受新操作。','code':'service_draining'});return
                if not control:
                    self.server._active_posts+=1
                    if path=='/api/connectors/task-tool':self.server._active_connector_posts+=1
            try:self._post_admitted()
            finally:
                if not control:
                    with self.server._admission:
                        self.server._active_posts-=1
                        if path=='/api/connectors/task-tool':self.server._active_connector_posts-=1
                        self.server._admission.notify_all()

        def _post_admitted(self):
            try:
                if urlsplit(self.path).path == '/api/connectors/task-tool':
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<1024*1024:raise ValueError('请求为空或过大')
                    authorization=self.headers.get('Authorization','')
                    access=authorization[7:] if authorization.startswith('Bearer ') else ''
                    self.send(200,self.server.connector_tasks.dispatch(access,json.loads(self._read_body(n))))
                    return
                origin=self.headers.get('Origin')
                expected=f'http://127.0.0.1:{self.server.server_port}'
                if self.headers.get('X-BriefLoop-Token')!=token or origin and origin!=expected:
                    self.send(403,{'error':'页面会话已过期，请刷新后重试'});return
                if urlsplit(self.path).path=='/api/upload-file':
                    self._upload_file();return
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<MAX_REQUEST_BYTES:
                    self.close_connection=True
                    self.send(413,{'error':'请求为空或超过 25 MiB（含 Base64 与 JSON）；单文件上限 18 MiB，请压缩、拆分文件后重试','code':'request_too_large'});return
                body=json.loads(self._read_body(n));path=urlsplit(self.path).path
                if path=='/api/software-update-check':
                    from .software_version import check_update
                    result=check_update(software_identity)
                    if result.get('state')=='available':
                        from .notifications import version_available
                        version_available(store,software_identity['version'],result['releaseVersion'])
                elif path=='/api/notifications/read':
                    from .notifications import mark_read
                    result=mark_read(store,body.get('through'),body.get('category'),body.get('seq'))
                elif path=='/api/notifications/version':
                    from .notifications import version_available,snapshot
                    version_available(store,body.get('current'),body.get('latest'));result=snapshot(store)
                elif path=='/api/service-stop':
                    if body.get('pid')!=os.getpid() or body.get('workspace_id')!=store.meta('workspace_id'):
                        raise ValueError('服务身份已变化，未执行停止')
                    with self.server._admission:
                        if self.server.draining:
                            self.send(200,{'stopping':True});return
                        state=_service_status(self.server)
                        if state['busy'] and body.get('busy_action')!='cancel':
                            self.send(409,{'error':'服务仍有任务或对话，请等待完成或明确停止后退出。',
                                           'code':'service_busy',**state});return
                        _begin_service_shutdown(self.server,cancel=body.get('busy_action')=='cancel')
                    self.send(200,{'stopping':True})
                    return
                elif path=='/api/zhipu-search':
                    from .zhipu import save_key,delete_key
                    result=delete_key() if body.get('remove') else save_key(body['api_key'])
                elif path=='/api/bocha':
                    from .bocha import save_key,delete_key
                    result=delete_key() if body.get('remove') else save_key(body['api_key'])
                elif path=='/api/tavily':
                    from .tavily import save_key,delete_key
                    result=delete_key() if body.get('remove') else save_key(body['api_key'])
                elif path=='/api/connectors/task-bind':
                    result=self.server.connector_tasks.bind(body['job_id'],body['selections'],max_calls=body['max_calls'],max_total_bytes=body['max_total_bytes'])
                elif path in ('/api/connectors/task-status','/api/connectors/task-access','/api/connectors/task-revoke'):
                    result=getattr(self.server.connector_tasks,path.rsplit('-',1)[-1])(body['job_id'])
                elif path=='/api/connectors/save':
                    result=self.server.connectors.save(body['config'],connector_id=body.get('connector_id'),secrets=body.get('secrets'))
                elif path in ('/api/connectors/test','/api/connectors/enable','/api/connectors/disable','/api/connectors/delete'):
                    result=getattr(self.server.connectors,path.rsplit('/',1)[-1])(body['connector_id'])
                elif path=='/api/runtime/permissions':
                    if body.get('backend')!='antigravity':raise ValueError('此宿主不使用文件规则管理')
                    if store.rows("SELECT id FROM chat_messages WHERE status IN ('queued','sending','delivered','streaming') LIMIT 1") or store.rows("SELECT id FROM jobs WHERE status IN ('queued','running') LIMIT 1"):
                        raise ValueError('请等待本工作区任务结束后再修改原生规则')
                    from .runtime_permissions import change_antigravity
                    result=change_antigravity(body)
                elif path=='/api/runtime-test':
                    result=test_runtime(body)
                elif path=='/api/opencode/provider-catalog':
                    result=opencode_harness._client().probe_provider_catalog(str(body.get('provider','')))
                elif path=='/api/opencode/provider-test':
                    result=opencode_harness.test_provider_model(body)
                elif path=='/api/opencode/provider':
                    result=opencode_harness.configure_provider(body)
                elif path=='/api/workspaces/open':
                    from .workspaces import open_workspace
                    result=open_workspace(store,body['path'],create=bool(body.get('create',False)))
                elif path=='/api/workspaces/stop':
                    from .workspaces import stop_workspace
                    result=stop_workspace(store,body['path'])
                elif path=='/api/harness/session':
                    choose_runtime(store,body.get('runtime'))
                    result=pick_harness(body.get('runtime')).create_session(body.get('title','新对话'),body.get('runtime'))
                elif path.startswith('/api/schedules/'):
                    from . import schedules
                    command=path.rsplit('/',1)[-1]
                    if command=='preview':
                        _,config=schedules.validate(store,body)
                        result={'next_at':schedules.stamp(schedules.next_time(config,schedules.clock()))}
                    elif command=='save':result=schedules.save(store,body)
                    elif command=='change':result=schedules.change(store,body)
                    elif command=='run':result=schedules.fire(store,body['id'],manual=True,request_id=body.get('request_id'))
                    else:raise ValueError('未知定时报告操作')
                elif path=='/api/external/action':
                    from .external_requests import dispatch
                    result=dispatch(store,body)
                elif path=='/api/harness/message':
                    choose_runtime(store,body.get('runtime'))
                    result=pick_harness(body.get('runtime'),body['session_id'],sending=True).send(body['session_id'],body.get('text',''),mode=body.get('mode','queue'),source_ids=body.get('source_ids'),runtime=body.get('runtime'),message_id=body.get('message_id'),display_text=body.get('display_text'),allow_web=bool(body.get('allow_web',store.settings().get('chat_allow_web',True))))
                elif path=='/api/harness/answer':result=pick_harness(session_id=body['session_id']).answer(body['session_id'],body['request_id'],body['answers'])
                elif path=='/api/harness/archive':result=pick_harness(session_id=body['session_id']).archive(body['session_id'])
                elif path=='/api/harness/delete':result=pick_harness(session_id=body['session_id']).delete(body['session_id'])
                elif path=='/api/harness/restore':result=pick_harness(session_id=body['session_id']).restore(body['session_id'])
                elif path=='/api/harness/archive-completed':result={'count':sum(manager.archive_completed(name)['count'] for name,manager in managers.items())}
                elif path=='/api/harness/cancel':result=pick_harness(session_id=body['session_id']).cancel(body['session_id'])
                elif path=='/api/upload':
                    data=_upload_data(body)
                    result=sources.upload(store,body['name'],data)
                elif path=='/api/source-url':result=sources.fetch(store,body['url'],allow_private=True)
                elif path=='/api/retry-source':result=sources.retry_source(store,body['source_id'])
                elif path=='/api/source-pages':
                    from .media import render_source_pages
                    result=render_source_pages(store,body['source_id'],body['pages'])
                    for page in result['pages']:
                        page['url']='/api/source-image?id='+body['source_id']+'&page='+str(page['page'])
                elif path=='/api/report-data/prepare':
                    from .report_tools import prepare_for_run
                    result=prepare_for_run(store,body['run_id'],body['data'])
                elif path=='/api/import-revision':
                    from .word_import import import_revision
                    result=import_revision(store,body['base_version'],body.get('name','revision.docx'),
                        _upload_data(body) if body.get('data') else b'',
                        accept_unaligned=bool(body.get('accept_unaligned',False)),source_id=body.get('source_id'))
                elif path=='/api/company-resolve':
                    from .company_context import resolve_conflict
                    result=resolve_conflict(store,body['fact_id'],body['accept'])
                elif path=='/api/fact-check-grant':
                    # 用户明确追加核查预算：并入阶段计量限额；阶段以预算耗尽收束后追加重开并继续核查。
                    from .fact_check import grant as grant_fact_check
                    result=grant_fact_check(store,body['version_id'],body.get('limits'))
                elif path=='/api/template-import':
                    from .templates import import_template
                    result=import_template(store,body['name'],_upload_data(body),body.get('parent_id'))
                elif path=='/api/reports/delete':result=store.delete_report(body['version_id'])
                elif path=='/api/export':
                    from .export_jobs import enqueue_export
                    result=enqueue_export(store,body['version_id'],body.get('template_id'))
                elif path=='/api/demo':
                    from .demo import create_demo
                    result=create_demo(store)
                elif path=='/api/report-time-preview':
                    from .report_time import freeze
                    result=freeze(Requirements.model_validate(body['requirements']).model_dump())
                elif path=='/api/generate':
                    req=Requirements.model_validate(body['requirements'])
                    if 'connector_selection' in body:
                        result=self.server.connector_tasks.enqueue(req.model_dump(),body.get('source_ids',[]),body['connector_selection'],session_id=body.get('session_id'))
                    else:
                        run=store.create_run(req.model_dump(),body.get('source_ids',[]),research_protocol='quality_v1')
                        payload={'run_id':run['id']}
                        if body.get('session_id'):payload['session_id']=body['session_id']
                        result=store.enqueue('generate',payload)
                elif path=='/api/save':
                    value=SaveRevision.model_validate(body)
                    result=store.brief_view(store.revise(value.base_version,value.markdown,value.editor_document,allow_markdown_conversion=value.allow_markdown_conversion)['id'])
                elif path=='/api/comment':
                    value=Comment.model_validate(body);result=store.comment(value.version_id,value.text,learning_intent=value.learning_intent)
                elif path=='/api/native/provider':
                    from .native_providers import save
                    result=save(body)
                elif path=='/api/native/provider-catalog':
                    from .native_providers import catalog
                    result=catalog(body)
                elif path=='/api/settings':
                    result=store.update_settings(body)
                    if 'auto_learn' in body:worker.opened_paused=False
                elif path=='/api/release':
                    from .release import enqueue_release
                    result=enqueue_release(store,body['version_id'],previous_id=body.get('previous_id'),change_type=body.get('change_type'),change_reason=body.get('change_reason',''))
                elif path=='/api/audit-bundle':
                    from .audit_bundle import enqueue_bundle
                    result=enqueue_bundle(store,body['release_id'],body.get('source_permissions',{}))
                elif path=='/api/source-refresh':
                    brief=store.one('briefs',body['version_id'])
                    if body['source_id'] not in store.source_ids(brief['run_id']):raise ValueError('来源不属于本轮报告')
                    result=store.enqueue('source_refresh',{'run_id':brief['run_id'],'version_id':brief['id'],'source_id':body['source_id'],'information_cutoff':body['information_cutoff'],'requested_by':'user'})
                elif path=='/api/revise-findings':
                    store.one('briefs',body['version_id']);result=store.enqueue('revise',{'version_id':body['version_id']})
                elif path=='/api/review':
                    from .review import enqueue_review
                    result=enqueue_review(store,body['version_id'])
                elif path=='/api/review-response':
                    from .review import respond
                    result=respond(store,body['finding_id'],body['version_id'],body['action'],body['reason'])
                elif path=='/api/assess':
                    store.one('briefs',body['version_id']);payload={'version_id':body['version_id']}
                    if body.get('session_id'):payload['session_id']=body['session_id']
                    result=store.enqueue('assess',payload)
                elif path=='/api/learn':
                    from .learning import enqueue_feedback
                    # The confirmation names the plan the user saw, so a settings
                    # change in another window cannot enlarge this batch (#727).
                    result=enqueue_feedback(store,confirmed_plan=body.get('confirm_plan'))
                elif path=='/api/stop':worker.stop_job(body['job_id']);result={'ok':True}
                elif path=='/api/resume':result=worker.retry_with_current_model(body['job_id']) if body.get('use_current_model') is True else worker.resume(body['job_id'])
                elif path=='/api/task-dismiss':
                    job=store.one('jobs',body['job_id'])
                    if job['status'] not in ('failed','interrupted','cancelled'):raise ValueError('只有已结束且未完成的任务可以清除')
                    store.update_job(job['id'],'dismissed',error=job['error']);result={'ok':True}
                elif path=='/api/rollback':store.bind_skill(body.get('skill_id'));result={'ok':True}
                elif path=='/api/render':result={'html':MarkdownIt('commonmark',{'html':False}).enable('table').render(body['markdown'])}
                else:self.send(404,{'error':'未知操作'});return
                self.send(200,result)
            except _RequestBodyError as exc:
                self.close_connection=True
                self.send(exc.status,{'error':str(exc),'code':exc.code})
            except (ValueError,KeyError,OSError,ValidationError) as exc:self.error(exc)
            except Exception as exc:
                self.send(500,{'error':str(exc)})
    try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    except OSError:
        harness.close();opencode_harness.close();native_harness.close();lock.close();raise
    server.daemon_threads=True
    server._admission=threading.Condition(threading.RLock())
    server._active_posts=0
    server._active_connector_posts=0
    server._reading_posts=set()
    server.draining=False
    server.shutdown_errors=[]
    from .connectors import ConnectorService
    try:
        server.connectors=ConnectorService(store.root)
        from .connectors.tasks import TaskMaterials
        server.connector_tasks=TaskMaterials(store,server.connectors)
        worker.connector_tasks=server.connector_tasks
        native_harness.connector_tasks=server.connector_tasks
        worker.connector_tool_url=f'http://127.0.0.1:{server.server_port}/api/connectors/task-tool'
    except Exception:
        server.server_close();harness.close();opencode_harness.close();native_harness.close();bridge.close();lock.close();raise
    close_socket=server.server_close
    def close_server():
        try:server.connectors.close()
        finally:close_socket()
    server.server_close=close_server
    server.workspace_lock=lock;server.runtime_bridge=bridge;server.bridge_harnesses=bridge_harnesses
    server.native_engine=native_engine;server.native_harness=native_harness
    server.store=store;server.worker=worker;server.harness=harness;server.opencode_harness=opencode_harness
    server.select_harness=pick_harness
    return server


def serve(workspace,port=8765,*,paused=False,backend=None):
    launch_id=os.environ.pop('BRIEFLOOP_LAUNCH_ID',None)
    desktop_owner=os.environ.pop('BRIEFLOOP_DESKTOP_OWNER_PIPE','')=='1'
    server=make_server(workspace,port,paused=paused,backend=backend)
    from .templates import import_builtin
    import_builtin(server.store)
    server.worker.start()
    url=f'http://127.0.0.1:{server.server_port}'
    marker=server.store.root/'server.json'
    temporary=marker.with_suffix('.tmp')
    temporary.write_text(dump({'pid':os.getpid(),'url':url,'workspace_id':server.store.meta('workspace_id'),'launch_id':launch_id}),encoding='utf-8')
    temporary.replace(marker)
    print(f'BriefLoop: {url}',flush=True)
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    if desktop_owner:_watch_desktop_owner(server,sys.stdin.buffer)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        errors=_close_service(server)
        if errors:print('BriefLoop cleanup: '+dump(errors),flush=True)
