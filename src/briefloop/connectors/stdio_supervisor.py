"""Bounded byte forwarding for an explicitly approved stdio command.

This is a transport supervisor, not a JSON-RPC implementation. The official MCP
SDK still performs parsing, negotiation and all protocol operations.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


def write_marker(marker, **extra):
    identity = {'pid': os.getpid()}
    if os.name == 'posix':
        identity['pgid'] = os.getpgrp()
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({**identity, **extra}), encoding='utf-8')
    temporary.replace(marker)


def supervise_windows(args):
    # This absolute entrypoint runs under -I, independently of the connector cwd.
    import subprocess
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from briefloop.platform_support import OwnedProcess

    marker = Path(args.marker)
    process = OwnedProcess(args.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_marker(marker, child_pid=process.pid)
    closing = threading.Lock()

    def close_tree():
        with closing:
            process.close_tree()

    def send_input():
        try:
            while chunk := os.read(sys.stdin.fileno(), 65536):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(process.stdin.fileno(), view):]
        except OSError:
            pass
        finally:
            process.stdin.close()

    def drain_errors():
        remaining = 65536
        try:
            while chunk := os.read(process.stderr.fileno(), 4096):
                if remaining:
                    visible = chunk[:remaining]
                    os.write(sys.stderr.fileno(), visible)
                    remaining -= len(visible)
        except OSError:
            pass

    def reap_after_exit():
        process.wait()
        close_tree()  # A server can exit while a descendant keeps its pipes open.

    for target in (send_input, drain_errors, reap_after_exit):
        threading.Thread(target=target, daemon=True).start()
    try:
        while line := process.stdout.readline(args.limit + 1):
            if len(line) > args.limit:
                write_marker(marker, child_pid=process.pid, error='response_too_large')
                raise ValueError('response_too_large')
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    finally:
        close_tree()


async def supervise(args) -> None:
    marker = Path(args.marker)
    write_marker(marker)
    process = await asyncio.create_subprocess_exec(*args.command, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE,
                                                   limit=args.limit + 1)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    output_transport, output_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    output = asyncio.StreamWriter(output_transport, output_protocol, None, loop)

    async def send_input():
        try:
            while chunk := await reader.read(65536):
                process.stdin.write(chunk)
                await process.stdin.drain()
        finally:
            process.stdin.close()

    async def receive_output():
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                raise ValueError('response_too_large') from None
            if not line:
                return
            if len(line) > args.limit:
                raise ValueError('response_too_large')
            output.write(line)
            await output.drain()

    async def drain_errors():
        remaining = 65536
        while chunk := await process.stderr.read(4096):
            if remaining:
                visible = chunk[:remaining]
                sys.stderr.buffer.write(visible)
                sys.stderr.buffer.flush()
                remaining -= len(visible)

    tasks = [asyncio.create_task(send_input()), asyncio.create_task(receive_output()),
             asyncio.create_task(drain_errors()), asyncio.create_task(process.wait())]
    try:
        done, _ = await asyncio.wait([tasks[1], tasks[3]], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[3] in done:
            try:
                await asyncio.wait_for(asyncio.shield(tasks[1]), .5)
            except TimeoutError:
                pass
    except ValueError as exc:
        if str(exc) == 'response_too_large':
            write_marker(marker, error='response_too_large')
        raise
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 1)
            except TimeoutError:
                process.kill()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        output.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--marker', required=True)
    parser.add_argument('--limit', required=True, type=int)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if options.command[:1] == ['--']:
        options.command = options.command[1:]
    try:
        if os.name == 'nt':
            supervise_windows(options)
        else:
            asyncio.run(supervise(options))
    except Exception:
        # Never print command/environment values or upstream error text.
        print('MCP stdio transport ended.', file=sys.stderr)
        sys.exit(1)
