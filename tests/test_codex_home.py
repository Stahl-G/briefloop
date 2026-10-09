"""History isolation uses synthetic configuration, never the developer's login."""
from pathlib import Path
from uuid import uuid4
import os
import pytest
from briefloop.codex_home import CodexHome


def fixture_home(tmp_path):
    source=tmp_path/'personal';source.mkdir()
    (source/'config.toml').write_text('model="user-choice"\nsqlite_home="/old-state"\n')
    (source/'auth.json').write_text('synthetic-auth-canary')
    try:
        (tmp_path/'probe-link').symlink_to(source/'config.toml')
    except OSError:
        pytest.skip('This host cannot create file symlinks; explicit private home remains supported')
    return source, CodexHome(environ={'CODEX_HOME':str(source),'CODEX_SQLITE_HOME':'/old-state'},state_root=tmp_path/'private')


def test_history_is_private_but_file_login_and_config_are_shared(tmp_path):
    source, home=fixture_home(tmp_path)
    assert home.root != source and not home.root.is_relative_to(source)
    assert (home.root/'auth.json').is_symlink()
    assert (home.root/'config.toml').resolve()==source/'config.toml'
    assert home.env['CODEX_HOME']==home.env['CODEX_SQLITE_HOME']==str(home.root)
    assert 'sqlite_home=' in home.overrides[1]
    # Credentials replaced by the original CLI stay reachable, without copying.
    replacement=source/'refreshed';replacement.write_text('synthetic-refreshed-canary')
    os.replace(replacement, source/'auth.json')
    assert (home.root/'auth.json').read_text()=='synthetic-refreshed-canary'
    assert not (home.root/'sessions').exists()
    again=CodexHome(environ={'CODEX_HOME':str(source)},state_root=tmp_path/'private')
    assert again.root==home.root
    (source/'sessions').mkdir()
    (home.root/'sessions').symlink_to(source/'sessions', target_is_directory=True)
    with pytest.raises(RuntimeError,match='链接指向'):
        CodexHome(environ={'CODEX_HOME':str(source)},state_root=tmp_path/'private')


@pytest.mark.parametrize('archived', [False, True])
def test_import_only_bound_id_preserves_original_and_private_progress(tmp_path, archived):
    source, home=fixture_home(tmp_path)
    identity=str(uuid4());other=str(uuid4())
    folder=source/'archived_sessions' if archived else source/'sessions'/'2026'/'10'/'09'
    folder.mkdir(parents=True)
    original=folder/f'rollout-2026-10-09T00-00-00-{identity}.jsonl'
    original.write_bytes(b'synthetic private conversation\n')
    (folder/f'rollout-{other}.jsonl').write_bytes(b'unrelated\n')
    home.import_bound_thread(identity)
    copied=next((home.root/'sessions').rglob('*.jsonl'))
    assert copied.parent==home.root/'sessions'/'2026'/'10'/'09'
    assert copied.read_bytes()==original.read_bytes()
    copied.write_bytes(b'new private progress\n')
    home.import_bound_thread(identity)
    assert copied.read_bytes()==b'new private progress\n'
    assert original.read_bytes()==b'synthetic private conversation\n'
    assert len(list((home.root/'sessions').rglob('*.jsonl')))==1
    with pytest.raises(ValueError):home.import_bound_thread('../*')


def test_no_shared_history_fallback_or_credential_overwrite(tmp_path):
    source=tmp_path/'personal';source.mkdir()
    (source/'config.toml').write_text('cli_auth_credentials_store="keyring"')
    with pytest.raises(RuntimeError,match='密钥链'):
        CodexHome(environ={'CODEX_HOME':str(source)},state_root=tmp_path/'private')
    with pytest.raises(RuntimeError,match='不能位于'):
        CodexHome(environ={'CODEX_HOME':str(source),'BRIEFLOOP_CODEX_HOME':str(source)})
    explicit=tmp_path/'configured-private';explicit.mkdir()
    (explicit/'config.toml').write_text('model="separately-configured"')
    home=CodexHome(environ={'CODEX_HOME':str(source),'BRIEFLOOP_CODEX_HOME':str(explicit)})
    assert home.root==explicit and not (explicit/'auth.json').exists()
    assert (explicit/'config.toml').read_text()=='model="separately-configured"'


def test_live_old_thread_is_not_imported(tmp_path,monkeypatch):
    import briefloop.codex_home as module
    source, home=fixture_home(tmp_path)
    identity=str(uuid4());folder=source/'archived_sessions';folder.mkdir()
    original=folder/f'rollout-2026-10-09T00-00-00-{identity}.jsonl';original.write_bytes(b'synthetic\n')
    copy=module.shutil.copyfileobj
    def racing_read(src,dst):
        copy(src,dst)
        with original.open('ab') as writer:writer.write(b'active writer\n')
    monkeypatch.setattr(module.shutil,'copyfileobj',racing_read)
    with pytest.raises(RuntimeError,match='仍在写入'):home.import_bound_thread(identity)
    assert not list((home.root/'sessions').rglob('*.jsonl'))
