"""Guard the portable diagnostic's non-mutation and benchmark output ownership."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('store_sqlite_benchmark', Path(__file__).parents[1] / 'scripts' / 'benchmark_store_sqlite.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_diagnostic_never_initializes_or_migrates_database(tmp_path, monkeypatch):
    database = tmp_path / 'existing.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE user_marker(value TEXT)')
        connection.execute("INSERT INTO user_marker VALUES('synthetic private marker')")
    before = (hashlib.sha256(database.read_bytes()).hexdigest(), database.stat().st_mtime_ns, sorted(p.name for p in tmp_path.iterdir()))
    monkeypatch.setattr(benchmark, 'load_store', lambda: pytest.fail('Read-only diagnostic must not load Store'))
    result = benchmark.diagnose(database)
    assert result['runtime']['sqlite_runtime'] == sqlite3.sqlite_version
    assert result['runtime']['wal_patch_assessment'] == 'not_assessed'
    assert result['database']['persistent_journal_family'] == 'rollback'
    assert result['database']['journal_mode'] is None
    assert result['database']['journal_mode_query'] == 'not_executed'
    assert 'synthetic private marker' not in str(result)
    assert before == (hashlib.sha256(database.read_bytes()).hexdigest(), database.stat().st_mtime_ns, sorted(p.name for p in tmp_path.iterdir()))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('user_marker',)]
    missing = tmp_path / 'absent' / 'database.db'
    with pytest.raises(FileNotFoundError):
        benchmark.diagnose(missing)
    assert not missing.parent.exists()


def test_results_are_exclusive_and_percentiles_are_recomputable(tmp_path, monkeypatch):
    target = tmp_path / 'existing-result'
    target.mkdir()
    marker = target / 'marker.txt'
    marker.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(benchmark, 'load_store', lambda: pytest.fail('Existing output must fail before running Store'))
    with pytest.raises(FileExistsError):
        benchmark.run_benchmark(target, processes=(1,), cycles=1)
    assert marker.read_text(encoding='utf-8') == 'keep'
    with pytest.raises(FileExistsError):
        benchmark.save_new(marker, {'overwrite': True})
    result = benchmark.stats([n / 100 for n in range(1, 21)])
    assert result == {'n': 20, 'p50_ms': pytest.approx(105), 'p95_ms': pytest.approx(190), 'max_ms': 200}
    assert benchmark.error_info(sqlite3.OperationalError('database is locked'))['lock_failure'] is True
    assert benchmark.error_info(ValueError('synthetic fixture invalid'))['lock_failure'] is False


def test_child_import_failure_keeps_explanation(tmp_path, monkeypatch):
    def unavailable():
        raise ModuleNotFoundError('synthetic missing dependency')
    monkeypatch.setattr(benchmark, 'load_store', unavailable)
    target = tmp_path / 'process.json'
    with pytest.raises(SystemExit) as stopped:
        benchmark.store_child(tmp_path, 0, 1, None, target)
    assert stopped.value.code == 1
    row = benchmark.read_json(target)
    assert row['status'] == 'failed'
    assert row['error']['type'] == 'ModuleNotFoundError'
    assert row['error']['message'] == 'synthetic missing dependency'
    assert not (tmp_path / 'briefloop.db').exists()


def test_wal_header_is_observed_without_opening_database(tmp_path):
    database = tmp_path / 'wal-header.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE marker(value TEXT)')
    # A header-only fixture proves the diagnostic never asks SQLite to open WAL.
    # It does not run/enable WAL on the actual runtime or pretend to validate WAL safety.
    content = bytearray(database.read_bytes())
    content[18:20] = bytes([2, 2])
    database.write_bytes(content)
    before = database.read_bytes()
    result = benchmark.diagnose(database)
    assert result['database']['persistent_journal_family'] == 'wal'
    assert result['database']['journal_mode_query'] == 'not_executed'
    assert list(tmp_path.iterdir()) == [database]
    assert database.read_bytes() == before


def test_logical_snapshot_detects_changed_text_with_unchanged_stored_hash(tmp_path):
    database = tmp_path / 'briefloop.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE briefs(id TEXT, markdown TEXT, hash TEXT)')
        connection.execute("INSERT INTO briefs VALUES('fixture','Original body','unchanged hash column')")
    before = benchmark.logical_snapshot(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE briefs SET markdown='WRONG RESTORED BODY'")
    after = benchmark.logical_snapshot(tmp_path)
    assert before['tables']['briefs']['count'] == after['tables']['briefs']['count']
    assert before['content_sha256'] != after['content_sha256']


def test_cleanup_escalates_only_owned_stubborn_child():
    class Child:
        pid = 123
        def __init__(self): self.alive = True; self.calls = []
        def is_alive(self): return self.alive
        def terminate(self): self.calls.append('terminate')
        def join(self, timeout): self.calls.append('join')
        def kill(self): self.calls.append('kill'); self.alive = False
    child = Child()
    assert benchmark.stop_owned([child]) == {'terminated_owned_processes': [0], 'killed_owned_processes': [0]}
    assert child.calls == ['terminate', 'join', 'kill', 'join']
    assert not child.is_alive()
