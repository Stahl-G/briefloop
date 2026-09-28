"""Windows lock and UTF-8 contracts for the WikiSkill evolution driver."""
import json
import os
from pathlib import Path
import subprocess
import sys

from wikiskill import engine
from wikiskill.jsonl import read_jsonl
from wikiskill.k4_lock import workspace_lock


def test_engine_event_and_snapshot_files_are_portable_utf8(tmp_path):
    value={'type':'gate','message':'新增中文事实'}
    log=tmp_path/'events.jsonl'
    engine.append(log,value)
    assert log.read_bytes()==(json.dumps(value,ensure_ascii=False,default=str)+'\n').encode('utf-8')
    assert read_jsonl(log)==[value]

    snapshot=tmp_path/'snapshot.json'
    engine.save(snapshot,{'skill':'汇总来源与结论'})
    assert snapshot.read_bytes().endswith('\n'.encode('utf-8'))
    assert engine.read(snapshot)=={'skill':'汇总来源与结论'}


def test_workspace_lock_excludes_another_process_then_releases(tmp_path):
    root=tmp_path/'工作区'
    repo=Path(__file__).resolve().parents[1]
    env=os.environ.copy()
    source_path=str(repo/'src')
    env['PYTHONPATH']=source_path+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    code='''
import sys
from pathlib import Path
from wikiskill.k4_lock import workspace_lock
try:
    with workspace_lock(Path(sys.argv[1])):
        print('acquired')
except RuntimeError:
    print('busy')
'''
    with workspace_lock(root):
        result=subprocess.run([sys.executable,'-c',code,str(root)],env=env,
                              capture_output=True,text=True,encoding='utf-8',timeout=10)
        assert result.returncode==0 and result.stdout.strip()=='busy',result.stderr
    result=subprocess.run([sys.executable,'-c',code,str(root)],env=env,
                          capture_output=True,text=True,encoding='utf-8',timeout=10)
    assert result.returncode==0 and result.stdout.strip()=='acquired',result.stderr
