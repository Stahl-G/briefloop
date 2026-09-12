"""Run real MCP protocol/lifecycle probes. No model, paid service or application mutation."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import traceback

import anyio
import httpx2
from mcp import Client, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

SERVER = Path(__file__).with_name('server.py').resolve()
DOCUMENT = 'Synthetic M0 source. 销量 17 件。This is a protocol fixture, not business evidence.\n'


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def events(directory: Path) -> list[dict]:
    path = directory / 'server.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        # A dead descendant can remain a zombie until launchd/init reaps it.
        status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(status) and not status.startswith('Z')
    except ProcessLookupError:
        return False


def prepare(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / 'document.txt').write_text(DOCUMENT, encoding='utf-8')
    return directory


@asynccontextmanager
async def endpoint(directory: Path, transport: str):
    args = [str(SERVER), '--transport', transport, '--directory', str(directory)]
    with (directory / 'stderr.log').open('w') as stderr:
        if transport == 'stdio':
            parameters = StdioServerParameters(command=sys.executable, args=args,
                                               env={'PYTHONUNBUFFERED': '1'}, cwd=directory)
            yield stdio_client(parameters, errlog=stderr)
            return
        process = subprocess.Popen([sys.executable, *args], cwd=directory,
                                   env={'PATH': os.defpath, 'PYTHONUNBUFFERED': '1'},
                                   stdin=subprocess.DEVNULL, stdout=stderr, stderr=stderr,
                                   start_new_session=True)
        try:
            with anyio.fail_after(10):
                while not (directory / 'ready.json').exists():
                    if process.poll() is not None:
                        raise RuntimeError(f'HTTP server exited {process.returncode}')
                    await anyio.sleep(.05)
            yield json.loads((directory / 'ready.json').read_text())['url']
        finally:
            if process.poll() is None:
                process.terminate()
                with anyio.CancelScope(shield=True):
                    with anyio.move_on_after(4):
                        while process.poll() is None:
                            await anyio.sleep(.05)
                if process.poll() is None:
                    # Only the process group created by this context.
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            save(directory / 'http_owner_cleanup.json', {'pid': process.pid, 'returncode': process.returncode,
                                                       'alive': alive(process.pid)})


async def core(root: Path, transport: str, mode: str) -> dict:
    directory = prepare(root, f'{transport}-{mode}')
    record: dict = {'transport': transport, 'mode': mode, 'receipts': {}}

    def receipt(name, result):
        # This is an SDK-decoded result, NOT a capture of original wire bytes.
        value = result.model_dump(mode='json', by_alias=True, exclude_unset=True)
        record['receipts'][name] = value
        save(directory / f'{name}.sdk-result.json', value)
        return result

    child = None
    server_pid = None
    try:
        async with endpoint(directory, transport) as target:
            async with Client(target, mode=mode, cache=None, read_timeout_seconds=3) as client:
                record['protocol'] = client.protocol_version
                assert record['protocol'] == ('2026-07-28' if mode == 'auto' else '2025-11-25')
                listing = receipt('list_tools', await client.list_tools())
                assert {'inspect_document', 'slow_read', 'explicit_failure'} <= {t.name for t in listing.tools}
                resources = receipt('list_resources', await client.list_resources())
                assert 'm0://document' in {str(r.uri) for r in resources.resources}
                templates = receipt('list_templates', await client.list_resource_templates())
                assert len(templates.resource_templates) == 1
                result = receipt('call_tool', await client.call_tool('inspect_document'))
                data = result.structured_content
                assert not result.is_error and data['text'] == DOCUMENT
                assert data['sha256'] == hashlib.sha256((directory / 'document.txt').read_bytes()).hexdigest()
                assert not data['unexpected_environment']
                server_pid = data['pid']
                for name, uri in [('read_resource', 'm0://document'),
                                  ('read_scoped_file_uri', 'file:///briefloop-m0-server/document.txt')]:
                    value = receipt(name, await client.read_resource(uri))
                    assert value.contents[0].text == DOCUMENT
                failure = receipt('explicit_failure', await client.call_tool('explicit_failure'))
                normal = receipt('ordinary_error_text', await client.call_tool('ordinary_error_text'))
                assert failure.is_error and not normal.is_error

                # AnyIO cancellation keeps context enter/exit in the owning task.
                with anyio.move_on_after(.3) as scope:
                    await client.call_tool('slow_read', {'token': 'caller-cancel'})
                assert scope.cancelled_caught
                record['caller_cancelled'] = True
                try:
                    await client.call_tool('slow_read', {'token': 'sdk-timeout'}, read_timeout_seconds=.3)
                except Exception as exc:
                    record['sdk_timeout'] = {'type': type(exc).__name__, 'message': str(exc)}
                else:
                    raise AssertionError('slow call did not time out')
                with anyio.move_on_after(2):
                    while len([e for e in events(directory) if e['event'] == 'slow_cancelled']) < 2:
                        await anyio.sleep(.05)
                record['cancellation_events'] = [e for e in events(directory) if e['event'].startswith('slow_')]
                assert len([e for e in record['cancellation_events'] if e['event'] == 'slow_started']) == 2
                assert not any(e['event'] == 'slow_completed' for e in record['cancellation_events'])
                assert len([e for e in record['cancellation_events'] if e['event'] == 'slow_cancelled']) == 2
                receipt('post_cancel_call', await client.call_tool('inspect_document'))
                if transport == 'stdio' and mode == 'auto':
                    child = (await client.call_tool('spawn_owned_child')).structured_content
                    assert child['pgid'] == data['pgid'] and child['pgid'] != os.getpgrp()
                started = time.monotonic()
            record['context_exit_seconds'] = round(time.monotonic() - started, 3)
            if transport == 'stdio':
                record['server_alive_after_sdk_exit'] = alive(server_pid)
                assert not record['server_alive_after_sdk_exit']
                if child:
                    record['descendant_alive_after_sdk_exit'] = alive(child['pid'])
            else:
                # Disconnect must not kill an independently owned HTTP server.
                record['server_alive_after_client_exit'] = alive(server_pid)
                assert record['server_alive_after_client_exit']
        record['server_alive_after_owner_exit'] = alive(server_pid)
        assert not record['server_alive_after_owner_exit']
        record['required_checks_passed'] = True
        return record
    finally:
        if child and alive(child['pid']):
            # The SDK created this isolated group; this probe alone spawned its child.
            if os.getpgid(child['pid']) != child['pgid'] or child['pgid'] == os.getpgrp():
                raise RuntimeError('Refusing to clean a process whose ownership changed')
            os.killpg(child['pgid'], signal.SIGTERM)
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(2):
                    while alive(child['pid']):
                        await anyio.sleep(.05)
                if alive(child['pid']):
                    os.killpg(child['pgid'], signal.SIGKILL)
            record['descendant_cleaned_by_probe'] = not alive(child['pid'])
        save(directory / 'summary.json', record)


async def interrupted_call(root: Path, transport: str) -> dict:
    directory = prepare(root, f'{transport}-death')
    record = {'transport': transport}
    async with endpoint(directory, transport) as target:
        started = time.monotonic()
        succeeded = False
        try:
            async with Client(target, cache=None, read_timeout_seconds=2) as client:
                started = time.monotonic()
                await client.call_tool('exit_during_call')
                succeeded = True
        except Exception as exc:
            # Transport failures can surface on the owner's __aexit__, not only the RPC await.
            record.update(error_type=type(exc).__name__, error=str(exc),
                          failure_seconds=round(time.monotonic() - started, 3))
            (directory / 'expected-error.txt').write_text(traceback.format_exc())
        assert not succeeded and 'error_type' in record
    calls = [e for e in events(directory) if e['event'] == 'exit_during_call']
    assert len(calls) == 1
    record['server_invocations'] = len(calls)
    record['outcome'] = 'sent_then_response_unknown; never replayed by this probe'
    save(directory / 'summary.json', record)
    return record


async def http_rejections(root: Path) -> list[dict]:
    directory = prepare(root, 'http-rejections')
    results = []

    async def reject_non_protocol_failures(response: httpx2.Response) -> None:
        # A supported SDK HTTP-client hook, not a replacement JSON-RPC client.
        if response.status_code in (401, 403, 429) or response.status_code >= 500:
            response.raise_for_status()

    async with endpoint(directory, 'http') as url:
        for guarded in (False, True):
            for status in (401, 403, 500):
                before = len(events(directory))
                connected = False
                try:
                    hooks = {'response': [reject_non_protocol_failures]} if guarded else {}
                    async with httpx2.AsyncClient(event_hooks=hooks, trust_env=False) as http_client:
                        transport = streamable_http_client(
                            url.rsplit('/', 1)[0] + f'/deny{status}', http_client=http_client)
                        async with Client(transport, read_timeout_seconds=2):
                            connected = True
                except Exception as exc:
                    calls = [e['method'] for e in events(directory)[before:] if e['event'] == 'http_rpc']
                    results.append({'status': status, 'guarded': guarded, 'rpc_attempts': calls,
                                    'error_type': type(exc).__name__, 'error': str(exc),
                                    'legacy_handshake_attempted': 'initialize' in calls})
                    (directory / f'error-{status}-{guarded}.txt').write_text(traceback.format_exc())
                    if guarded:
                        assert calls == ['server/discover']
                assert not connected
        assert len(results) == 6
    save(directory / 'summary.json', results)
    return results


async def run(root: Path) -> None:
    report = {'python': sys.version, 'platform': platform.platform(),
              'packages': {d.metadata['Name']: d.version for d in metadata.distributions()},
              'receipt_representation': 'SDK model JSON, by_alias=True, exclude_unset=True; not wire bytes',
              'core': [], 'server_death': []}
    try:
        for transport in ('stdio', 'http'):
            for mode in ('auto', 'legacy'):
                result = await core(root, transport, mode)
                report['core'].append(result)
                print(f'{transport}/{mode}: protocol={result["protocol"]}, list/call/read/cancel/exit OK', flush=True)
        for transport in ('stdio', 'http'):
            report['server_death'].append(await interrupted_call(root, transport))
        report['http_rejections'] = await http_rejections(root)
        report['required_checks_passed'] = True
    except BaseException:
        report['failure'] = traceback.format_exc()
        raise
    finally:
        save(root / 'report.json', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='New output directory; never overwrites a run')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('This M0 lifecycle probe is POSIX-only; Windows requires its separate acceptance')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    # A harmless sentinel tests that ambient secrets would not be inherited.
    os.environ['BRIEFLOOP_M0_DO_NOT_INHERIT'] = 'non-secret-test-sentinel'
    anyio.run(run, args.output)
