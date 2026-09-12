"""Real SDK server over subprocess stdio or loopback HTTP; synthetic local inputs only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=['stdio', 'http'], required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()

    def event(kind: str, **fields: object) -> None:
        with (directory / 'server.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': kind, 'pid': os.getpid(), 'time': time.time(), **fields}) + '\n')

    server = MCPServer('BriefLoop M0 file reader', version='1')

    @server.tool()
    def inspect_document() -> dict[str, object]:
        """Read the controlled document from disk and compute its digest."""
        data = (directory / 'document.txt').read_bytes()
        event('inspect_document')
        return {'text': data.decode('utf-8'), 'sha256': hashlib.sha256(data).hexdigest(),
                'pid': os.getpid(), 'pgid': os.getpgrp() if os.name == 'posix' else None,
                'unexpected_environment': 'BRIEFLOOP_M0_DO_NOT_INHERIT' in os.environ}

    @server.resource('m0://document', mime_type='text/plain')
    def document() -> str:
        event('read_document')
        return (directory / 'document.txt').read_text(encoding='utf-8')

    @server.resource('file:///briefloop-m0-server/document.txt', mime_type='text/plain')
    def scoped_document() -> str:
        event('read_scoped_document')
        return (directory / 'document.txt').read_text(encoding='utf-8')

    @server.resource('m0://document/{revision}', mime_type='text/plain')
    def revision_document(revision: str) -> str:
        return revision + ': ' + document()

    @server.tool()
    def explicit_failure() -> str:
        """Return a real SDK ToolError."""
        raise ToolError('controlled material access failure')

    @server.tool()
    def ordinary_error_text() -> str:
        """An unflagged result containing a business-failure message."""
        return 'error: source quota exceeded; no document was returned.'

    @server.tool()
    async def slow_read(token: str) -> str:
        """Read after a delay so the caller can test cancellation."""
        event('slow_started', token=token)
        try:
            await asyncio.sleep(20)
            event('slow_completed', token=token)
            return document()
        except asyncio.CancelledError:
            event('slow_cancelled', token=token)
            raise

    @server.tool()
    def spawn_owned_child() -> dict[str, int | None]:
        """Create a short-lived disposable descendant for the cleanup probe."""
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        group = os.getpgid(child.pid) if os.name == 'posix' else None
        event('owned_child', child_pid=child.pid, child_pgid=group)
        return {'pid': child.pid, 'pgid': group}

    @server.tool()
    def exit_during_call() -> str:
        """End only this disposable server, before a call response is sent."""
        event('exit_during_call')
        os._exit(23)

    event('server_started', transport=args.transport)
    if args.transport == 'stdio':
        try:
            server.run(transport='stdio')
        finally:
            event('server_stopped')
        return

    class ObserveHTTP:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await self.app(scope, receive, send)
            headers = dict(scope['headers'])
            event('http_request', method=scope['method'], path=scope['path'],
                  protocol=headers.get(b'mcp-protocol-version', b'').decode(),
                  has_session=b'mcp-session-id' in headers)

            async def observed_receive():
                message = await receive()
                if message['type'] == 'http.request' and message.get('body'):
                    # Diagnostic only: local small requests, not an RPC implementation.
                    try:
                        body = json.loads(message['body'])
                        event('http_rpc', method=body.get('method'))
                    except (ValueError, AttributeError):
                        pass
                return message

            if scope['path'] in ('/deny401', '/deny403', '/deny500'):
                # Exercise actual HTTP errors, not fabricated MCP responses.
                await observed_receive()
                await send({'type': 'http.response.start', 'status': int(scope['path'][-3:]),
                            'headers': [(b'content-type', b'text/plain')]})
                await send({'type': 'http.response.body', 'body': b'controlled HTTP rejection'})
                return
            return await self.app(scope, observed_receive, send)

    # Hold the socket throughout startup: no select-a-free-port race.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        url = f'http://127.0.0.1:{sock.getsockname()[1]}/mcp'
        ready = directory / 'ready.json'
        ready.with_suffix('.tmp').write_text(json.dumps({'pid': os.getpid(), 'url': url}))
        ready.with_suffix('.tmp').replace(ready)
        app = ObserveHTTP(server.streamable_http_app(host='127.0.0.1'))
        config = uvicorn.Config(app, log_level='warning', timeout_graceful_shutdown=2)
        uvicorn.Server(config).run(sockets=[sock])
    event('server_stopped')


if __name__ == '__main__':
    main()
