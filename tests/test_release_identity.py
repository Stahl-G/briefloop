"""Release bytes may be reused, never silently replaced under the same version."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load(ROOT / 'desktop/electron/scripts/prepare-backend.py')
versions = load(ROOT / 'scripts/sync_versions.py')
checks = load(ROOT / 'scripts/check_versions.py')


def test_stage_reuses_identical_bytes_but_preserves_original_on_conflict(tmp_path):
    wheel = tmp_path / 'briefloop-1.2.3-py3-none-any.whl'
    wheel.write_bytes(b'first frozen artifact')
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest = dict(version='1.2.3', wheel=wheel.name, sha256=digest,
                    source_commit='a' * 40, channel='release')
    output = tmp_path / 'app/backend'
    backend.stage_wheel(wheel, manifest, output)
    backend.stage_wheel(wheel, manifest, output)
    wheel.write_bytes(b'new candidate using the same version')
    with pytest.raises(ValueError, match='same version'):
        backend.stage_wheel(wheel, {**manifest, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}, output)
    assert (output / wheel.name).read_bytes() == b'first frozen artifact'
    assert json.loads((output / 'manifest.json').read_text()) == manifest


def test_frozen_release_rejects_dirty_or_different_checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, text=True).strip()
    git('init', '-q')
    (tmp_path / 'source.txt').write_text('frozen')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'freeze')
    commit = git('rev-parse', 'HEAD')
    backend.frozen_source(commit, tmp_path)
    with pytest.raises(ValueError, match='differs'):
        backend.frozen_source('0' * 40, tmp_path)
    (tmp_path / 'source.txt').write_text('changed')
    with pytest.raises(ValueError, match='clean'):
        backend.frozen_source(commit, tmp_path)


def test_stable_backend_cannot_be_built_as_an_unpublished_local_candidate(tmp_path, monkeypatch, capsys):
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "1.2.3"\n')
    monkeypatch.setattr(backend, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['prepare-backend.py'])
    with pytest.raises(SystemExit):
        backend.main()
    assert 'Stable versions require' in capsys.readouterr().err


def test_dependency_lock_must_match_pyproject_and_ships_with_the_wheel(tmp_path):
    dependencies = ['pydantic>=2,<3', 'pypdf>=5']
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "1.2.3"\ndependencies = ' + json.dumps(dependencies) + '\n')
    lock_path = tmp_path / backend.LOCK
    lock_path.parent.mkdir(parents=True)
    digest = hashlib.sha256(json.dumps(sorted(dependencies)).encode()).hexdigest()
    lock_path.write_text(f'# header\n{backend.LOCK_MARKER}{digest}\npydantic==2.13.5 --hash=sha256:{"0" * 64}\n')
    lock = backend.frozen_lock(None, tmp_path)
    wheel = tmp_path / 'briefloop-1.2.3rc1-py3-none-any.whl'
    wheel.write_bytes(b'candidate')
    manifest = dict(version='1.2.3rc1', wheel=wheel.name, sha256=hashlib.sha256(b'candidate').hexdigest(),
                    source_commit=None, channel='prerelease', **backend.lock_identity(lock))
    backend.stage_wheel(wheel, manifest, tmp_path / 'backend', lock)
    assert (tmp_path / 'backend/requirements.txt').read_bytes() == lock
    with pytest.raises(ValueError, match='differs from the manifest'):
        backend.stage_wheel(wheel, manifest, tmp_path / 'other', lock + b'# edited\n')
    # A dependency change without regenerating the lock is refused before any build.
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "1.2.3"\ndependencies = ["pydantic>=2,<3"]\n')
    with pytest.raises(ValueError, match='lock-backend.py'):
        backend.frozen_lock(None, tmp_path)


def test_committed_dependency_lock_matches_pyproject():
    backend.frozen_lock(None, ROOT)


def test_prerelease_identity_is_preserved_across_python_and_electron():
    assert versions.desktop_version('1.2.3rc2') == '1.2.3-rc.2'
    assert versions.desktop_version('1.2.3.dev7') == '1.2.3-dev.7'
    assert checks.compare('1.2.3rc2', {'python': '1.2.3rc2', 'app': '1.2.3-rc.2'})['status'] == 'match'
    assert checks.compare('1.2.3', {'app': '1.2.3-rc.2'})['status'] == 'mismatch'


def test_windows_checkout_and_frozen_archive_keep_shared_wheel_bytes(tmp_path, monkeypatch):
    origin = tmp_path / '共享 冻结库'
    origin.mkdir()

    def git(directory, *args):
        return subprocess.check_output(['git', *args], cwd=directory, text=True).strip()

    git(origin, 'init', '-q')
    inputs = {
        'src/briefloop/__init__.py': b'__version__ = "1.2.3"\n',
        'src/briefloop/static/app.js': b'console.log("shared");\n',
        'src/briefloop/static/runtime-bridge.mjs': b'export const version = "shared";\n',
        'src/briefloop/static/tokens.css': b':root { --shared: blue; }\n',
        'src/briefloop/static/runtime-briefloop.svg': b'<svg>\n</svg>\n',
        'src/wikiskill/__init__.py': b'"""Shared package."""\n',
        'src/briefloop/static/sample.png': b'\x89PNG\r\n\x1a\n\x00\xffbinary\r\n',
        'pyproject.toml': b'[project]\nname = "briefloop"\nversion = "1.2.3"\n',
        'README.md': b'# Frozen README\n',
        'LICENSE': b'Frozen license\n',
        'THIRD_PARTY_NOTICES.md': b'# Frozen notices\n',
    }
    desktop_copies = {
        'desktop/electron/assets/ui-tokens.css': inputs['src/briefloop/static/tokens.css'],
        'desktop/electron/assets/briefloop-mark.svg': inputs['src/briefloop/static/runtime-briefloop.svg'],
    }
    (origin / '.gitattributes').write_bytes((ROOT / '.gitattributes').read_bytes())
    for name, data in {**inputs, **desktop_copies}.items():
        target = origin / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(origin, '-c', 'core.autocrlf=false', 'add', '.')
    git(origin, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'freeze')
    commit = git(origin, 'rev-parse', 'HEAD')
    checkout = tmp_path / 'Windows 检出'
    git(tmp_path, 'clone', '-q', '--no-local', '-c', 'core.autocrlf=true', str(origin), str(checkout))
    assert git(checkout, 'config', '--get', 'core.autocrlf') == 'true'
    backend.frozen_source(commit, checkout)
    for name, data in inputs.items():
        if name.startswith('src/'):
            assert (checkout / name).read_bytes() == data
    for name, data in desktop_copies.items():
        assert (checkout / name).read_bytes() == data
    with zipfile.ZipFile(io.BytesIO(backend.frozen_archive(commit, checkout))) as archive:
        for name, data in inputs.items():
            assert archive.read(name) == data
    wheel = tmp_path / 'briefloop-1.2.3-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('briefloop-1.2.3.dist-info/METADATA', 'Metadata-Version: 2.1\nName: briefloop\nVersion: 1.2.3\n')
        for name, data in inputs.items():
            if name.startswith('src/'):
                archive.writestr(name[4:], data)
    monkeypatch.setattr(backend, 'ROOT', checkout)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert backend.verify_wheel(wheel, '1.2.3', digest) == digest
    (checkout / 'src/wikiskill/__init__.py').write_bytes(b'changed source\n')
    with pytest.raises(ValueError, match='differs from frozen source'):
        backend.verify_wheel(wheel, '1.2.3', digest)
