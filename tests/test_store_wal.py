"""WAL only on SQLite builds with the WAL-reset fix; readers no longer stall the writer (#731)."""
import sqlite3
import time
import pytest

import briefloop.store as store_module
from briefloop.store import Store
from briefloop.workspaces import _workspace_id


def test_unpatched_build_keeps_the_rollback_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, 'wal_supported', lambda: False)
    assert Store(tmp_path).journal_mode == 'delete'


def test_unpatched_build_rejects_reopening_an_existing_wal_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, 'wal_supported', lambda: True)
    store = Store(tmp_path)
    store.event(None, 'preserved', {'text': 'saved before runtime change'})
    monkeypatch.setattr(store_module, 'wal_supported', lambda: False)
    with pytest.raises(ValueError, match='WAL.*SQLite'):
        Store(tmp_path)
    with sqlite3.connect(store.db) as c:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert c.execute("SELECT COUNT(*) FROM events WHERE kind='preserved'").fetchone()[0] == 1
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_unpatched_build_rejects_writes_if_an_open_workspace_switches_to_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, 'wal_supported', lambda: False)
    store = Store(tmp_path)
    with sqlite3.connect(store.db) as c:
        c.execute('PRAGMA journal_mode=WAL')
    with pytest.raises(ValueError, match='WAL.*SQLite'):
        store.event(None, 'blocked', {})
    with sqlite3.connect(store.db) as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE kind='blocked'").fetchone()[0] == 0
    # A patched runtime can still reopen and use the untouched workspace.
    monkeypatch.setattr(store_module, 'wal_supported', lambda: True)
    Store(tmp_path).event(None, 'after_runtime_update', {})


def test_wal_lets_a_write_commit_during_a_long_read_and_backups_see_it(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, 'wal_supported', lambda: True)
    store = Store(tmp_path)
    assert store.journal_mode == 'wal' and Store(tmp_path).journal_mode == 'wal'
    reader = sqlite3.connect(store.db)
    reader.execute('BEGIN'); reader.execute('SELECT COUNT(*) FROM events').fetchone()
    started = time.monotonic()
    store.event(None, 'during_read', {})
    assert time.monotonic() - started < 2  # a rollback journal waits for the reader here
    reader.execute('COMMIT'); reader.close()
    backup = tmp_path / 'backup.db'
    source, target = sqlite3.connect(store.db), sqlite3.connect(backup)
    source.backup(target); source.close(); target.close()
    copy = sqlite3.connect(backup)
    assert copy.execute("SELECT COUNT(*) FROM events WHERE kind='during_read'").fetchone()[0] == 1
    assert copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    copy.close()
    # Listing workspaces opens the file read-only; that still works in WAL.
    assert _workspace_id(tmp_path) == store.meta('workspace_id')
