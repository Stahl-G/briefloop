"""Bounded byte forwarding for an explicitly approved stdio command (POSIX).

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


async def supervise(args) -> None:
    marker = Path(args.marker)
    marker.write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp()}))
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
            marker.write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'error': 'response_too_large'}))
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
        asyncio.run(supervise(options))
    except Exception:
        # Never print command/environment values or upstream error text.
        print('MCP stdio transport ended.', file=sys.stderr)
        sys.exit(1)
