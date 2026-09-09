import argparse
import json
import os
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
    tool=sub.add_parser('tool',help='agent 使用的来源工具')
    tool.add_argument('--workspace',required=True)
    ts=tool.add_subparsers(dest='tool',required=True)
    add=ts.add_parser('add-url');add.add_argument('--run',required=True);add.add_argument('--url',required=True)
    read=ts.add_parser('read-source');read.add_argument('--id',required=True)
    read.add_argument('--start-line',type=int);read.add_argument('--end-line',type=int);read.add_argument('--max-chars',type=int)
    join=ts.add_parser('join-scouts');join.add_argument('--files',nargs='+',required=True)
    count=ts.add_parser('count-brief',help='按统一中英混合规则统计 Markdown 正文长度')
    count.add_argument('--file',required=True,help='Markdown 正文文件，不包含 citations 元数据')
    count.add_argument('--target-words',type=int);count.add_argument('--max-words',type=int)
    action=ts.add_parser('workspace-action',help='交互助手操作当前工作区')
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
        serve(a.workspace,a.port,paused=a.paused)
    elif a.command=='start':
        root=Path(a.workspace).resolve();root.mkdir(parents=True,exist_ok=True)
        with (root/'server.log').open('a') as log:
            proc=subprocess.Popen([sys.executable,'-m','briefloop','serve','--workspace',str(root),'--port',str(a.port)]+(['--paused'] if a.paused else []),stdout=log,stderr=log,start_new_session=True)
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
        print(json.dumps({'codex':shutil.which('codex'),'pdftotext':shutil.which('pdftotext'),'workspace':str(Path(a.workspace).resolve()),'note':'检查命令存在；未启动模型、未验证登录'},ensure_ascii=False,indent=2))
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
        elif a.tool=='count-brief':
            from .length import length_stats
            result=length_stats(Path(a.file).expanduser().read_text(encoding='utf-8'),target_words=a.target_words,max_words=a.max_words)
            print(json.dumps(result,ensure_ascii=False))
        elif a.tool=='read-source':
            from .scout_tools import read_source
            print(read_source(store,a.id,start_line=a.start_line,end_line=a.end_line,max_chars=a.max_chars))
        elif a.tool=='join-scouts':
            from .scout_tools import join_scouts
            print(json.dumps(join_scouts(store,a.files),ensure_ascii=False))
        elif a.tool=='add-url':
            from .sources import fetch_for_run
            result=fetch_for_run(store,a.run,a.url)
            print(json.dumps(result,ensure_ascii=False))
