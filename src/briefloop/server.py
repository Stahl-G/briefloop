"""Loopback application: real save/command API, no static feedback façade."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit, parse_qs, quote
import base64
import json
import secrets
import os
import signal
import fcntl
from markdown_it import MarkdownIt
from pydantic import ValidationError
from .models import Requirements, Settings, SaveRevision, Comment
from .runtime import Worker
from .harness import HarnessManager
from .interactive_runtime import InteractiveRuntime
from .store import Store, Conflict, dump
from . import sources


def make_server(workspace, port=8765, *, paused=False):
    from .runtime_bridge import RuntimeBridge
    bridge=RuntimeBridge()
    store=Store(workspace)
    lock=(store.root/'.server.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close();raise RuntimeError('这个工作区已有本地服务在运行')
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
    managers={'codex':harness,'opencode':opencode_harness,**bridge_harnesses}
    worker.runtime=InteractiveRuntime(store,backends=managers)
    worker.opened_paused=paused
    def pick_harness(runtime=None,session_id=None):
        backend=(runtime or {}).get('backend')
        if session_id is not None:
            for candidate in managers.values():
                try:
                    owner=candidate.chat.session(session_id)['runtime'].get('backend','codex')
                    if backend is not None and backend!=owner:raise ValueError('切换执行引擎请新建会话；当前会话沿用原宿主')
                    backend=owner
                    break
                except KeyError:
                    continue
        backend=backend or store.settings().get('agent_backend','codex')
        return managers[validate_backend(backend)]
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
        runtime={'backend':backend,'model':model,'permission':'read-only' if backend in ('codex','opencode') else 'runtime-native'}
        session=manager.create_session(backend+' · 连接测试',runtime,root)
        message=manager.send(session['id'],'Reply with OK only. Do not use tools.',runtime=runtime,allow_web=False)
        manager.chat.event(session['id'],'runtime/test',{'backend':backend,'model':model,'kind':'short_model_call'})
        return {'session_id':session['id'],'message_id':message['id'],'status':'submitted'}

    token=secrets.token_urlsafe(24)
    assets=files('briefloop').joinpath('static')
    # Serve one UI/backend version for this process; builds must not replace a live UI halfway.
    asset_bytes={name:assets.joinpath(name).read_bytes() for name in ('index.html','app.js','style.css')}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,format,*args): pass
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
            self.send(409 if isinstance(exc,Conflict) else 400,{'error':str(exc)})
        def do_GET(self):
            try:
                u=urlsplit(self.path);q=parse_qs(u.query)
                if u.path=='/api/state':
                    snapshot=store.snapshot()
                    for source in snapshot['sources']:
                        sidecar=store.root/'sources'/(source['id']+'.provenance.json')
                        if sidecar.is_file():
                            try:
                                meta=json.loads(sidecar.read_text())
                                source['media_type']=meta.get('media_type');source['needs_visual']=bool(meta.get('needs_visual',False))
                            except (ValueError,OSError):pass
                    self.send(200,snapshot)
                elif u.path=='/api/workspaces':
                    from .workspaces import list_workspaces
                    self.send(200,list_workspaces(store))
                elif u.path=='/api/harness/sessions':self.send(200,{'sessions':harness.list_sessions(q.get('view',['active'])[0])})
                elif u.path=='/api/harness/session':self.send(200,pick_harness(session_id=q['id'][0]).snapshot(q['id'][0],int(q.get('after',['0'])[0]),reasoning=q.get('reasoning',['0'])[0]=='1'))
                elif u.path=='/api/session':self.send(200,{'token':token})
                elif u.path=='/api/runtime':
                    observed=worker._review_runtime if worker.review_current and not worker.current else worker.runtime
                    proc=observed.process
                    self.send(200,{'server_pid':os.getpid(),'worker_alive':worker.thread.is_alive(),'automatic_learning_paused':worker.opened_paused,'paused':worker.opened_paused,'job_id':worker.current,'pid':proc.pid if proc else None,'returncode':proc.poll() if proc else None})
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
                elif u.path=='/api/research-budget':
                    from .research_budget import snapshot
                    self.send(200,snapshot(store,q['run'][0]))
                elif u.path=='/api/learning-candidates':
                    from .projections import learning_candidates
                    self.send(200,learning_candidates(store))
                elif u.path=='/api/tavily':
                    from .tavily import key_status
                    self.send(200,key_status())
                elif u.path=='/api/opencode/providers':
                    self.send(200,{'configurations':opencode_harness._client().provider_settings()})
                elif u.path=='/api/runtimes':
                    self.send(200,bridge.discover())
                elif u.path=='/api/models':
                    from .backends import validate_backend
                    backend=validate_backend(q.get('backend',[store.settings().get('agent_backend','codex')])[0])
                    if backend=='opencode':
                        models=opencode_harness.list_models(refresh=q.get('refresh',[''])[0]=='1')
                    elif backend in bridge_harnesses:
                        catalog=bridge_harnesses[backend].list_models(refresh=q.get('refresh',[''])[0]=='1')
                        self.send(200,{'backend':backend,**catalog});return
                    else:
                        catalog=bridge.call('list_models',{'runtime_id':'codex','cwd':str(store.root)})
                        self.send(200,{'backend':backend,**catalog});return
                    self.send(200,{'backend':backend,'count':len(models),'models':models})
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
                elif u.path=='/api/export-file':
                    from .export_jobs import output_path
                    job=store.one('jobs',q['job'][0])
                    if job['status']!='complete':raise ValueError('Word 尚未制作完成')
                    data=output_path(store,job).read_bytes()
                    import hashlib
                    if hashlib.sha256(data).hexdigest()!=json.loads(job['result'])['sha256']:raise ValueError('Word 文件已变化，请重新生成')
                    self.send(200,data,'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_name='report.docx')
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
                elif u.path in ('/app.js','/style.css'):
                    self.send(200,asset_bytes[u.path[1:]],'text/javascript' if u.path.endswith('.js') else 'text/css')
                else:self.send(404,{'error':'未找到页面'})
            except (ValueError,KeyError,OSError,RuntimeError) as exc:self.error(exc)
        def do_POST(self):
            try:
                origin=self.headers.get('Origin')
                expected=f'http://127.0.0.1:{self.server.server_port}'
                if self.headers.get('X-BriefLoop-Token')!=token or origin and origin!=expected:
                    self.send(403,{'error':'页面会话已过期，请刷新后重试'});return
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<25*1024*1024:raise ValueError('请求为空或过大')
                body=json.loads(self.rfile.read(n));path=urlsplit(self.path).path
                if path=='/api/tavily':
                    from .tavily import save_key,delete_key
                    result=delete_key() if body.get('remove') else save_key(body['api_key'])
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
                elif path=='/api/harness/message':
                    choose_runtime(store,body.get('runtime'))
                    result=pick_harness(body.get('runtime'),body['session_id']).send(body['session_id'],body.get('text',''),mode=body.get('mode','queue'),source_ids=body.get('source_ids'),runtime=body.get('runtime'),message_id=body.get('message_id'),display_text=body.get('display_text'),allow_web=bool(body.get('allow_web',False)))
                elif path=='/api/harness/answer':result=pick_harness(session_id=body['session_id']).answer(body['session_id'],body['request_id'],body['answers'])
                elif path=='/api/harness/archive':result=pick_harness(session_id=body['session_id']).archive(body['session_id'])
                elif path=='/api/harness/delete':result=pick_harness(session_id=body['session_id']).delete(body['session_id'])
                elif path=='/api/harness/restore':result=pick_harness(session_id=body['session_id']).restore(body['session_id'])
                elif path=='/api/harness/archive-completed':result={'count':sum(manager.archive_completed(name)['count'] for name,manager in managers.items())}
                elif path=='/api/harness/cancel':result=pick_harness(session_id=body['session_id']).cancel(body['session_id'])
                elif path=='/api/upload':
                    data=base64.b64decode(body['data'],validate=True)
                    result=sources.upload(store,body['name'],data)
                elif path=='/api/source-url':result=sources.fetch(store,body['url'])
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
                        base64.b64decode(body['data'],validate=True) if body.get('data') else b'',
                        accept_unaligned=bool(body.get('accept_unaligned',False)),source_id=body.get('source_id'))
                elif path=='/api/company-resolve':
                    from .company_context import resolve_conflict
                    result=resolve_conflict(store,body['fact_id'],body['accept'])
                elif path=='/api/template-import':
                    from .templates import import_template
                    result=import_template(store,body['name'],base64.b64decode(body['data'],validate=True),body.get('parent_id'))
                elif path=='/api/export':
                    from .export_jobs import enqueue_export
                    result=enqueue_export(store,body['version_id'])
                elif path=='/api/generate':
                    req=Requirements.model_validate(body['requirements'])
                    run=store.create_run(req.model_dump(),body.get('source_ids',[]))
                    payload={'run_id':run['id']}
                    if body.get('session_id'):payload['session_id']=body['session_id']
                    result=store.enqueue('generate',payload)
                elif path=='/api/save':
                    value=SaveRevision.model_validate(body)
                    result=store.revise(value.base_version,value.markdown,value.editor_document)
                elif path=='/api/comment':
                    value=Comment.model_validate(body);result=store.comment(value.version_id,value.text)
                elif path=='/api/settings':
                    merged={**store.settings(),**body}
                    # Saving a model is the explicit choice the pending flag waits for.
                    if 'model_selection_required' not in body and str(body.get('model') or '').strip():merged['model_selection_required']=False
                    settings=Settings.model_validate(merged)
                    store.set_meta('settings',settings.model_dump());result=settings.model_dump()
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
                    result=store.enqueue('source_refresh',{'run_id':brief['run_id'],'version_id':brief['id'],'source_id':body['source_id'],'information_cutoff':body['information_cutoff']})
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
                    result=enqueue_feedback(store)
                elif path=='/api/stop':worker.stop_job(body['job_id']);result={'ok':True}
                elif path=='/api/resume':result=worker.resume(body['job_id'])
                elif path=='/api/task-dismiss':
                    job=store.one('jobs',body['job_id'])
                    if job['status'] not in ('failed','interrupted','cancelled'):raise ValueError('只有已结束且未完成的任务可以清除')
                    store.update_job(job['id'],'dismissed',error=job['error']);result={'ok':True}
                elif path=='/api/rollback':store.bind_skill(body.get('skill_id'));result={'ok':True}
                elif path=='/api/render':result={'html':MarkdownIt('commonmark',{'html':False}).enable('table').render(body['markdown'])}
                else:self.send(404,{'error':'未知操作'});return
                self.send(200,result)
            except (ValueError,KeyError,OSError,ValidationError) as exc:self.error(exc)
            except Exception as exc:
                self.send(500,{'error':str(exc)})
    try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    except OSError:
        harness.close();opencode_harness.close();lock.close();raise
    server.daemon_threads=True
    server.workspace_lock=lock;server.runtime_bridge=bridge;server.bridge_harnesses=bridge_harnesses
    server.store=store;server.worker=worker;server.harness=harness;server.opencode_harness=opencode_harness
    return server


def serve(workspace,port=8765,*,paused=False):
    server=make_server(workspace,port,paused=paused)
    server.worker.start()
    url=f'http://127.0.0.1:{server.server_port}'
    (server.store.root/'server.json').write_text(dump({'pid':os.getpid(),'url':url,'workspace_id':server.store.meta('workspace_id')}))
    print(f'BriefLoop: {url}',flush=True)
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for manager in server.bridge_harnesses.values():manager.close()
        server.worker.close();server.harness.close();server.opencode_harness.close();server.runtime_bridge.close();server.server_close();server.workspace_lock.close()
