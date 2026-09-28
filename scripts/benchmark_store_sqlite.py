"""Portable ControlStore benchmark and read-only SQLite runtime diagnostic.

Only ``run`` creates Store instances, under a new output directory owned by this
invocation. ``diagnose`` reads only database header bytes, without a DB connection or Store initialization.
No model calls, production workspace writes, journal changes, or Worker emulation.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time

TABLES = ('sources', 'runs', 'briefs', 'events')


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def runtime_info():
    with closing(sqlite3.connect(':memory:')) as connection:
        version, source_id = connection.execute('SELECT sqlite_version(), sqlite_source_id()').fetchone()
    return {'python': sys.version, 'python_executable': sys.executable, 'platform': platform.platform(),
            'sqlite_runtime': version, 'sqlite_source_id': source_id,
            'sqlite_module_version': sqlite3.sqlite_version,
            'wal_patch_assessment': 'not_assessed', 'journal_mode_changed': False}


def readonly(database):
    path = Path(database).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Database must be an existing regular file')
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)


def diagnose(database=None):
    result = {'scope': 'selected ControlStore file header and this interpreter only', 'runtime': runtime_info()}
    if database is not None:
        path = Path(database).expanduser().resolve(strict=True)
        with path.open('rb') as stream:
            header = stream.read(100)
        if len(header) != 100 or header[:16] != b'SQLite format 3\x00':
            raise ValueError('Not a complete SQLite database header')
        versions = (header[18], header[19])
        family = 'wal' if versions == (2, 2) else 'rollback' if versions == (1, 1) else 'unknown'
        # Even mode=ro can create WAL shared-memory/sidecar files. Never connect
        # to a user-selected DB, including during a concurrent journal transition.
        result['database'] = {'path': str(path), 'persistent_journal_family': family,
            'header_write_version': versions[0], 'header_read_version': versions[1],
            'journal_mode': None, 'journal_mode_query': 'not_executed',
            'reason': 'Header-only observation; no SQLite connection or sidecar creation. '
                      'Rollback header does not distinguish DELETE/PERSIST/TRUNCATE; this is not a live-data snapshot.'}
    return result


def load_store():
    source = Path(__file__).resolve().parents[1] / 'src'
    if source.is_dir():
        sys.path.insert(0, str(source))
    from briefloop.store import Store
    return Store


def error_info(exc):
    code = getattr(exc, 'sqlite_errorcode', None)
    locked = isinstance(exc, sqlite3.Error) and (code is not None and code & 255 in (5, 6)
        or any(word in str(exc).lower() for word in ('database is locked', 'database table is locked', 'database is busy')))
    return {'type': type(exc).__name__, 'message': str(exc), 'sqlite_errorcode': code,
            'sqlite_errorname': getattr(exc, 'sqlite_errorname', None), 'lock_failure': bool(locked)}


def stats(seconds):
    values = sorted(seconds)
    return {'n': len(values), 'p50_ms': statistics.median(values) * 1000 if values else None,
            'p95_ms': values[math.ceil(.95 * len(values)) - 1] * 1000 if values else None,
            'max_ms': values[-1] * 1000 if values else None}


def fixture_text(process, index):
    return (f'Synthetic public fixture {process}_{index}\n') * 60


def fixture_draft(process, index):
    key = f'{process}_{index}'
    return {'title': key, 'markdown': 'Synthetic revenue 12 million. ' + key,
            'citations': [{'source_id': 'src_bench_' + key, 'locator': 'line 1'}]}


def store_child(root, index, cycles, barrier, output):
    try:
        Store = load_store()
    except BaseException as exc:
        save_new(output, {'process': index, 'status': 'failed', 'error': error_info(exc),
                          'operations': [], 'transactions': []})
        raise SystemExit(1)
    operations, transactions = [], []
    operation = 'initialize'

    class MeasuredStore(Store):
        @contextmanager
        def tx(self):
            start = time.monotonic()
            entered = body_end = failure = None
            try:
                with super().tx() as connection:
                    entered = time.monotonic()
                    try:
                        yield connection
                    finally:
                        body_end = time.monotonic()
            except BaseException as exc:
                failure = error_info(exc)
                raise
            finally:
                end = time.monotonic()
                transactions.append({'operation': operation,
                    'connection_and_begin_s': None if entered is None else entered - start,
                    'body_s': None if body_end is None else body_end - entered,
                    'commit_close_s': None if body_end is None else end - body_end,
                    'total_s': end - start, 'error': failure})

    def measured(name, call):
        nonlocal operation
        operation = name
        start = time.monotonic()
        failure = None
        try:
            return call()
        except BaseException as exc:
            failure = error_info(exc)
            raise
        finally:
            operations.append({'operation': name, 'elapsed_s': time.monotonic() - start, 'error': failure})

    failure = None
    try:
        barrier.wait(timeout=20)
        store = measured('initialize', lambda: MeasuredStore(root))
        seed = measured('seed_source', lambda: store.add_source('Synthetic seed', 'Local fixture only.', source_id=f'src_seed_{index}'))
        run = measured('create_run', lambda: store.create_run(
            {'title': f'Synthetic {index}', 'objective': 'Local benchmark only', 'allow_web': False}, [seed['id']]))
        for n in range(cycles):
            source = measured('source', lambda: store.add_source(str(n), fixture_text(index, n), source_id=f'src_bench_{index}_{n}'))
            measured('attach', lambda: store.attach_source(run['id'], source['id']))
            measured('event', lambda: store.event(None, 'sqlite_benchmark', {'process': index, 'index': n}))
            draft, version_id = fixture_draft(index, n), f'brief_bench_{index}_{n}'
            measured('publish', lambda: store.publish(run['id'], draft, version_id=version_id))
            measured('publish_replay', lambda: store.publish(run['id'], draft, version_id=version_id))
            measured('query', lambda: store.rows('SELECT id,hash FROM briefs ORDER BY rowid DESC LIMIT 10'))
    except BaseException as exc:
        failure = error_info(exc)
    save_new(output, {'process': index, 'status': 'failed' if failure else 'complete', 'error': failure,
                      'operations': operations, 'transactions': transactions})
    if failure:
        raise SystemExit(1)


def database_checks(root):
    with closing(readonly(Path(root) / 'briefloop.db')) as connection:
        return {'journal_mode': connection.execute('PRAGMA journal_mode').fetchone()[0],
                'integrity': [row[0] for row in connection.execute('PRAGMA integrity_check')],
                'foreign_key_errors': connection.execute('PRAGMA foreign_key_check').fetchall(),
                'counts': {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES}}


def check_content(root, processes, cycles):
    errors = []
    with closing(readonly(root / 'briefloop.db')) as connection:
        events = [json.loads(row[0]) for row in connection.execute("SELECT data FROM events WHERE kind='sqlite_benchmark'")]
        for index in range(processes):
            for n in range(cycles):
                key = f'{index}_{n}'
                brief = connection.execute('SELECT markdown FROM briefs WHERE id=?', ('brief_bench_' + key,)).fetchone()
                if not brief or brief[0] != fixture_draft(index, n)['markdown']:
                    errors.append('brief:' + key)
                source = root / 'sources' / ('src_bench_' + key + '.txt')
                expected = fixture_text(index, n).encode('utf-8')
                if not source.is_file() or source.read_bytes() != expected:
                    errors.append('source:' + key)
                stored_hash = connection.execute('SELECT hash FROM sources WHERE id=?', ('src_bench_' + key,)).fetchone()
                if not stored_hash or stored_hash[0] != hashlib.sha256(expected).hexdigest():
                    errors.append('source_hash:' + key)
                if sum(e == {'process': index, 'index': n} for e in events) != 1:
                    errors.append('event:' + key)
    return {'checked_cycles': processes * cycles, 'errors': errors, 'passed': not errors}


def stop_owned(children):
    terminated, killed = [], []
    for index, child in enumerate(children):
        if child.is_alive():
            terminated.append(index)
            child.terminate()
        if child.pid is not None:
            child.join(timeout=5)
        if child.is_alive():
            killed.append(index)
            child.kill()
            child.join(timeout=5)
        if child.is_alive():
            raise RuntimeError('Owned benchmark child did not stop; quiescent database checks skipped')
    return {'terminated_owned_processes': terminated, 'killed_owned_processes': killed}


def load_batch(folder, processes, cycles):
    folder.mkdir()
    root = folder / 'workspace'
    root.mkdir()
    context = mp.get_context('spawn')
    barrier = context.Barrier(processes)
    children = [context.Process(target=store_child, args=(root, i, cycles, barrier, folder / f'process-{i}.json')) for i in range(processes)]
    start = time.monotonic()
    cleanup = {}
    try:
        for child in children:
            child.start()
        deadline = start + max(60, cycles * 2)
        while any(p.is_alive() for p in children) and time.monotonic() < deadline:
            for child in children:
                child.join(timeout=.05)
    finally:
        cleanup = stop_owned(children)  # Never includes an existing product process.
    elapsed = time.monotonic() - start
    rows = [read_json(folder / f'process-{i}.json') if (folder / f'process-{i}.json').exists() else
            {'process': i, 'status': 'missing_result', 'operations': [], 'transactions': []} for i in range(processes)]
    operations = [op for row in rows for op in row['operations']]
    transactions = [tx for row in rows for tx in row['transactions']]
    failures = [op for op in operations if op['error']]
    measured = {}
    for name in sorted({op['operation'] for op in operations}):
        selected = [op for op in operations if op['operation'] == name]
        measured[name] = {**stats([op['elapsed_s'] for op in selected]),
                          'failed': sum(op['error'] is not None for op in selected),
                          'lock_failures': sum(bool(op['error'] and op['error']['lock_failure']) for op in selected)}
    result = {'processes': processes, 'cycles_per_process': cycles, 'wall_seconds_including_spawn': elapsed,
              'children_exitcodes': [p.exitcode for p in children], **cleanup,
              'failed_processes': [r['process'] for r in rows if r['status'] != 'complete'],
              'operations': measured, 'failures': failures, 'lock_failures': sum(e['error']['lock_failure'] for e in failures),
              'transactions': {key: stats([tx[key] for tx in transactions if tx[key] is not None]) for key in
                               ('connection_and_begin_s', 'body_s', 'commit_close_s', 'total_s')},
              'transaction_timing_note': 'Connection/PRAGMA/BEGIN IMMEDIATE combined; not isolated SQLite lock-wait time',
              'percentile_note': 'p50 median; p95 nearest-rank; operation timings include failed attempts',
              'expected_counts': {'sources': processes * (cycles + 1), 'runs': processes,
                                  'briefs': processes * cycles, 'events': processes * cycles}}
    try:
        result['database'] = database_checks(root)
        result['counts_match'] = result['database']['counts'] == result['expected_counts']
        result['content_check'] = check_content(root, processes, cycles)
    except (sqlite3.Error, OSError, ValueError) as exc:
        result['check_error'] = error_info(exc)
    result['passed'] = (not result['failed_processes'] and all(p.exitcode == 0 for p in children)
        and result.get('counts_match', False) and result.get('content_check', {}).get('passed', False)
        and result['database']['integrity'] == ['ok'] and not result['database']['foreign_key_errors'])
    save_new(folder / 'summary.json', result)
    return result


def crash_child(root, ready):
    store = load_store()(root)
    store.set_meta('benchmark_committed', {'keep': True})
    with store.tx() as connection:
        connection.execute("INSERT OR REPLACE INTO meta VALUES('benchmark_uncommitted',?)", (json.dumps({'discard': True}),))
        ready.set()
        os._exit(31)


def logical_snapshot(root):
    """Hash all persisted table content in a stopped generated fixture, not just stored hash columns."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    tables = {}
    with closing(readonly(Path(root) / 'briefloop.db')) as connection:
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            query = connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            rows = [[{'blob_hex': value.hex()} if isinstance(value, bytes) else value for value in row] for row in query]
            # Preserve duplicates, avoid unspecified row ordering, and never output fixture row values.
            encoded = sorted(json.dumps(row, ensure_ascii=False) for row in rows)
            tables[name] = {'count': len(rows), 'sha256': digest({'columns': [c[0] for c in query.description], 'rows': encoded})}
    return {'schema_sha256': digest(schema), 'tables': tables, 'content_sha256': digest([schema, tables])}


