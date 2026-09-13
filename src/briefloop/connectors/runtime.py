"""One task owns each SDK context, including cancellation and teardown."""
from __future__ import annotations

from concurrent.futures import Future
import hashlib
import json
import time
import uuid

import anyio
import httpx2
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pathlib import Path
from pydantic import ValidationError

from .config import ConnectorError
from .transport import connection_transport, ResponseLimitError


def public_error(exc: BaseException, diagnostics: dict | None = None) -> dict:
    diagnostics = diagnostics or {}
    marker = diagnostics.get('marker_path')
    if marker:
        try:
            diagnostics.update(json.loads(Path(marker).read_text()))
        except (OSError, ValueError):
            pass
    if diagnostics.get('error') == 'response_too_large':
        return {'code': 'response_limit', 'message': '连接器响应超出大小限制。'}
    leaves = []
    def visit(error):
        if isinstance(error, BaseExceptionGroup):
            for item in error.exceptions:
                visit(item)
        else:
            leaves.append(error)
    visit(exc)
    for error in leaves:
        if type(error).__name__ == 'InputRequiredRoundsExceededError':
            return {'code': 'requires_interaction', 'message': '该连接器需要额外授权或交互，当前入口不自动继续。'}
        if isinstance(error, ConnectorError):
            return {'code': error.code, 'message': str(error)}
        if isinstance(error, httpx2.HTTPStatusError):
            status = error.response.status_code
            return {'code': 'authentication_required' if status == 401 else 'access_denied' if status == 403 else 'upstream_http_error',
                    'message': f'连接器返回 HTTP {status}。请检查授权或服务状态。'}
        if isinstance(error, ResponseLimitError):
            return {'code': 'response_limit', 'message': '响应超出大小限制或使用了尚未支持的压缩编码。'}
        if isinstance(error, (TimeoutError, httpx2.TimeoutException)) or 'timed out' in str(error).lower():
            return {'code': 'timeout', 'message': '连接器请求超时；不会自动重发。'}
    if (diagnostics or {}).get('error') == 'response_too_large':
        return {'code': 'response_limit', 'message': '连接器响应超出大小限制。'}
    return {'code': 'connection_failed', 'message': '连接器通信已中断或请求失败；请检查本地命令、地址或服务状态。'}


def check_refs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('$ref', '$dynamicRef') and isinstance(item, str) and not item.startswith('#'):
                raise ConnectorError('此版本不读取工具 schema 中的外部引用。', code='unsupported_schema')
            check_refs(item)
    elif isinstance(value, list):
        for item in value:
            check_refs(item)


