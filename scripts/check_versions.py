"""Compare source and actual distribution versions; missing evidence is not a pass."""
import argparse
import ast
import email
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tomllib
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ('cli', 'mac', 'windows', 'pypi')


def wheel_version(path):
    with ZipFile(path) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA') and n.startswith('briefloop-'))
        return email.message_from_bytes(archive.read(metadata))['Version']


def backend_versions(resources, expected_hash=None):
    base = resources / 'backend'
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    wheel = (base / manifest['wheel']).resolve()
    if wheel.parent != base.resolve():
        raise ValueError('Invalid backend wheel path')
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Backend wheel hash differs from manifest')
    if expected_hash and manifest['sha256'] != expected_hash:
        raise ValueError('Desktop backend differs from shared release wheel')
    return {'backend_manifest': manifest['version'], 'backend_wheel': wheel_version(wheel)}


def source_versions(root):
    tree = ast.parse((root / 'src/briefloop/__init__.py').read_text(encoding='utf-8'))
    python_version = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
    desktop = json.loads((root / 'desktop/electron/package.json').read_text(encoding='utf-8'))
    lock = json.loads((root / 'desktop/electron/package-lock.json').read_text(encoding='utf-8'))
    html = (root / 'src/briefloop/static/index.html').read_text(encoding='utf-8')
    return {'version_file': (root / 'VERSION').read_text(encoding='utf-8').strip(), 'python': python_version, 'desktop_mac_windows': desktop['version'],
            'desktop_lock': lock['version'], 'desktop_lock_root': lock['packages']['']['version'],
            'web': re.search(r'data-web-version="([^"]+)"', html).group(1)}


def compare(expected, values):
    return {'status': 'match' if values and all(v == expected for v in values.values()) else 'mismatch', 'versions': values}


def check(args):
    root = args.root.resolve()
    expected = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    release_hash = hashlib.sha256(args.wheel.read_bytes()).hexdigest() if args.wheel else None
    results = {'source': compare(expected, source_versions(root))}
    results.update({name: {'status': 'unverified', 'reason': 'No artifact or channel requested'} for name in CHANNELS})

    def capture(name, read):
        try:
            results[name] = compare(expected, read())
        except Exception as error:
            results[name] = {'status': 'error', 'reason': str(error)}

    if args.cli:
        def cli():
            value = subprocess.check_output([args.cli, '--version'], text=True, stderr=subprocess.PIPE).strip()
            match = re.fullmatch(r'(?:BriefLoop\s+)?v?(\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*))', value, re.I)
            if not match:
                raise ValueError('CLI did not return a version')
            return {'runtime': match.group(1)}
        capture('cli', cli)
    if args.mac_app:
        def mac():
            contents = args.mac_app / 'Contents'
            with (contents / 'Info.plist').open('rb') as stream:
                version = plistlib.load(stream)['CFBundleShortVersionString']
            return {'app': version, **backend_versions(contents / 'Resources', release_hash)}
        capture('mac', mac)
        if results['mac']['status']=='match' and release_hash:results['mac']['release_wheel_sha256']=release_hash
    if args.windows_app:
        def windows():
            if os.name != 'nt':
                raise ValueError('Windows EXE version must be inspected on Windows')
            exe = args.windows_app.resolve()
            # Literal path and no executable invocation: inspect PE version metadata.
            literal = str(exe).replace("'", "''")
            command = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; (Get-Item -LiteralPath '{literal}').VersionInfo.ProductVersion"
            version = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', command], text=True, encoding='utf-8').strip()
            return {'app': version, **backend_versions(exe.parent / 'resources', release_hash)}
        capture('windows', windows)
        if results['windows']['status']=='match' and release_hash:results['windows']['release_wheel_sha256']=release_hash
    if args.wheel:
        capture('wheel', lambda: {'metadata': wheel_version(args.wheel)})
    if args.pypi:
        def pypi():
            with urlopen(Request('https://pypi.org/pypi/briefloop/json', headers={'User-Agent': 'BriefLoop-version-check'}), timeout=20) as response:
                data = json.load(response)
            if args.wheel:
                digest = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
                candidates = data.get('releases', {}).get(expected, [])
                if not any(f['filename'] == args.wheel.name and f['digests']['sha256'] == digest and not f.get('yanked') for f in candidates):
                    raise ValueError('PyPI wheel missing, yanked, or hash differs from local release')
            return {'latest': data['info']['version']}
        capture('pypi', pypi)
    for report_path in args.platform_report:
        report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        if report.get('expected') != expected:
            raise ValueError('Platform report belongs to another release: ' + str(report_path))
        for name in CHANNELS:
            value = report.get('checks', {}).get(name, {})
            if results[name]['status'] == 'unverified' and value.get('versions'):
                if value.get('status') != 'match':
                    results[name] = {**value, 'evidence': str(report_path)}
                elif release_hash and name in ('mac', 'windows') and value.get('release_wheel_sha256') != release_hash:
                    results[name] = {'status':'error', 'reason':'Platform evidence does not verify the shared release wheel', 'evidence':str(report_path)}
                else:
                    results[name] = {**value, **compare(expected, value['versions']), 'evidence': str(report_path)}
    consistent = bool(release_hash) and all(results[name]['status'] == 'match' for name in CHANNELS) and results['source']['status'] == 'match'
    failed = any(value['status'] in ('mismatch', 'error') for value in results.values())
    if args.require_all and not consistent:
        failed = True
    if args.require_all and not release_hash:
        consistent=False;failed=True
        results['wheel']={'status':'unverified','reason':'A shared release wheel is required for all-platform verification'}
    return {'release_wheel_sha256':release_hash, 'expected': expected, 'expected_from': 'pyproject.toml', 'all_platforms_consistent': consistent,
            'checks': results}, int(failed)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--cli', help='Actual installed briefloop executable')
    parser.add_argument('--mac-app', type=Path, help='Installed or unpacked .app')
    parser.add_argument('--windows-app', type=Path, help='Installed or unpacked BriefLoop.exe, run on Windows')
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--pypi', action='store_true', help='Read live PyPI and optionally compare wheel hash')
    parser.add_argument('--platform-report', type=Path, action='append', default=[], help='Combine JSON checks captured on another native platform')
    parser.add_argument('--require-all', action='store_true', help='Fail on any missing platform evidence')
    args = parser.parse_args()
    result, status = check(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(status)


if __name__ == '__main__':
    main()
