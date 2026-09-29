"""Synchronize version mirrors from pyproject.toml, including prerelease App names."""
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def desktop_version(version):
    match = re.fullmatch(r'(\d+\.\d+\.\d+)(?:(rc|a|b)(\d+)|\.dev(\d+))?', version)
    if not match:
        raise ValueError('Use a stable X.Y.Z or PEP 440 rc/a/b/dev version')
    base, kind, number, dev = match.groups()
    if kind:
        return f'{base}-' + {'rc': 'rc', 'a': 'alpha', 'b': 'beta'}[kind] + f'.{number}'
    return f'{base}-dev.{dev}' if dev else base


def sync(root=ROOT):
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    app_version = desktop_version(version)
    (root / 'VERSION').write_text(version + '\n')
    path = root / 'src/briefloop/__init__.py'
    path.write_text(re.sub(r'(__version__\s*=\s*)[\'"][^\'\"]+[\'"]', lambda m: m[1] + f'"{version}"', path.read_text()))
    for name in ('package.json', 'package-lock.json'):
        path = root / 'desktop/electron' / name
        value = json.loads(path.read_text())
        value['version'] = app_version
        if name == 'package-lock.json':
            value['packages']['']['version'] = app_version
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    path = root / 'src/briefloop/static/index.html'
    path.write_text(re.sub(r'data-web-version="[^"]+"', f'data-web-version="{version}"', path.read_text()))
    print(f'Python/Web: {version}; App: {app_version}')


if __name__ == '__main__':
    sync()
