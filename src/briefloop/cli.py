import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .store import Store
from . import __version__


def main():
    p=argparse.ArgumentParser(prog='briefloop',description='本地简报、改稿与持续学习')
    p.add_argument('--version',action='version',version=f'BriefLoop {__version__}')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('serve','start','status','doctor'):
        parser=sub.add_parser(name)
        parser.add_argument('--workspace',default='./workspaces/default')
        if name in ('serve','start'):
            parser.add_argument('--port',type=int,default=8765)
            parser.add_argument('--paused',action='store_true',help='打开工作区但不自动重跑旧队列或反馈学习')
            parser.add_argument('--backend',choices=('codex','opencode'),default=None,help='新任务默认走哪个 CLI 后端；不传则沿用工作区设置')
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
    join=ts.add_parser('join-scouts');join.add_argument('--files',nargs='+',required=True)
    count=ts.add_parser('count-brief',help='按统一中英混合规则统计 Markdown 正文长度')
    count.add_argument('--file',required=True,help='Markdown 正文文件，不包含 citations 元数据')
    count.add_argument('--target-words',type=int);count.add_argument('--max-words',type=int)
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
    a=p.parse_args()
    if a.command=='serve':
        from .server import serve
        if a.backend is not None:
            from .models import Settings
            store=Store(a.workspace)
            settings=Settings.model_validate({**store.settings(),'agent_backend':a.backend})
            store.set_meta('settings',settings.model_dump())
        serve(a.workspace,a.port,paused=a.paused)
    elif a.command=='start':
        root=Path(a.workspace).resolve();root.mkdir(parents=True,exist_ok=True)
        with (root/'server.log').open('a') as log:
            proc=subprocess.Popen([sys.executable,'-m','briefloop','serve','--workspace',str(root),'--port',str(a.port)]+(['--paused'] if a.paused else [])+(['--backend',a.backend] if a.backend else []),stdout=log,stderr=log,start_new_session=True)
        (root/'server.pid').write_text(str(proc.pid))
        for _ in range(80):
            if proc.poll() is not None:raise RuntimeError('服务未能启动，请查看 '+str(root/'server.log'))
            info=root/'server.json'
            if info.exists():
                value=json.loads(info.read_text())
                if value['pid']==proc.pid:
                    print(f"BriefLoop 已启动：{value['url']}，日志：{root/'server.log'}");break
            time.sleep(.1)
        else:raise RuntimeError('服务尚未报告就绪，请查看 '+str(root/'server.log'))
    elif a.command=='status':print(json.dumps(Store(a.workspace).snapshot(),ensure_ascii=False,indent=2))
    elif a.command=='doctor':
        from .backends.opencode_server import EXPECTED_MAJOR
        opencode=shutil.which('opencode')
        print(json.dumps({'codex':shutil.which('codex'),'opencode':opencode,'opencode_expected_major':EXPECTED_MAJOR,'pdftotext':shutil.which('pdftotext'),'workspace':str(Path(a.workspace).resolve()),'note':'检查命令存在；未启动模型、未验证登录'},ensure_ascii=False,indent=2))
    elif a.command=='tool':
        store=Store(a.workspace)
        if a.tool=='workspace-action':
            from .chat_tools import workspace_action
            print(json.dumps(workspace_action(store,json.loads(Path(a.request).read_text())),ensure_ascii=False))
        elif a.tool=='tavily-search':
            from . import tavily
            tavily.check_run(store,a.run)
            result=tavily.search(a.query,topic=a.topic,time_range=a.time_range,start_date=a.start_date,end_date=a.end_date,include_domains=a.include_domain,exclude_domains=a.exclude_domain,max_results=a.max_results,search_depth=a.search_depth,store=store,run_id=a.run)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='tavily-extract':
            from . import tavily
            print(json.dumps(tavily.extract(store,a.url,run_id=a.run,extract_depth=a.extract_depth),ensure_ascii=False))
        elif a.tool=='prepare-report-data':
            from .report_tools import prepare_for_run
            from .store import dump
            result=prepare_for_run(store,a.run,json.loads(Path(a.file).read_text(encoding='utf-8')))
            if a.output:
                output=Path(a.output).expanduser().resolve();output.parent.mkdir(parents=True,exist_ok=True)
                temporary=output.with_name(output.name+'.tmp');temporary.write_text(dump(result),encoding='utf-8');temporary.replace(output)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='count-brief':
            from .length import length_stats
            result=length_stats(Path(a.file).expanduser().read_text(encoding='utf-8'),target_words=a.target_words,max_words=a.max_words)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='extract-workbook-figures':
            from .workbook_figures import extract_workbook_figures
            print(json.dumps(extract_workbook_figures(store,a.id),ensure_ascii=False))
        elif a.tool=='read-source':
            from .scout_tools import read_source
            print(read_source(store,a.id,start_line=a.start_line,end_line=a.end_line,max_chars=a.max_chars))
        elif a.tool=='render-source':
            from .media import render_source_pages
            print(json.dumps(render_source_pages(store,a.id,a.pages),ensure_ascii=False))
        elif a.tool=='register-figure':
            from .figures import register_figure
            print(json.dumps(register_figure(store,a.run,a.image,a.title,caption=a.caption,source_ids=a.source,data_path=a.data,script_path=a.script),ensure_ascii=False))
        elif a.tool=='join-scouts':
            from .scout_tools import join_scouts
            print(json.dumps(join_scouts(store,a.files),ensure_ascii=False))
        elif a.tool=='add-url':
            from .sources import fetch_for_run
            result=fetch_for_run(store,a.run,a.url)
            print(json.dumps(result,ensure_ascii=False))
