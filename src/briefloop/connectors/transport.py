"""Official SDK transports with bounded local supervision and HTTP responses."""
from __future__ import annotations

from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import tempfile

import anyio
import httpx2
from mcp import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import ConnectorError


class ResponseLimitError(Exception):
    pass


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        total = 0
        async for chunk in self.stream:
            total += len(chunk)
            if total > self.limit:
                raise ResponseLimitError('response_too_large')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


@asynccontextmanager
async def connection_transport(config: dict, secrets: dict, diagnostics: dict):
    if config['transport'] == 'http':
        async def inspect_response(response):
            if response.status_code in (401, 403, 429) or response.status_code >= 500:
                response.raise_for_status()
            if response.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
                raise ResponseLimitError('encoded_response_unsupported')
            length = response.headers.get('content-length')
            if length and int(length) > config['max_response_bytes']:
                raise ResponseLimitError('response_too_large')
            response.stream = BoundedStream(response.stream, config['max_response_bytes'])

        headers = {'Accept-Encoding': 'identity'}
        if secrets.get('bearer_token'):
            headers['Authorization'] = 'Bearer ' + secrets['bearer_token']
        async with httpx2.AsyncClient(headers=headers, trust_env=False,
                                     timeout=httpx2.Timeout(config['timeout_seconds']),
                                     event_hooks={'response': [inspect_response]}) as http_client:
            async with streamable_http_client(config['url'], http_client=http_client) as streams:
                yield streams
        return
    if os.name != 'posix':
        raise ConnectorError('此版本尚未验证 Windows stdio 进程清理。', code='unsupported_transport')
    with tempfile.TemporaryDirectory(prefix='briefloop-mcp-') as directory:
        marker = Path(directory) / 'process.json'
        diagnostics['marker_path'] = str(marker)
        supervisor = Path(__file__).with_name('stdio_supervisor.py')
        params = StdioServerParameters(command=sys.executable,
                                       args=['-I', str(supervisor), '--marker', str(marker),
                                             '--limit', str(config['max_response_bytes']), '--',
                                             config['command'], *config['args']],
                                       cwd=config.get('cwd'), env=secrets.get('env', {}))
        ownership = None
        with tempfile.TemporaryFile(mode='w+') as stderr:
            try:
                async with stdio_client(params, errlog=stderr) as streams:
                    with anyio.fail_after(5):
                        while not marker.exists():
                            await anyio.sleep(.01)
                    candidate = json.loads(marker.read_text())
                    pid = candidate['pid']
                    parent = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
                    if (not isinstance(pid, int) or candidate['pgid'] != pid or os.getpgid(pid) != pid
                            or parent != str(os.getpid())):
                        raise ConnectorError('无法确认连接器进程所有权。', code='cleanup_failed')
                    ownership = {'pid': pid, 'pgid': pid}
                    diagnostics.update(ownership)
                    yield streams
            finally:
                if marker.exists():
                    observed = json.loads(marker.read_text())
                    if observed.get('error') == 'response_too_large':
                        diagnostics['error'] = 'response_too_large'
                if ownership is not None:
                    group = ownership['pgid']
                    # SDK creates this dedicated POSIX session; marker never comes from config.
                    if group != ownership['pid'] or group == os.getpgrp() or group <= 1:
                        raise ConnectorError('无法确认连接器进程所有权。', code='cleanup_failed')
                    with anyio.CancelScope(shield=True):
                        with suppress(ProcessLookupError):
                            os.killpg(group, signal.SIGTERM)
                        await anyio.sleep(.05)
                        with suppress(ProcessLookupError):
                            os.killpg(group, signal.SIGKILL)
