"""Release bytes may be reused, never silently replaced under the same version."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys

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


def test_prerelease_identity_is_preserved_across_python_and_electron():
    assert versions.desktop_version('1.2.3rc2') == '1.2.3-rc.2'
    assert versions.desktop_version('1.2.3.dev7') == '1.2.3-dev.7'
    assert checks.compare('1.2.3rc2', {'python': '1.2.3rc2', 'app': '1.2.3-rc.2'})['status'] == 'match'
    assert checks.compare('1.2.3', {'app': '1.2.3-rc.2'})['status'] == 'mismatch'