def source_file_hashes(root):
    folder = Path(root) / 'sources'
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def recovery_probe(folder):
    folder.mkdir()
    root = folder / 'workspace'
    Store = load_store()
    store = Store(root)
    source = store.add_source('Synthetic backup source', 'Preserve this exact source.\n')
    run = store.create_run({'title': 'Synthetic backup', 'objective': 'Offline recovery probe', 'allow_web': False}, [source['id']])
    brief = store.publish(run['id'], {'title': 'Synthetic backup', 'markdown': 'Preserve this draft.'}, version_id='brief_backup')
    reader = readonly(store.db)
    reader.execute('BEGIN')
    reader.execute('SELECT id FROM briefs').fetchall()
    entered, done = threading.Event(), threading.Event()
    wait = {}
    def blocked_writer():
        start = time.monotonic()
        entered.set()
        try:
            store.event(None, 'long_read_probe', {})
            wait['status'] = 'complete'
        except BaseException as exc:
            wait.update(status='failed', error=error_info(exc))
        finally:
            wait['elapsed_s'] = time.monotonic() - start
            done.set()
    writer = threading.Thread(target=blocked_writer, daemon=True)
    writer.start()
    try:
        if not entered.wait(5):
            raise RuntimeError('Synthetic writer did not start')
        time.sleep(.2)
        wait['unfinished_while_read_open'] = not done.is_set()
    finally:
        reader.rollback()
        reader.close()
        writer.join(timeout=15)
    if writer.is_alive():
        raise RuntimeError('Owned writer did not stop; backup was not attempted')
    context = mp.get_context('spawn')
    ready = context.Event()
    child = context.Process(target=crash_child, args=(root, ready))
    child.start()
    signaled = ready.wait(15)
    child.join(timeout=15)
    cleanup = stop_owned([child])
    reopened = Store(root)
    crash = {'exitcode': child.exitcode, **cleanup, 'uncommitted_transaction_entered': signaled,
             'committed_preserved': reopened.meta('benchmark_committed') == {'keep': True},
             'uncommitted_absent': reopened.meta('benchmark_uncommitted') is None,
             'database': database_checks(root)}
    # This is a generated fixture with all writers joined. It is not a general live-workspace backup command.
    before = logical_snapshot(root)
    before_sources = source_file_hashes(root)
    restored_root = folder / 'restore'
    restored_root.mkdir()
    shutil.copytree(root / 'sources', restored_root / 'sources')
    with closing(readonly(store.db)) as src, closing(sqlite3.connect(restored_root / 'briefloop.db')) as destination:
        src.backup(destination)
    restored = Store(restored_root)
    after = logical_snapshot(restored_root)
    backup = {'logical_data_equal': before == after, 'source_file_map_equal': before_sources == source_file_hashes(restored_root),
              'original_logical_snapshot': before, 'restored_logical_snapshot': after,
              'method': 'SQLite Backup API plus source files copied while all owned writers stopped',
              'database': database_checks(restored_root),
              'counts_equal': database_checks(restored_root)['counts'] == crash['database']['counts'],
              'source_bytes_equal': (root / source['path']).read_bytes() == (restored_root / source['path']).read_bytes(),
              'source_hash_equal': hashlib.sha256((restored_root / source['path']).read_bytes()).hexdigest() == source['hash'],
              'brief_hash_equal': restored.one('briefs', brief['id'])['hash'] == brief['hash'],
              'committed_marker_preserved': restored.meta('benchmark_committed') == {'keep': True},
              'uncommitted_marker_absent': restored.meta('benchmark_uncommitted') is None}
    passed = (wait.get('status') == 'complete' and signaled and child.exitcode == 31
              and crash['committed_preserved'] and crash['uncommitted_absent']
              and crash['database']['integrity'] == ['ok'] and not crash['database']['foreign_key_errors']
              and all(backup[k] for k in ('counts_equal', 'source_bytes_equal', 'source_hash_equal', 'brief_hash_equal',
                                          'committed_marker_preserved', 'uncommitted_marker_absent', 'logical_data_equal', 'source_file_map_equal'))
              and backup['database']['integrity'] == ['ok'] and not backup['database']['foreign_key_errors'])
    result = {'long_read': wait, 'crash': crash, 'backup_restore': backup, 'passed': passed,
              'limits': 'Process exit only, not power loss; quiescent generated fixture only, not arbitrary multi-file hot backup'}
    save_new(folder / 'summary.json', result)
    return result


