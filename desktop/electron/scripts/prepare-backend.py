#!/usr/bin/env python3
"""Build the App backend wheel without bundling Python or standalone Node."""
from __future__ import annotations

import argparse
import email
import io
import re
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


def frozen_source(commit: str, root: Path = ROOT) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise ValueError("Use an explicit frozen 40-character --release-commit")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    if git("rev-parse", "HEAD") != commit:
        raise ValueError("Release commit differs from checked-out source")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Freeze a clean checkout before preparing a stable release")


def verify_wheel(wheel: Path, version: str, expected_hash: str | None = None) -> str:
    if wheel.name != f'briefloop-{version}-py3-none-any.whl':
        raise ValueError('Unexpected shared wheel filename')
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError("Shared release wheel SHA-256 mismatch")
    with zipfile.ZipFile(wheel) as archive:
        metadata = [n for n in archive.namelist() if n.startswith('briefloop-') and n.endswith('.dist-info/METADATA')]
        if len(metadata) != 1 or email.message_from_bytes(archive.read(metadata[0]))['Version'] != version:
            raise ValueError("Wheel version differs from pyproject.toml")
        for name in ('briefloop/static/runtime-bridge.mjs', 'briefloop/static/app.js', 'wikiskill/__init__.py'):
            if name not in archive.namelist():
                raise ValueError(f"Wheel missing required resource: {name}")
        for name in archive.namelist():
            if name.endswith('/') or not name.startswith(('briefloop/', 'wikiskill/')):
                continue
            source = (ROOT / 'src' / name).resolve()
            if not source.is_relative_to((ROOT / 'src').resolve()) or not source.is_file():
                raise ValueError(f"Wheel resource not present in frozen source: {name}")
            if archive.read(name) != source.read_bytes():
                raise ValueError(f"Wheel differs from frozen source: {name}; use canonical Git line endings")
    return digest


def stage_wheel(wheel: Path, manifest: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    existing = output / 'manifest.json'
    if existing.exists():
        previous = json.loads(existing.read_text(encoding='utf-8'))
        if previous.get('version') == manifest['version'] and previous != manifest:
            raise ValueError("Refusing different artifacts/provenance for the same version; use a new release or prerelease number")
    target = output / wheel.name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError("Refusing to overwrite a different same-name wheel")
    if wheel.resolve() != target.resolve():
        shutil.copy2(wheel, target)
    pending = output / 'manifest.pending'
    pending.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    pending.replace(existing)
    for old in output.glob('briefloop-*.whl'):
        if old.name == wheel.name:
            continue
        archive = output.parent / '.cache' / 'backend-history' / hashlib.sha256(old.read_bytes()).hexdigest()
        archive.mkdir(parents=True, exist_ok=True)
        saved = archive / old.name
        if not saved.exists():
            shutil.copy2(old, saved)
        if hashlib.sha256(saved.read_bytes()).digest() != hashlib.sha256(old.read_bytes()).digest():
            raise ValueError('Previous backend backup verification failed')
        old.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit', help='Full clean frozen commit; required for stable versions')
    parser.add_argument('--artifact-dir', type=Path, help='Shared immutable build registry (one directory per version)')
    parser.add_argument('--wheel', type=Path, help='Stage an existing shared wheel; never rebuild')
    parser.add_argument('--sha256', help='Required SHA-256 when staging a shared wheel')
    args = parser.parse_args()
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    version = project['project']['version']
    stable = bool(re.fullmatch(r'\d+\.\d+\.\d+', version))
    if stable and not args.release_commit:
        parser.error('Stable versions require --release-commit and a single shared artifact. Local candidates must use an rc/dev version.')
    if args.release_commit:
        frozen_source(args.release_commit)
    output = DESKTOP / 'backend'
    identity = {'version': version, 'source_commit': args.release_commit,
                'channel': 'release' if stable else 'prerelease'}
    if args.wheel:
        if not re.fullmatch(r'[0-9a-f]{64}', args.sha256 or ''):
            parser.error('--wheel requires an explicit --sha256')
        digest = verify_wheel(args.wheel, version, args.sha256)
        manifest = {**identity, 'wheel': args.wheel.name, 'sha256': digest}
        stage_wheel(args.wheel, manifest, output)
        print(json.dumps(manifest, indent=2))
        return
    if args.sha256:
        parser.error('--sha256 requires --wheel')
    if not args.artifact_dir:
        parser.error('Build once with --artifact-dir; subsequent platforms must use --wheel and --sha256')
    registry = args.artifact_dir.resolve() / version
    registry.mkdir(parents=True, exist_ok=True)
    claim = registry / 'build-identity.json'
    if claim.exists():
        if json.loads(claim.read_text()) != identity:
            raise ValueError('This version is already reserved for another source; use a new version')
        recorded = registry / 'backend-manifest.json'
        if not recorded.exists():
            raise ValueError('Previous build is incomplete; preserve and inspect it instead of rebuilding the same version')
        manifest = json.loads(recorded.read_text())
        wheel = registry / manifest['wheel']
        verify_wheel(wheel, version, manifest['sha256'])
        stage_wheel(wheel, manifest, output)
        print(json.dumps({**manifest, 'reused': True}, indent=2))
        return
    with claim.open('x', encoding='utf-8') as stream:
        json.dump(identity, stream, indent=2)
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
        inputs = ('src', 'pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
        if args.release_commit:
            # Build the committed tree, never ignored files or mutable worktree bytes.
            data = subprocess.check_output(['git', 'archive', '--format=zip', args.release_commit, *inputs], cwd=ROOT)
            with zipfile.ZipFile(io.BytesIO(data)) as snapshot:
                snapshot.extractall(source)
        else:
            shutil.copytree(ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info', '.DS_Store'))
            for filename in inputs[1:]:
                shutil.copy2(ROOT / filename, source / filename)
        wheels = stage / 'wheels'
        subprocess.run([*pip, 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', str(wheels), str(source)],
                       env=env, check=True, cwd=stage)
        files = list(wheels.glob('briefloop-*.whl'))
        if len(files) != 1:
            raise RuntimeError('Expected exactly one BriefLoop wheel')
        wheel = files[0]
        digest = verify_wheel(wheel, version)
        if args.release_commit:
            frozen_source(args.release_commit)
        manifest = {**identity, 'wheel': wheel.name, 'sha256': digest}
        # Registry is immutable: no regeneration of the same version, even if ZIP timestamps differ.
        with (registry / wheel.name).open('xb') as stream:
            stream.write(wheel.read_bytes())
        with (registry / 'backend-manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
        stage_wheel(registry / wheel.name, manifest, output)
        print(json.dumps({**manifest, 'bytes': wheel.stat().st_size, 'output': str(output)}, indent=2))


if __name__ == '__main__':
    main()
