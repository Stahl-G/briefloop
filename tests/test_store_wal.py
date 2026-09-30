"""WAL only on SQLite builds with the WAL-reset fix; readers no longer stall the writer (#731)."""
import sqlite3
import time

import briefloop.store as store_module
from briefloop.store import Store, wal_supported
from briefloop.workspaces import _workspace_id


def test_only_patched_sqlite_builds_use_wal():
    assert wal_supported((3, 51, 3)) and wal_supported((3, 53, 1))
    assert wal_supported((3, 50, 7)) and wal_supported((3, 44, 6))
    assert not wal_supported((3, 50, 4)) and not wal_supported((3, 51, 2)) and not wal_supported((3, 45, 1))


def test_unpatched_build_keeps_the_rollback_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, 'wal_supported', lambda: False)
    assert Store(tmp_path).journal_mode == 'delete'


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