def run_benchmark(output, processes=(1, 4), cycles=25):
    if not processes or len(set(processes)) != len(processes) or any(n < 1 or n > 8 for n in processes) or not 1 <= cycles <= 1000:
        raise ValueError('Choose distinct process counts 1..8 and cycles 1..1000')
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    Store = load_store()
    module = sys.modules[Store.__module__]
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False) if shutil.which('git') else None
    environment = {**runtime_info(), 'application_commit': commit.stdout.strip() if commit and commit.returncode == 0 else None,
                   'store_path': str(Path(module.__file__).resolve()),
                   'store_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                   'benchmark_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'scope': 'Synthetic BriefLoop ControlStore load, no model/network calls; SDK databases not measured'}
    save_new(output / 'environment.json', environment)
    result = {'environment': environment, 'loads': {}, 'scope': environment['scope']}
    try:
        for count in processes:
            result['loads'][str(count)] = load_batch(output / f'load-{count}', count, cycles)
        result['recovery'] = recovery_probe(output / 'recovery')
        result['passed'] = all(row['passed'] for row in result['loads'].values()) and result['recovery']['passed']
    except BaseException as exc:
        result.update(passed=False, error=error_info(exc))
    save_new(output / 'metrics.json', result)
    with (output / 'metrics.csv').open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['processes', 'operation', 'n', 'p50_ms', 'p95_ms', 'max_ms', 'failed', 'lock_failures'])
        writer.writeheader()
        for count, row in result['loads'].items():
            for operation, values in row['operations'].items():
                writer.writerow({'processes': count, 'operation': operation, **values})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    diagnostic = sub.add_parser('diagnose', help='Read actual runtime and optional database persistent header format, without opening that DB')
    diagnostic.add_argument('--database', type=Path)
    diagnostic.add_argument('--out', type=Path, help='New JSON file; refuses overwrite')
    run = sub.add_parser('run', help='Create a new synthetic result folder; never accepts an existing workspace')
    run.add_argument('--out', type=Path, required=True)
    run.add_argument('--processes', type=int, nargs='+', default=[1, 4])
    run.add_argument('--cycles', type=int, default=25)
    args = parser.parse_args()
    try:
        result = diagnose(args.database) if args.command == 'diagnose' else run_benchmark(args.out, args.processes, args.cycles)
        if args.command == 'diagnose' and args.out:
            save_new(args.out, result)
        print(json.dumps(result if args.command == 'diagnose' else {'output': str(args.out), 'passed': result['passed']}, ensure_ascii=False, indent=2))
        return 0 if args.command == 'diagnose' or result['passed'] else 1
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': error_info(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
