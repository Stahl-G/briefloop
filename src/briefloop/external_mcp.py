"""Stdio MCP adapter to the existing, authenticated local external client.

No Store, Worker, credentials, model calls or HTTP listener live in this process.
The selected canonical path and durable workspace identity cannot be changed by
tool arguments or by replacing the workspace while a client is connected.
"""
import http.client
import json
from pathlib import Path

import anyio
from jsonschema import Draft202012Validator
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from . import __version__
from .external_client import Client, RequestError, discover
from .external_requests import FIELDS, MUTATIONS
from .workspaces import _workspace_id


ID = {'type': 'string', 'minLength': 1}
REQUEST_ID = {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'}
PROPERTIES = {
    'source_id': ID, 'job_id': ID, 'version_id': ID, 'base_version': ID,
    'requirements': {'type': 'object'}, 'editor_document': {'type': 'object'},
    'source_ids': {'type': 'array', 'items': ID},
}
DESCRIPTIONS = {
    'discover': '检查启动时选定工作区的现有服务和能力；不创建或启动服务。',
    'inspect': '列出选定工作区最近 100 个来源和 20 个稿件；返回真实 ID。',
    'source': '读取指定来源正文和出处。source_id 必须来自该工作区。',
    'submit': '提交报告，立即返回 job_id/run_id；用 query 查进度。沿用工作区模型、企业背景与需求规则。每次新写操作使用稳定 request_id；响应丢失仅重发原 ID 和原内容。',
    'query': '查询原 job_id 的实际状态、安全错误、版本和 Word 文件可用性；不重建失败任务。latest_version_id 可来自后续修订。',
    'read': '读取精确 version_id 的 Markdown 和完整 editor_document。',
    'revise': '保存 base_version 的完整 editor_document 为新版本，保留图表、引用和未修改节点；已有新版本时报冲突。用稳定 request_id 重试原内容。',
    'export': '排队生成明确 version_id 的 Word 工作稿，立即返回 job_id；query 至 artifact_available=true 后 download。不代表正式交付审核通过。用稳定 request_id 重试原内容。',
    'download': '将已完成导出 job_id 的 Word 保存到明确 output 文件路径；父目录须已存在。校验 SHA256、复用相同文件，不覆盖不同内容，不重新排队。',
}


class WorkspaceUnavailable(ValueError):
    pass


def failure(code, message):
    return {'status': 'error', 'code': code, 'message': message}


def request_failure(status):
    if status == 409:
        return failure('conflict', '稿件已有更新或 request_id 已用于不同内容；请核对原请求，并重新读取最新版本后合并修订')
    if status == 400:
        return failure('invalid_request', '请求参数或报告配置不符合要求；请检查需求、来源 ID、完整富文档及工作区设置')
    if status == 403:
        return failure('access_denied', '本地服务拒绝鉴权；请重新连接用户选定的工作区')
    if status == 503:
        return failure('service_unavailable', '本地服务尚未就绪或正在停止接收任务；请查询原任务，不自动重建')
    return failure('request_failed', '本地服务请求失败；写操作结果可能未知，请保留原 request_id 和原内容重试')


def tools():
    catalog = []
    for action, description in DESCRIPTIONS.items():
        if action == 'download':
            properties = {'job_id': ID, 'output': {'type': 'string', 'minLength': 1}}
            required = list(properties)
        else:
            fields = FIELDS.get(action, set())
            properties = {field: PROPERTIES[field] for field in sorted(fields)}
            required = sorted(fields - ({'source_ids'} if action == 'submit' else set()))
            if action in MUTATIONS:
                properties['request_id'] = REQUEST_ID
                required.append('request_id')
        catalog.append(Tool(
            name='briefloop_' + action, description=description,
            input_schema={'type': 'object', 'properties': properties,
                          'required': required, 'additionalProperties': False},
            annotations=ToolAnnotations(
                read_only_hint=action not in MUTATIONS | {'download'},
                destructive_hint=False, idempotent_hint=True,
                # Only submit can invoke models or research outside this
                # workspace. Revise saves as author=agent, without learning.
                open_world_hint=action == 'submit',
            ),
        ))
    return catalog


class WorkspaceAdapter:
    def __init__(self, workspace):
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            raise ValueError('--workspace 必须是用户选定工作区的绝对路径')
        self.root = path.resolve()
        self.workspace_id = _workspace_id(self.root) if self.root.is_dir() else None

    def discovery(self):
        info = discover(self.root)
        if info.get('workspace_id') != self.workspace_id:
            return {'path': str(self.root), 'workspace_id': self.workspace_id,
                    'ready': False, 'status': 'identity_changed',
                    'message': '工作区身份已变化；请重新启动 MCP 连接到用户选择的工作区'}
        return info

    def invoke(self, action, arguments):
        if action == 'discover':
            return self.discovery()
        if not self.workspace_id:
            raise WorkspaceUnavailable()
        try:
            client = Client(self.root)
        except ValueError:
            raise WorkspaceUnavailable() from None
        if client.info['workspace_id'] != self.workspace_id:
            raise WorkspaceUnavailable()
        if action == 'download':
            return client.download(arguments['job_id'], arguments['output'])
        return client.request({'action': action, 'workspace_id': self.workspace_id, **arguments})


def create_server(workspace):
    adapter = WorkspaceAdapter(workspace)
    catalog = tools()
    validators = {tool.name: Draft202012Validator(tool.input_schema) for tool in catalog}
    limiter = anyio.CapacityLimiter(4)

    async def list_tools(context, params):
        return ListToolsResult(tools=catalog)

    async def call_tool(context, params):
        arguments = params.arguments if params.arguments is not None else {}
        validator = validators.get(params.name)
        # Never echo invalid arguments (which may contain credential-shaped
        # values), validation traces or internal exception details to clients.
        if validator is None:
            value = failure('unknown_tool', '未知 BriefLoop 工具')
            failed = True
        elif not validator.is_valid(arguments):
            value = failure('invalid_arguments', '工具参数不符合公布的 schema；不得传入其他工作区或服务地址')
            failed = True
        else:
            try:
                action = params.name.removeprefix('briefloop_')
                value = await anyio.to_thread.run_sync(adapter.invoke, action, arguments, limiter=limiter)
                failed = False
            except WorkspaceUnavailable:
                value = failure('workspace_unavailable', '选定工作区服务尚未就绪，或服务路径/身份不匹配；请在 BriefLoop 打开用户选定的工作区后重新连接')
                failed = True
            except RequestError as exc:
                value = request_failure(exc.status)
                failed = True
            except (OSError, http.client.HTTPException):
                value = failure('local_io_failed', '本地服务通信或文件操作失败；写操作结果可能未知，请使用原 request_id 和原内容重试')
                failed = True
            except (ValueError, KeyError):
                value = failure('invalid_request', '请求、完整富文档或下载目标不符合要求；请检查指定 ID、父目录及目标文件，不覆盖不同内容')
                failed = True
            except Exception:
                value = failure('request_failed', '本地外部请求失败；写操作结果可能未知，请用原 request_id 和原内容重试，勿新建任务')
                failed = True
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(value, ensure_ascii=False))],
                              structured_content=value, is_error=failed)

    return Server('BriefLoop', version=__version__, on_list_tools=list_tools, on_call_tool=call_tool,
                  instructions='仅操作启动时用户选定的本机工作区。先 discover 确认 ready，再 inspect/source。写操作保留原 request_id 与完整请求；submit/export 返回 job_id 后查询，不等待模型生成。修订先读取精确版本、保留完整富文档；Word 是指定版本的工作稿。MCP 取消或断开不取消已接收的后台任务；重连后查询原任务。')


def serve(workspace):
    server = create_server(workspace)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
