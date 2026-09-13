#!/usr/bin/env python3
"""Verify the artifact after moving it to an unrelated, space-containing path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

source = Path(sys.argv[1]).resolve()
if not source.is_dir():
    raise SystemExit('Provide a prepared runtime directory')
env = os.environ.copy()
for key in ('PYTHONPATH', 'PYTHONHOME', 'NODE_PATH'):
    env.pop(key, None)
env.update(PYTHONDONTWRITEBYTECODE='1', PATH='/usr/bin:/bin:/usr/sbin:/sbin')
with tempfile.TemporaryDirectory(prefix='BriefLoop relocated ') as temporary:
    moved = Path(temporary)/'Application Resources/runtime'
    moved.parent.mkdir()
    shutil.move(source, moved)
    try:
        py = moved/'python/bin/python3'
        node = moved/'node/bin/node'
        env['BRIEFLOOP_NODE'] = str(node)
        code = """
    import importlib.metadata as md, json, platform, sys
    from importlib.resources import files
    import briefloop, wikiskill, ssl, sqlite3, pydantic, pypdfium2, PIL, lxml.etree, mcp
    from briefloop.server import make_server
    from briefloop.runtime_bridge import RuntimeBridge
    assert platform.machine() == 'arm64'
    assert md.version('briefloop') == briefloop.__version__
    bridge = files('briefloop').joinpath('static/runtime-bridge.mjs')
    assert bridge.is_file() and bridge.stat().st_size > 1000
    assert files('briefloop').joinpath('static/frontend-licenses.txt').is_file()
    assert files('wikiskill').joinpath('__init__.py').is_file()
    print(json.dumps({'python': platform.python_version(), 'machine': platform.machine(), 'briefloop': briefloop.__version__, 'module': briefloop.__file__, 'prefix': sys.prefix, 'runtime_bridge': str(bridge)}))
    """
        result = json.loads(subprocess.check_output([str(py), '-I', '-c', textwrap.dedent(code)], cwd=temporary, env=env, text=True))
        assert str(moved/'python') in result['module'] and str(source) not in result['module']
        node_result = json.loads(subprocess.check_output([str(node), '-e',
            'console.log(JSON.stringify({version:process.version,arch:process.arch,execPath:process.execPath}))'],
            cwd=temporary, env=env, text=True))
        assert node_result['arch'] == 'arm64' and Path(node_result['execPath']).resolve() == node.resolve()
        cli = subprocess.check_output([str(moved/'python/bin/briefloop'), '--version'], cwd=temporary, env=env, text=True).strip()
        assert cli == 'BriefLoop '+result['briefloop']
        check = subprocess.check_output([str(py), '-I', '-m', 'pip', 'check'], cwd=temporary, env=env, text=True).strip()
        proof = {'status': 'passed', 'python': result, 'node': node_result, 'cli': cli, 'pip_check': check,
                 'isolated_python': True, 'host_pythonpath_removed': True, 'host_nodepath_removed': True,
                 'moved_path_contained_spaces': True, 'model_calls': 0, 'runtime_installs': 0}
        print(json.dumps(proof, indent=2))
    finally:
        shutil.move(moved, source)
    (source/'relocation-proof.json').write_text(json.dumps(proof, indent=2)+'\n')
