"""Move the prepared runtime to a Unicode path and check it without host tools."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap


def verify(source):
    source=source.resolve()
    if not (source/'manifest.json').is_file():raise ValueError('Expected a prepared runtime')
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','NODE_PATH')}
    env.update(PATH=str(Path(os.environ['SystemRoot'])/'System32'),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='0',PIP_NO_INDEX='1')
    with tempfile.TemporaryDirectory(prefix='BriefLoop 中文搬移 ') as temporary:
        moved=Path(temporary)/'应用 Resources/runtime';moved.parent.mkdir()
        # Relocate only this supplied build artifact; finally restores it even
        # after a failed import/verification, never touching installed apps.
        shutil.move(source,moved)
        try:
            py=moved/'python/python.exe';node=moved/'node/node.exe';env['BRIEFLOOP_NODE']=str(node)
            code='''
import base64, csv, hashlib, importlib.metadata as md, json, platform, sys
from pathlib import Path
from importlib.resources import files
import briefloop, wikiskill, ssl, sqlite3, pydantic, pypdfium2, PIL, lxml.etree, mcp, win32api, pywintypes
from briefloop.server import make_server
from briefloop.runtime_bridge import RuntimeBridge
root=Path(sys.prefix).resolve()
assert md.version('briefloop')==briefloop.__version__
for name in ('runtime-bridge.mjs','frontend-licenses.txt'):
    assert files('briefloop').joinpath('static',name).is_file()
assert files('wikiskill').joinpath('__init__.py').is_file()
records=0
for dist in md.distributions():
    assert dist.read_text('direct_url.json') is None
    record=Path(dist._path)/'RECORD'
    with record.open(encoding='utf-8',newline='') as stream:
        for name,digest,size in csv.reader(stream):
            path=(record.parent.parent/name).resolve()
            assert path.is_relative_to(root), name
            assert path.is_file(), name
            if digest:
                data=path.read_bytes()
                assert len(data)==int(size), name
                assert digest=='sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode('ascii').rstrip('='), name
    records+=1
print(json.dumps({'python':platform.python_version(),'machine':platform.machine(),'briefloop':briefloop.__version__,'module':briefloop.__file__,'prefix':sys.prefix,'verified_records':records}))
'''
            def output(args):return subprocess.check_output(list(map(str,args)),cwd=temporary,env=env,encoding='utf-8',timeout=90).strip()
            python=json.loads(output([py,'-I','-X','utf8','-c',textwrap.dedent(code)]))
            assert python['python']==manifest['python']['version']
            assert Path(python['module']).is_relative_to(moved/'python')
            node_result=json.loads(output([node,'-e','console.log(JSON.stringify({version:process.version,arch:process.arch,execPath:process.execPath}))']))
            assert node_result['arch']=='x64' and node_result['version']=='v'+manifest['node']['version']
            cli=output([py,'-I','-X','utf8','-m','briefloop','--version'])
            assert cli=='BriefLoop '+python['briefloop']
            launcher=output([os.environ['COMSPEC'],'/d','/c',moved/'python/Scripts/briefloop.cmd','--version'])
            assert launcher==cli
            check=output([py,'-I','-m','pip','check'])
            proof={'status':'passed','python':python,'node':node_result,'cli':cli,'relative_launcher':launcher,'pip_check':check,
                   'moved_path_contained_unicode_and_spaces':True,'host_tools_removed_from_path':True,
                   'model_calls':0,'runtime_installs':0}
        finally:shutil.move(moved,source)
    (source/'relocation-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(proof,ensure_ascii=False,indent=2),flush=True)
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('runtime',type=Path)
    verify(parser.parse_args().runtime)
