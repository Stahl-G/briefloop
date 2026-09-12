"""Check the actual wheel/sdist notices after `python -m build`."""
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def check(archive: Path) -> None:
    if archive.suffix == '.whl':
        with zipfile.ZipFile(archive) as package:
            contents = {name: package.read(name) for name in package.namelist() if not name.endswith('/')}
        expected = {
            'briefloop/static/frontend-licenses.txt': 'src/briefloop/static/frontend-licenses.txt',
            'briefloop/static/app.js': 'src/briefloop/static/app.js',
            'briefloop/static/runtime-bridge.LICENSE.txt': 'src/briefloop/static/runtime-bridge.LICENSE.txt',
            'briefloop/static/runtime-bridge.NOTICE.txt': 'src/briefloop/static/runtime-bridge.NOTICE.txt',
            'wikiskill/_licenses/LICENSE': 'src/wikiskill/_licenses/LICENSE',
            'wikiskill/_licenses/NOTICE.md': 'src/wikiskill/_licenses/NOTICE.md',
        }
        metadata_path = next(name for name in contents if name.endswith('.dist-info/METADATA'))
        metadata = contents[metadata_path].decode()
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
            assert f'License-File: {name}' in metadata.splitlines(), (archive, name, 'missing license metadata')
            expected[metadata_path.removesuffix('METADATA') + 'licenses/' + name] = name
    else:
        with tarfile.open(archive) as package:
            contents = {member.name.split('/', 1)[1]: package.extractfile(member).read()
                        for member in package.getmembers() if member.isfile() and '/' in member.name}
        names = ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'KNOWN_ISSUES.md', 'SECURITY.md', 'CONTRIBUTING.md', 'package.json', 'package-lock.json',
                 'scripts/build_frontend.mjs', 'scripts/build_frontend_licenses.mjs',
                 'src/briefloop/static/frontend-licenses.txt', 'src/briefloop/static/app.js',
                 'src/briefloop/static/runtime-bridge.LICENSE.txt', 'src/briefloop/static/runtime-bridge.NOTICE.txt',
                 'src/wikiskill/_licenses/LICENSE', 'src/wikiskill/_licenses/NOTICE.md']
        expected = {name: name for name in names}
    for asset in (ROOT / 'src/briefloop/workflow_assets').rglob('*'):
        if asset.is_file():
            local = asset.relative_to(ROOT).as_posix()
            expected[local.removeprefix('src/') if archive.suffix == '.whl' else local] = local
    for packaged, local in expected.items():
        assert packaged in contents, (archive, packaged, 'missing')
        assert contents[packaged] == (ROOT / local).read_bytes(), (archive, packaged, 'content drift')
    print(f'PASS: {archive.name} includes exact frontend and third-party notices')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python tests/check_distribution.py dist/*.whl dist/*.tar.gz')
    for argument in sys.argv[1:]:
        check(Path(argument))
