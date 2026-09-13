#!/usr/bin/env python3
"""Refresh only the current business wheel after a native runtime has been verified."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib

helpers = runpy.run_path(str(Path(__file__).with_name('prepare-runtime.py')))
HERE, REPO, OUTPUT, CACHE, run = (helpers[key] for key in ('HERE', 'REPO', 'OUTPUT', 'CACHE', 'run'))
lock = json.loads((HERE/'runtime-lock.json').read_text())
previous = json.loads((OUTPUT/'manifest.json').read_text())
assert all(previous[key] == lock[key] for key in lock), 'Runtime lock changed; run prepare-runtime.py'
requirements = tomllib.loads((REPO/'pyproject.toml').read_text())['project']['dependencies']+['setuptools>=77', 'wheel']
assert requirements == lock['python_requirements'], 'Dependency lock must be updated before rebuilding'
assert json.loads((OUTPUT/'relocation-proof.json').read_text())['status'] == 'passed'
env = os.environ.copy()
for key in ('PYTHONPATH', 'PYTHONHOME', 'NODE_PATH'):
    env.pop(key, None)
env.update(PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_CACHE_DIR='1')
with tempfile.TemporaryDirectory(prefix='.refresh-', dir=OUTPUT.parent) as temporary:
    temporary = Path(temporary)
    stage = temporary/'bundle'
    shutil.copytree(OUTPUT, stage, symlinks=True)
    source = temporary/'source'; source.mkdir()
    shutil.copytree(REPO/'src', source/'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    for filename in ('pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(REPO/filename, source/filename)
    wheel_dir = temporary/'wheel'; wheel_dir.mkdir()
    py = stage/'python/bin/python3'
    run([py, '-I', '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation', '--wheel-dir', wheel_dir, source], env=env)
    wheels = list(wheel_dir.glob('briefloop-*.whl')); assert len(wheels) == 1
    run([py, '-I', '-m', 'pip', 'install', '--no-compile', '--no-index', '--no-deps', '--force-reinstall', wheels[0]], env=env)
    run([py, '-I', HERE/'runtime-metadata.py', stage], env=env)
    hostile = temporary/'workspace'; hostile.mkdir()
    (hostile/'briefloop.py').write_text("raise RuntimeError('WORKSPACE MODULE MUST NOT EXECUTE')\n")
    version = subprocess.check_output([str(py), '-I', '-m', 'briefloop', '--version'], cwd=hostile, env=env, text=True).strip()
    assert version == 'BriefLoop '+tomllib.loads((REPO/'pyproject.toml').read_text())['project']['version']
    module = subprocess.check_output([str(py), '-I', '-c', 'import briefloop; print(briefloop.__file__)'], cwd=hostile, env=env, text=True).strip()
    assert Path(module).resolve().is_relative_to((stage/'python').resolve())
    check = subprocess.check_output([str(py), '-I', '-m', 'pip', 'check'], env=env, text=True).strip()
    manifest = {**previous, 'briefloop_wheel': {'filename': wheels[0].name, 'sha256': helpers['sha'](wheels[0])},
                'source_version': version.removeprefix('BriefLoop '),
                'business_refreshed_at_utc': datetime.now(timezone.utc).isoformat(),
                'source_git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()}
    (stage/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    proof = {'status': 'passed', 'cli': version, 'pip_check': check, 'workspace_shadow_module_ignored': True,
             'wheel_sha256': manifest['briefloop_wheel']['sha256'], 'source_git_head': manifest['source_git_head'],
             'refreshed_at_utc': manifest['business_refreshed_at_utc'],
             'native_relocation_proof_sha256': helpers['sha'](stage/'relocation-proof.json'),
             'native_runtime_retested': False, 'model_calls': 0, 'http_servers_started': 0}
    (stage/'app-refresh-proof.json').write_text(json.dumps(proof, indent=2)+'\n')
    old = OUTPUT.with_name('.macos-arm64-previous')
    if old.exists():
        raise ValueError('Previous output backup remains; inspect it first')
    OUTPUT.rename(old)
    try:
        stage.rename(OUTPUT)
    except BaseException:
        old.rename(OUTPUT)
        raise
    shutil.rmtree(old)
print(json.dumps(proof, indent=2))
