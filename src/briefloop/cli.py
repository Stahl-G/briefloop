import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .store import Store


def main():
    p=argparse.ArgumentParser(prog='briefloop',description='本地简报、改稿与持续学习')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('serve','start','status','doctor'):
        parser=sub.add_parser(name)
        parser.add_argument('--workspace',default='./workspaces/default')
        if name in ('serve','start'):parser.add_argument('--port',type=int,default=8765)
    tool=sub.add_parser('tool',help='agent 使用的来源工具')
    tool.add_argument('--workspace',required=True)
    ts=tool.add_subparsers(dest='tool',required=True)
    add=ts.add_parser('add-url');add.add_argument('--run',required=True);add.add_argument('--url',required=True)
    read=ts.add_parser('read-source');read.add_argument('--id',required=True)
    join=ts.add_parser('join-scouts');join.add_argument('--files',nargs='+',required=True)
    a=p.parse_args()
    if a.command=='serve':
        from .server import serve
        serve(a.workspace,a.port)
    elif a.command=='start':
        root=Path(a.workspace).resolve();root.mkdir(parents=True,exist_ok=True)
        with (root/'server.log').open('a') as log:
            proc=subprocess.Popen([sys.executable,'-m','briefloop','serve','--workspace',str(root),'--port',str(a.port)],stdout=log,stderr=log,start_new_session=True)
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
        if a.tool=='read-source':print(store.source_text(a.id))
        elif a.tool=='join-scouts':
            from .scout_tools import join_scouts
            print(json.dumps(join_scouts(store,a.files),ensure_ascii=False))
        elif a.tool=='add-url':
            from .sources import fetch
            run=store.one('runs',a.run)
            if not json.loads(run['requirements'])['allow_web']:raise ValueError('本轮仅允许本地来源')
            result=fetch(store,a.url);store.attach_source(a.run,result['id'])
            print(json.dumps(result,ensure_ascii=False))
