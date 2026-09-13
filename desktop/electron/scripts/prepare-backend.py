#!/usr/bin/env python3
"""Build the App backend wheel without bundling Python or standalone Node."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / 'desktop' / 'electron'


def main() -> None:
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    build_env = DESKTOP / '.cache' / 'desktop-wheel-build'
    python = build_env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python3')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(build_env)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PYTHON', 'PIP_'))}
    env['PIP_CONFIG_FILE'] = os.devnull
    pip = [str(python), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', '--no-input']
    subprocess.run([*pip, 'install', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
                    *project['build-system']['requires']], env=env, check=True)
    with tempfile.TemporaryDirectory(prefix='briefloop-backend-build-') as temporary:
        stage = Path(temporary)
        source = stage / 'source'
        source.mkdir()
        shutil.copytree(ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info', '.DS_Store'))
        for filename in ('pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT / filename, source / filename)
        wheels = stage / 'wheels'
        subprocess.run([*pip, 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', str(wheels), str(source)],
                       env=env, check=True, cwd=stage)
        files = list(wheels.glob('briefloop-*.whl'))
        if len(files) != 1:
            raise RuntimeError('Expected exactly one BriefLoop wheel')
        wheel = files[0]
        with zipfile.ZipFile(wheel) as archive:
            for name in ('briefloop/static/runtime-bridge.mjs', 'briefloop/static/app.js', 'wikiskill/__init__.py'):
                if name not in archive.namelist():
                    raise RuntimeError(f'Wheel missing required resource: {name}')
        output = DESKTOP / 'backend'
        output.mkdir(exist_ok=True)
        manifest = {'version': project['project']['version'], 'wheel': wheel.name,
                    'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
        shutil.copy2(wheel, output / wheel.name)
        pending = output / 'manifest.pending'
        pending.write_text(json.dumps(manifest, indent=2) + '\n')
        pending.replace(output / 'manifest.json')
        for old in output.glob('briefloop-*.whl'):
            if old.name != wheel.name:
                old.unlink()
        print(json.dumps({**manifest, 'bytes': wheel.stat().st_size, 'output': str(output)}, indent=2))


if __name__ == '__main__':
    main()