class Owner:
    def __init__(self, config: dict, secrets: dict, scope: str):
        self.config, self.secrets, self.scope = config, secrets, scope
        self.state = 'connecting'
        self.error = None
        self.protocol = None
        self.capabilities = {'tools': [], 'resources': [], 'resource_templates': []}
        self.warnings = []
        self.pending = {}
        self.diagnostics = {}
        self.stopping = False
        self.ready = Future()

    async def run(self):
        self.stopped = anyio.Event()
        self.send, receive = anyio.create_memory_object_stream(16)
        try:
            with anyio.CancelScope(deadline=anyio.current_time() + self.config['timeout_seconds']) as lifetime:
                self.lifetime = lifetime
                transport = connection_transport(self.config, self.secrets, self.diagnostics)
                async with Client(transport, cache=None, read_timeout_seconds=self.config['timeout_seconds'], input_required_max_rounds=0) as client:
                    self.client = client
                    self.protocol = client.protocol_version
                    await self._catalog()
                    lifetime.deadline = float('inf')
                    self.state = 'connected'
                    self.ready.set_result(self)
                    async with anyio.create_task_group() as group:
                        async with receive:
                            async for command in receive:
                                if command is None:
                                    group.cancel_scope.cancel()
                                    break
                                group.start_soon(self._execute, command)
        except BaseException as exc:
            self.error = public_error(exc, self.diagnostics)
            self.state = 'error'
        finally:
            self.secrets = {}
            if not self.ready.done():
                self.error = self.error or {'code': 'cancelled' if self.stopping else 'timeout',
                                            'message': '连接已取消。' if self.stopping else '连接器测试超时。'}
                self.state = 'error'
                self.ready.set_exception(ConnectorError(self.error['message'], code=self.error['code']))
            if self.state != 'error':
                self.state = 'disconnected'
            for identifier, future in list(self.pending.items()):
                if not future.done():
                    future.set_result({'operation_id': identifier, 'delivery': 'unknown',
                                       'error': self.error or {'code': 'closed', 'message': '连接已关闭。'}})
            self.pending.clear()
            self.send.close()
            receive.close()
            self.stopped.set()

    async def _catalog(self):
        async def collect(method, field):
            items, cursor = [], None
            seen = set()
            total = 0
            for _ in range(10):
                try:
                    result = await method(cursor=cursor)
                except ValidationError as exc:
                    optional = {'resources': ('ListResourcesResult', 'resources'),
                                'resource_templates': ('ListResourceTemplatesResult', 'resourceTemplates')}
                    expected = optional.get(field)
                    errors = exc.errors(include_url=False)
                    if (expected is None or exc.title != expected[0] or len(errors) != 1
                            or errors[0]['type'] != 'list_type' or errors[0]['loc'] != (expected[1],)
                            or type(errors[0].get('input')) is not dict or errors[0]['input'] != {}):
                        raise
                    # The SDK validated this response after receiving it; the
                    # session remains usable. Never retry a request or relax tools.
                    # ValidationError does not retain nextCursor, so stop this
                    # optional listing and disclose its incomplete validation.
                    self.warnings.append({'code': 'empty_object_catalog', 'catalog': field,
                                          'message': '服务将可选资源目录返回为 {}，已按空页兼容；该目录后续分页未核实。'})
                    return items
                batch = getattr(result, field)
                total += len(result.model_dump_json())
                if total > self.config['max_response_bytes']:
                    raise ResponseLimitError('catalog_too_large')
                items.extend(x.model_dump(mode='json', by_alias=True, exclude_unset=True) for x in batch)
                cursor = result.next_cursor
                if not cursor:
                    return items
                if cursor in seen:
                    raise ConnectorError('连接器目录分页游标重复。', code='invalid_catalog')
                seen.add(cursor)
            raise ConnectorError('连接器目录超过当前分页限制。', code='catalog_limit')
        caps = self.client.server_capabilities
        if caps.tools is not None:
            self.capabilities['tools'] = await collect(self.client.list_tools, 'tools')
        if caps.resources is not None:
            self.capabilities['resources'] = await collect(self.client.list_resources, 'resources')
            self.capabilities['resource_templates'] = await collect(self.client.list_resource_templates, 'resource_templates')
        check_refs(self.capabilities)

    async def submit(self, method: str, args: tuple) -> Future:
        if self.state != 'connected' or self.stopping:
            raise ConnectorError('连接器当前未连接。', code='disconnected')
        if len(self.pending) >= 8:
            raise ConnectorError('连接器并发请求已达上限。', code='busy')
        identifier = str(uuid.uuid4())
        future = Future()
        self.pending[identifier] = future
        await self.send.send((identifier, future, method, args))
        return future

    async def _execute(self, command):
        identifier, future, method, args = command
        value = None
        try:
            if method == 'call':
                result = await self.client.call_tool(*args)
            else:
                result = await self.client.read_resource(*args)
            payload = result.model_dump(mode='json', by_alias=True, exclude_unset=True)
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            value = {'operation_id': identifier, 'delivery': 'received', 'payload': payload,
                     'is_error': getattr(result, 'is_error', None), 'material_status': 'unassessed',
                     'representation': 'sdk_decoded', 'sha256': digest}
        except anyio.get_cancelled_exc_class():
            if self.stopping:
                value = {'operation_id': identifier, 'delivery': 'cancelled',
                         'error': {'code': 'cancelled', 'message': '已停止等待结果；不保证上游操作已回滚。'}}
            # A failed transport also cancels workers. Let the owner supply its
            # actual failure after the entire SDK context has unwound.
        except MCPError as exc:
            error = public_error(exc, self.diagnostics)
            if exc.code == CONNECTION_CLOSED:
                self.error, self.state = error, 'error'
                self.lifetime.cancel()
            else:
                value = {'operation_id': identifier, 'delivery': 'unknown' if exc.code == REQUEST_TIMEOUT else 'received',
                         'error': error}
        except Exception as exc:
            value = {'operation_id': identifier, 'delivery': 'unknown', 'error': public_error(exc, self.diagnostics)}
        finally:
            if value is not None:
                self.pending.pop(identifier, None)
        if value is not None and not future.done():
            future.set_result(value)

    async def stop(self):
        self.stopping = True
        while not hasattr(self, 'lifetime') and not hasattr(self, 'stopped'):
            await anyio.sleep(0)
        if not self.ready.done():
            self.lifetime.cancel()
        elif not self.stopped.is_set():
            try:
                await self.send.send(None)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                pass
        await self.stopped.wait()
