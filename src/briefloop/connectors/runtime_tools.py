"""Scope live material access to the main generation call, never its reviewer."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import shlex
import sys
import uuid


@contextmanager
def generation_access(worker, job):
    tasks = getattr(worker, 'connector_tasks', None)
    if tasks is None or not tasks.has_binding(job['id']):
        yield ''
        return
    grant = tasks.status(job['id'])['grant']['data']
    selection = [{'connector_id': item['id'], 'tools': item['tools']} for item in grant['connectors'] if item['tools']]
    if not selection:
        yield ''
        return
    summary = '\n本轮用户已选择的连接器工具：' + json.dumps(selection, ensure_ascii=False) + '\n'
    summary += '已接纳资源见本轮来源索引，未接纳资源见 input.json.connector_resources 的回执；工具由作者按需要确定参数。\n'
    if json.loads(job['payload']).get('agent_backend', 'codex') == 'briefloop-native':
        yield summary + '使用本阶段的 connector_material 工具调用所选工具；实际接纳的 source_id 才能读取和引用。\n'
        return
    access = tasks.access(job['id'])
    token = access['access_token']
    directory = worker.store.root / '.connector-access'
    path = directory / (job['id'] + '-' + uuid.uuid4().hex + '.json')
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'access_token': token, 'url': worker.connector_tool_url}, stream)
        command = shlex.join([sys.executable, str(Path(__file__).with_name('task_client.py')), '--access-file', str(path), '--request', 'request.json'])
        yield summary + (
            '需要调用所选工具时，写 JSON 请求到 request.json，再执行：' + command + '\n'
            '请求格式：{"action":"call","connector_id":"...","name":"...","arguments":{},"request_id":"稳定唯一ID"}。'
            '状态用 {"action":"status"}，回执用 {"action":"receipt","receipt_id":"..."}。'
            '按已选目录和冻结预算执行；不要读取、复制或输出 access-file 内容。返回 admitted 后用 source_id 读取、引用和核查正文；'
            '未接纳的回执不能当事实。不要自行授权、提额、改宿主连接配置或把凭据传给审阅者。\n')
    finally:
        try:
            path.unlink(missing_ok=True)
        finally:
            tasks.release_access(token)
