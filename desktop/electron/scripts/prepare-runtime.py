#!/usr/bin/env python3
"""Build an offline-at-launch macOS arm64 runtime from checksum-pinned artifacts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent
REPO = DESKTOP.parents[1]
CACHE = DESKTOP/'.cache'
OUTPUT = DESKTOP/'runtime/macos-arm64'


def run(args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(item):
    filename = item['filename']
    if Path(filename).name != filename:
        raise ValueError('Unsafe download filename')
    target = CACHE/filename
    if target.exists() and sha(target) == item['sha256']:
        return target
    temporary = target.with_suffix(target.suffix+'.part')
    run(['curl', '--fail', '--location', '--silent', '--show-error', '--max-time', '180',
         '--proto', '=https', '--proto-redir', '=https', item['url'], '-o', temporary])
    if sha(temporary) != item['sha256']:
        temporary.unlink()
        raise ValueError('Download checksum mismatch: '+filename)
    temporary.replace(target)
    return target


def unpack(archive, destination):
    with tarfile.open(archive) as package:
        # data rejects paths/symlinks escaping destination and special device files.
        package.extractall(destination, filter='data')


def python_licenses(archive, destination):
    destination.mkdir(parents=True)
    names = subprocess.check_output(['tar', '-tf', str(archive)], text=True).splitlines()
    selected = [name for name in names if name == 'python/PYTHON.json' or name.startswith('python/licenses/')]
    if 'python/PYTHON.json' not in selected or not any(name.endswith('.txt') for name in selected):
        raise ValueError('Python distribution licenses missing')
    for name in selected:
        relative = Path(name).relative_to('python')
        if '..' in relative.parts:
            raise ValueError('Unsafe license path')
    run(['tar', '-xf', archive, '-C', destination, *selected])


def prepare():
    if sys.version_info < (3, 11) or platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('Build on macOS arm64 with Python 3.11+; cross-platform runtimes are not provided.')
    lock = json.loads((HERE/'runtime-lock.json').read_text())
    requirements = tomllib.loads((REPO/'pyproject.toml').read_text())['project']['dependencies']+['setuptools>=77', 'wheel']
    if requirements != lock['python_requirements']:
        raise SystemExit('Python dependencies changed: refresh/review runtime-lock.json before building.')
    CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    archives = {key: fetch(lock[key]) for key in ('python', 'node', 'python_licenses')}
    with ThreadPoolExecutor(max_workers=4) as pool:
        wheels = list(pool.map(fetch, lock['python_wheels']))
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=OUTPUT.parent) as temporary:
        stage = Path(temporary)/'bundle'; stage.mkdir()
        unpack(archives['python'], stage)
        unpack(archives['node'], stage)
        (stage/('node-v'+lock['node']['version']+'-darwin-arm64')).rename(stage/'node')
        # The app needs the Node executable only, not npm/corepack or C headers.
        node = stage/'node'
        for item in list(node.iterdir()):
            if item.name not in ('bin', 'LICENSE'):
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        for item in (node/'bin').iterdir():
            if item.name != 'node':
                item.unlink()
        py = stage/'python/bin/python3'
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'NODE_PATH'):
            env.pop(key, None)
        env.update(PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_CACHE_DIR='1')
        wheelhouse = Path(temporary)/'wheels'; wheelhouse.mkdir()
        for wheel in wheels:
            shutil.copy2(wheel, wheelhouse/wheel.name)
        requirements_file = Path(temporary)/'requirements.txt'
        requirements_file.write_text(''.join(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n" for item in lock['python_wheels']))
        run([py, '-I', '-m', 'pip', 'install', '--no-compile', '--no-index', '--find-links', wheelhouse,
             '--require-hashes', '-r', requirements_file], env=env)
        # Always build from a fresh copy of this checkout, including its current
        # uncommitted product fixes. No editable install or cached business wheel.
        source = Path(temporary)/'source'; source.mkdir()
        shutil.copytree(REPO/'src', source/'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
        for filename in ('pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(REPO/filename, source/filename)
        built = Path(temporary)/'business-wheel'; built.mkdir()
        run([py, '-I', '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
             '--wheel-dir', built, source], env=env)
        business = list(built.glob('briefloop-*.whl'))
        if len(business) != 1:
            raise ValueError('Expected exactly one current BriefLoop wheel')
        run([py, '-I', '-m', 'pip', 'install', '--no-compile', '--no-index', '--no-deps', business[0]], env=env)
        run([py, '-I', '-m', 'pip', 'check'], env=env)
        license_dir = stage/'licenses'; license_dir.mkdir()
        python_licenses(archives['python_licenses'], license_dir/'python-distribution')
        shutil.copy2(node/'LICENSE', license_dir/'NODE-LICENSE.txt')
        # The installed RECORD and metadata remain alongside their distributions;
        # this helper also gathers every license/notice and rewrites console entry
        # points as relative launchers so moving the app never retains build paths.
        run([py, '-I', HERE/'runtime-metadata.py', stage], env=env)
        manifest = {**lock, 'briefloop_wheel': {'filename': business[0].name, 'sha256': sha(business[0])},
                    'source_version': tomllib.loads((REPO/'pyproject.toml').read_text())['project']['version']}
        (stage/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        run([sys.executable, HERE/'verify-runtime.py', stage], env=env)
        # Replace only this script's generated output after successful verification.
        old = OUTPUT.with_name('.macos-arm64-previous')
        if old.exists():
            raise ValueError('Previous build backup remains; inspect it before rebuilding')
        if OUTPUT.exists():
            OUTPUT.rename(old)
        try:
            stage.rename(OUTPUT)
        except BaseException:
            if old.exists():
                old.rename(OUTPUT)
            raise
        if old.exists():
            shutil.rmtree(old)
    print('Prepared runtime:', OUTPUT, flush=True)


if __name__ == '__main__':
    prepare()
