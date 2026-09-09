"""One-checkout startup. No second git clone, Node build or manual WikiSkill setup."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import webbrowser


def live_url(workspace):
    marker=workspace/'server.json'
    if not marker.exists():return None
    try:
        data=json.loads(marker.read_text())
        with urllib.request.urlopen(data['url']+'/api/state',timeout=2) as response:
            state=json.load(response)
        if data.get('workspace_id') and state.get('workspace_id')==data['workspace_id']:return data['url']
    except (OSError,ValueError,KeyError):return None


def main():
    if sys.version_info<(3,11):raise SystemExit('BriefLoop 需要 Python 3.11 或更新版本。')
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description='安装依赖并启动 BriefLoop；无需另行 clone WikiSkill')
    parser.add_argument('--workspace',type=Path,default=root/'workspaces/default')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args();workspace=args.workspace.expanduser().resolve()
    url=live_url(workspace)
    if not url:
        venv=root/'.venv';python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.exists():
            print('首次启动：正在建立本地环境…',flush=True)
            subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
        wheels=list((root/'vendor/wheels').glob('wikiskill_research-*.whl'))
        if len(wheels)!=1:raise SystemExit('项目缺少唯一的 WikiSkill 依赖包，请使用完整的 BriefLoop 仓库。')
        digest=hashlib.sha256()
        for path in [root/'pyproject.toml',wheels[0],*sorted((root/'src/briefloop').rglob('*'))]:
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':digest.update(path.read_bytes())
        signature=digest.hexdigest();marker=venv/'briefloop-install.json'
        installed=json.loads(marker.read_text()).get('signature') if marker.exists() else None
        if installed!=signature:
            print('正在安装 BriefLoop 和随项目提供的 WikiSkill 依赖…',flush=True)
            subprocess.run([str(python),'-m','pip','install','--find-links',str(root/'vendor/wheels'),str(root)],cwd=root,check=True)
            marker.write_text(json.dumps({'signature':signature}))
        subprocess.run([str(python),'-m','briefloop','start','--workspace',str(workspace),'--port',str(args.port)],cwd=root,check=True)
        url=live_url(workspace)
        if not url:raise SystemExit('服务尚未就绪，请查看工作区 server.log。')
    print('打开 BriefLoop：'+url,flush=True)
    if not args.no_open:webbrowser.open(url)


if __name__=='__main__':main()
