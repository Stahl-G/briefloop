import argparse
from ._entrypoint import command as entry_command
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import os
import secrets
from .store import Store
from .backends import BACKENDS, REVIEW_ONLY_BACKENDS
from . import __version__


def _search_failure(operation,exc,provider='tavily'):
    """A structured, redacted failure so the Scout's path-switching can act on it."""
    return {'provider':getattr(exc,'provider',None) or provider,'operation':operation,'status':'failed',
            'failure_kind':getattr(exc,'failure_kind','provider_error'),
            'http_status':getattr(exc,'status',None),
            'error':str(exc),
            'request_record_path':getattr(exc,'request_record_path',None)}


def _tavily_failure(operation,exc):
    # Compat name for the Tavily-only commands and their tests.
    return _search_failure(operation,exc)


def main():
    from .platform_support import ensure_utf8
    ensure_utf8()
    p=argparse.ArgumentParser(prog='briefloop',description='本地简报、改稿与持续学习')
    p.add_argument('--version',action='version',version=f'BriefLoop {__version__}')
    sub=p.add_subparsers(dest='command',required=True)
    version=sub.add_parser('version',help='查看实际后端版本、安装来源和更新方式')
    version.add_argument('--check',action='store_true',help='检查 PyPI 稳定版本，不安装或重启')
    for name in ('serve','start','status','doctor'):
        parser=sub.add_parser(name)
        parser.add_argument('--workspace',default='./workspaces/default')
        if name in ('serve','start'):
            parser.add_argument('--port',type=int,default=8765)
            parser.add_argument('--paused',action='store_true',help='打开工作区但不自动重跑旧队列或反馈学习')
            parser.add_argument('--backend',choices=[b for b in BACKENDS if b not in REVIEW_ONLY_BACKENDS],default=None,help='新任务默认走哪个 CLI 后端；不传则沿用工作区设置')
    external=sub.add_parser('external',help='连接已授权的本地工作区；不自动创建或启动服务')
    external.add_argument('--workspace',required=True)
    es=external.add_subparsers(dest='external_action',required=True)
    es.add_parser('discover',help='只读检查现有工作区和服务')
    es.add_parser('skill',help='输出随包提供的 WorkBuddy/本地 Agent 使用说明，不连接工作区')
    er=es.add_parser('request',help='提交一个外部请求；任务异步执行，写请求须保留request_id')
    er.add_argument('--file',required=True,help='UTF-8 JSON 请求文件')
    ed=es.add_parser('download',help='下载指定已完成导出任务的Word，不覆盖不同内容的文件')
    ed.add_argument('--job',required=True);ed.add_argument('--output',required=True)
    tool=sub.add_parser('tool',help='agent 使用的来源工具')
    tool.add_argument('--workspace',required=True)
    ts=tool.add_subparsers(dest='tool',required=True)
    add=ts.add_parser('add-url');add.add_argument('--run',required=True);add.add_argument('--url',required=True)
    read=ts.add_parser('read-source');read.add_argument('--id',required=True)
    read.add_argument('--start-line',type=int);read.add_argument('--end-line',type=int);read.add_argument('--max-chars',type=int)
    render=ts.add_parser('render-source',help='按需渲染 PDF 指定页面，不执行 OCR 或模型')
    render.add_argument('--id',required=True);render.add_argument('--pages',nargs='+',type=int,required=True)
    figure=ts.add_parser('register-figure',help='登记已生成图像及数据/脚本快照；不执行脚本')
    figure.add_argument('--run',required=True);figure.add_argument('--image',required=True);figure.add_argument('--title',required=True)
    figure.add_argument('--caption',default='');figure.add_argument('--source',action='append',default=[])
    figure.add_argument('--data');figure.add_argument('--script')
    fact=ts.add_parser('fact-status',help='登记事实核查结果：逐条候选状态、一句依据与证据 span，只做确定性校验落库')
    fact.add_argument('--run',required=True)
    fact.add_argument('--file',required=True,help='UTF-8 JSON 结果文件（version_id/selection/candidates/execution）')
    fact.add_argument('--job',help='本次核查任务 job id，用于记录事件')
    join=ts.add_parser('join-scouts');join.add_argument('--files',nargs='+',required=True)
    join.add_argument('--run');join.add_argument('--round');join.add_argument('--slots',nargs='+')
    join.add_argument('--output',help='保存合并结果为 UTF-8 JSON，避免 shell 重定向改变编码')
    document=ts.add_parser('normalize-document',help='检查富文档 JSON 并生成兼容 Markdown，用于导入与字数检查')
    document.add_argument('--file',required=True);document.add_argument('--output')
    count=ts.add_parser('count-brief',help='按统一中英混合规则统计 Markdown 正文长度')
    count.add_argument('--file',required=True,help='Markdown 正文文件，不包含 citations 元数据')
    count.add_argument('--target-words',type=int);count.add_argument('--max-words',type=int)
    writer=ts.add_parser('writer',help='按冻结写作协议保存Markdown正文、局部修订和证据；不发布')
    writer.add_argument('--run',required=True);writer.add_argument('--draft-file',required=True)
    writer.add_argument('--operation',required=True,choices=['write_report','write_sections','assemble_report','update_draft_evidence','update_draft_details','patch_report_text','replace_report_blocks','read_draft','check_draft','submit_draft'])
    writer.add_argument('--file',help='本次任务目录内的UTF-8 Markdown（write_report）或操作JSON')
    writer.add_argument('--title');writer.add_argument('--revision')
    check=ts.add_parser('check-draft',help='按稿件契约自检 draft.json；只检查不发布')
    check.add_argument('--file',required=True)
    check.add_argument('--run',help='按本次报告要求返回篇幅、引用与数字定位诊断；不发布、不评分')
    submit=ts.add_parser('submit-draft',help='接纳已经检查的完整稿件版本；不发布或评分')
    submit.add_argument('--file',required=True);submit.add_argument('--run',required=True)
    submit.add_argument('--revision',required=True)
    report_data=ts.add_parser('prepare-report-data',help='核对行业指标来源并计算变化；输出计算表与数据缺口')
    report_data.add_argument('--run',required=True);report_data.add_argument('--file',required=True)
    report_data.add_argument('--output',help='保存计算包 JSON 的路径；原始 records 写入 draft.report_data')
    workbook=ts.add_parser('extract-workbook-figures',help='提取XLSX内嵌图像并列出需渲染的原生图表');workbook.add_argument('--id',required=True)
    action=ts.add_parser('workspace-action' ,help='交互助手操作当前工作区')
    action.add_argument('--request',required=True)
    tavily_search=ts.add_parser('tavily-search',help='Tavily 搜索摘要，只发现来源')
    tavily_search.add_argument('--run',required=True);tavily_search.add_argument('--query',required=True)
    tavily_search.add_argument('--topic',choices=['general','news'],default='general')
    tavily_search.add_argument('--time-range',choices=['day','week','month','year'])
    tavily_search.add_argument('--start-date');tavily_search.add_argument('--end-date')
    tavily_search.add_argument('--include-domain',action='append',default=[])
    tavily_search.add_argument('--exclude-domain',action='append',default=[])
    tavily_search.add_argument('--max-results',type=int,default=5)
    tavily_search.add_argument('--search-depth',choices=['basic','advanced'],default='basic')
    tavily_extract=ts.add_parser('tavily-extract',help='保存 Tavily 提取的正文与提供方响应')
    tavily_extract.add_argument('--run',required=True);tavily_extract.add_argument('--url',action='append',required=True)
    tavily_extract.add_argument('--extract-depth',choices=['basic','advanced'],default='basic')
    web=ts.add_parser('web-search',help='联网搜索摘要，按本轮冻结的搜索源自动选择 provider，只发现来源')
    web.add_argument('--run',required=True);web.add_argument('--query',required=True)
    web.add_argument('--provider',choices=['tavily','duckduckgo','bocha','zhipu'])
    web.add_argument('--purpose',choices=['primary','coverage_probe','gap_repair'],default='primary')
    web.add_argument('--reason',default='');web.add_argument('--gap-id')
    web.add_argument('--topic',choices=['general','news'],default='general')
    web.add_argument('--time-range',choices=['day','week','month','year'])
    web.add_argument('--start-date');web.add_argument('--end-date')
    web.add_argument('--include-domain',action='append',default=[])
    web.add_argument('--exclude-domain',action='append',default=[])
    web.add_argument('--max-results',type=int,default=5)
    web.add_argument('--search-depth',choices=['basic','advanced'],default='basic')
    a=p.parse_args()
    if a.command=='version':
        from .software_version import runtime_info,check_update
        print(json.dumps(check_update() if a.check else runtime_info(),ensure_ascii=False,indent=2))
    elif a.command=='external':
        import http.client
        from .external_client import discover, Client
        from .execution_records import sanitize
        try:
            if a.external_action=='skill':
                from importlib.resources import files
                print(files('briefloop').joinpath('skill_assets','briefloop-external','SKILL.md').read_text(encoding='utf-8'))
                return
            if a.external_action=='discover':result=discover(a.workspace)
            elif a.external_action=='request':result=Client(a.workspace).request(json.loads(Path(a.file).read_text(encoding='utf-8-sig')))
            else:result=Client(a.workspace).download(a.job,a.output)
            print(json.dumps(result,ensure_ascii=False))
        except (OSError,ValueError,KeyError,http.client.HTTPException) as exc:
            p.exit(2,json.dumps({'status':'error','message':sanitize(str(exc))},ensure_ascii=False)+'\n')
    elif a.command=='serve':
        from .server import serve
        serve(a.workspace,a.port,paused=a.paused,backend=a.backend)
    elif a.command=='start':
        root=Path(a.workspace).resolve();root.mkdir(parents=True,exist_ok=True)
        launch_id=secrets.token_hex(16)
        with (root/'server.log').open('a') as log:
            proc=subprocess.Popen(entry_command('serve','--workspace',root,'--port',a.port)+(['--paused'] if a.paused else [])+(['--backend',a.backend] if a.backend else []),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,env={**os.environ,'BRIEFLOOP_LAUNCH_ID':launch_id},**({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}))
        # A clean Windows workspace imports bundled templates before readiness.
        for _ in range(450):
            if proc.poll() is not None:raise RuntimeError('服务未能启动，请查看 '+str(root/'server.log'))
            info=root/'server.json'
            if info.exists():
                value=json.loads(info.read_text())
                # Windows venv redirectors can launch a different Python PID.
                if value.get('launch_id')==launch_id:
                    (root/'server.pid').write_text(str(value['pid']))
                    print(f"BriefLoop 已启动：{value['url']}，日志：{root/'server.log'}");break
            time.sleep(.1)
        else:raise RuntimeError('服务尚未报告就绪，请查看 '+str(root/'server.log'))
    elif a.command=='status':print(json.dumps(Store(a.workspace).snapshot(),ensure_ascii=False,indent=2))
    elif a.command=='doctor':
        from .backends.opencode_server import EXPECTED_MAJOR
        from .host_bins import find as _find_host_bin
        print(json.dumps({'codex':_find_host_bin('codex'),'opencode':_find_host_bin('opencode'),'opencode_expected_major':EXPECTED_MAJOR,'pdftotext':_find_host_bin('pdftotext'),'workspace':str(Path(a.workspace).resolve()),'note':'检查命令存在；未启动模型、未验证登录'},ensure_ascii=False,indent=2))
    elif a.command=='tool':
        store=Store(a.workspace)
        if a.tool=='normalize-document':
            from .document_model import normalize_document,document_markdown
            document=normalize_document(json.loads(Path(a.file).read_text(encoding='utf-8-sig')))
            markdown=document_markdown(document)
            if a.output:Path(a.output).write_text(markdown,encoding='utf-8')
            print(json.dumps({'document':document,'markdown':markdown},ensure_ascii=False))
        elif a.tool=='workspace-action':
            from .chat_tools import workspace_action
            from pydantic import ValidationError
            # Windows PowerShell 5.1 writes a BOM for Out-File -Encoding utf8.
            try:
                request = json.loads(Path(a.request).read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                p.exit(2, json.dumps({'status': 'invalid', 'error': 'request_file_invalid',
                    'message': '--request 需要工作区内 UTF-8 JSON 文件的路径，不是 JSON 正文。'}, ensure_ascii=False) + '\n')
            try:
                result = workspace_action(store, request)
            except ValidationError as exc:
                errors = [{'field': '.'.join(map(str, error['loc'])),
                           'type': error['type'], 'message': error['msg']}
                          for error in exc.errors(include_url=False, include_input=False, include_context=False)]
                p.exit(2, json.dumps({'status': 'invalid', 'error': 'validation_error',
                    'errors': errors}, ensure_ascii=False) + '\n')
            print(json.dumps(result, ensure_ascii=False))
        elif a.tool=='web-search':
            from . import websearch
            try:
                result=websearch.search(a.query,provider=a.provider,purpose=a.purpose,reason=a.reason,gap_id=a.gap_id,topic=a.topic,time_range=a.time_range,start_date=a.start_date,end_date=a.end_date,
                                        include_domains=a.include_domain,exclude_domains=a.exclude_domain,
                                        max_results=a.max_results,search_depth=a.search_depth,store=store,run_id=a.run)
            except websearch.SearchError as exc:
                print(json.dumps(_search_failure('search',exc),ensure_ascii=False))
            else:
                print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='tavily-search':
            from . import tavily
            try:
                tavily.check_run(store,a.run)
                result=tavily.search(a.query,topic=a.topic,time_range=a.time_range,start_date=a.start_date,end_date=a.end_date,include_domains=a.include_domain,exclude_domains=a.exclude_domain,max_results=a.max_results,search_depth=a.search_depth,store=store,run_id=a.run)
            except tavily.TavilyError as exc:
                print(json.dumps(_search_failure('search',exc),ensure_ascii=False))
            else:
                print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='tavily-extract':
            from . import tavily
            try:
                result=tavily.extract(store,a.url,run_id=a.run,extract_depth=a.extract_depth)
            except tavily.TavilyError as exc:
                print(json.dumps(_search_failure('extract',exc),ensure_ascii=False))
            else:
                print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='prepare-report-data':
            from .report_tools import prepare_for_run
            from .store import dump
            result=prepare_for_run(store,a.run,json.loads(Path(a.file).read_text(encoding='utf-8-sig')))
            if a.output:
                output=Path(a.output).expanduser().resolve();output.parent.mkdir(parents=True,exist_ok=True)
                temporary=output.with_name(output.name+'.tmp');temporary.write_text(dump(result),encoding='utf-8');temporary.replace(output)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='extract-workbook-figures':
            from .workbook_figures import extract_workbook_figures
            print(json.dumps(extract_workbook_figures(store,a.id),ensure_ascii=False))
        elif a.tool=='writer':
            from . import writer_input, analyst_drafts
            from .native_roles import run_tool
            try:
                target=Path(a.draft_file).expanduser().resolve()
                config=analyst_drafts.file_config(store,a.run,target)
                config['native_role']='analyst'
                if writer_input.protocol(config)!=writer_input.PROTOCOL:
                    raise ValueError('本任务未启用 writer_input_v1')
                args={}
                if a.file:
                    path=Path(a.file).expanduser().resolve()
                    if not path.is_relative_to(target.parent):raise ValueError('输入文件必须位于本次写作任务目录')
                    raw=path.read_text(encoding='utf-8-sig')
                    args={'title':a.title,'markdown':raw} if a.operation=='write_report' else json.loads(raw)
                if a.revision:args['revision']=a.revision
                result=run_tool(store,config,a.operation,args)
                if not result['ok']:raise ValueError(result['error'])
                report=json.loads(result.get('settle') or result['content'][0]['text'])
            except (ValueError,KeyError,OSError) as exc:
                print(json.dumps({'status':'invalid','error':str(exc)},ensure_ascii=False));raise SystemExit(1)
            print(json.dumps(report,ensure_ascii=False))
        elif a.tool in ('check-draft','submit-draft'):
            from .models import BriefDraft, check_artifact, prune_unknown
            path=Path(a.file).expanduser().resolve()
            try:
                value=json.loads(path.read_text(encoding='utf-8-sig'))
                if (path.parent/'packet/input.json').exists():
                    from .analyst_drafts import file_config, save, check, submit
                    run_id=a.run or json.loads((path.parent/'packet/input.json').read_text())['run_id']
                    config=file_config(store,run_id,path)
                    if a.tool=='submit-draft':
                        report=submit(store,config,{'revision':a.revision},file_value=value)
                    else:
                        saved=save(store,config,value)
                        report={'status':'ok','scope':'writer_packet','unknown_fields':[],'errors':[],
                                **check(store,config,{'revision':saved['revision']})}
                        report['status']='ok'
                elif a.tool=='submit-draft':
                    raise ValueError('提交需要当前写作任务包和执行身份')
                else:
                    report=check_artifact(value,BriefDraft)
                    report['scope']='run_diagnostics' if a.run else 'schema_only'
                    if report['status']=='ok':
                        from .draft_checks import inspect_draft
                        requirements=json.loads(store.one('runs',a.run)['requirements']) if a.run else None
                        allowed=set(store.source_ids(a.run)) - set((requirements or {}).get('reference_source_ids') or []) if a.run else None
                        frozen=store.meta('reader_contract:'+a.run) if a.run else None
                        if frozen and value.get('reader_contract') not in (None,frozen):
                            raise ValueError('不能改写本轮冻结的 reader_contract')
                        normalized,_=prune_unknown(value,BriefDraft)
                        report['diagnostics']=inspect_draft(normalized,requirements,store=store,allowed_sources=allowed)
            except (ValueError,KeyError,OSError) as exc:
                report={'status':'invalid','errors':[{'field':'draft','message':str(exc)}]}
            print(json.dumps(report,ensure_ascii=False))
            if report['status'] not in ('ok','saved'):raise SystemExit(1)
        elif a.tool=='count-brief':
            from .length import length_stats
            result=length_stats(Path(a.file).expanduser().read_text(encoding='utf-8'),target_words=a.target_words,max_words=a.max_words)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='render-source':
            from .media import render_source_pages
            print(json.dumps(render_source_pages(store,a.id,a.pages),ensure_ascii=False))
        elif a.tool=='register-figure':
            from .figures import register_figure
            print(json.dumps(register_figure(store,a.run,a.image,a.title,caption=a.caption,source_ids=a.source,data_path=a.data,script_path=a.script),ensure_ascii=False))
        elif a.tool=='fact-status':
            from .fact_check import submit_result,FactCheckError
            from .research_plan import AdmissionError
            try:payload=json.loads(Path(a.file).read_text(encoding='utf-8-sig'))
            except (OSError,ValueError) as exc:
                p.exit(2,json.dumps({'status':'error','error':'result_file_invalid','message':'--file 需要 UTF-8 JSON 结果文件：'+str(exc)},ensure_ascii=False)+'\n')
            try:admitted=submit_result(store,a.run,payload,job_id=a.job)
            except FactCheckError as exc:
                # 契约违规整体结构化退回，供 agent 按逐条错误自修后重交。
                print(json.dumps({'status':'invalid','error':'fact_check_contract','errors':exc.errors},ensure_ascii=False));raise SystemExit(1)
            except AdmissionError as exc:
                print(json.dumps({'status':'error','error':exc.code,'message':str(exc)},ensure_ascii=False));raise SystemExit(1)
            record=admitted['record']
            print(json.dumps({'status':'ok','record_id':record['id'],'version_id':record['version_id'],
                              'stage_id':record['stage_id'],'execution':record['execution'],
                              'checked':len(record['candidates']),'unchecked':record['unchecked'],
                              'stage':admitted['stage']['status']},ensure_ascii=False))
        elif a.tool=='read-source':
            from .scout_tools import read_source
            print(read_source(store,a.id,start_line=a.start_line,end_line=a.end_line,max_chars=a.max_chars))
        elif a.tool=='join-scouts':
            from .scout_tools import join_scouts
            result=json.dumps(join_scouts(store,a.files,run_id=a.run,round_id=a.round,slots=a.slots),ensure_ascii=False)
            if a.output:
                output=Path(a.output).expanduser().resolve();output.parent.mkdir(parents=True,exist_ok=True)
                temporary=output.with_name(output.name+'.tmp');temporary.write_text(result,encoding='utf-8');temporary.replace(output)
            print(result)
        elif a.tool=='add-url':
            from .sources import fetch_for_run
            result=fetch_for_run(store,a.run,a.url)
            print(json.dumps(result,ensure_ascii=False))
