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
    access = tasks.access(job['id'])
    token = access['access_token']
    directory = worker.store.root / '.connector-access'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (job['id'] + '-' + uuid.uuid4().hex + '.json')
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'access_token': token, 'url': worker.connector_tool_url}, stream)
        command = shlex.join([sys.executable, str(Path(__file__).with_name('task_client.py')), '--access-file', str(path), '--request', 'request.json'])
        grant = tasks.status(job['id'])['grant']['data']
        selection = [{'connector_id': item['id'], 'resources': item['resources'], 'tools': item['tools']} for item in grant['connectors']]
        yield '\n本轮用户已选择的连接器材料：' + json.dumps(selection, ensure_ascii=False) + '\n' + (
            '使用已授权材料工具读取这些来源；这不依赖是否允许网页搜索。写 JSON 请求到 request.json，再执行：' + command + '\n'
            '请求格式：{"action":"read","connector_id":"...","uri":"...","request_id":"稳定唯一ID"}；'
            '或 {"action":"call","connector_id":"...","name":"...","arguments":{},"request_id":"稳定唯一ID"}。'
            '状态用 {"action":"status"}，回执用 {"action":"receipt","receipt_id":"..."}。'
            '按已选目录和冻结预算执行；不要读取、复制或输出 access-file 内容。返回 admitted 后用 source_id 读取、引用和核查正文；'
            '未接纳的回执不能当事实。不要自行授权、提额、改宿主连接配置或把凭据传给审阅者。\n')
    finally:
        path.unlink(missing_ok=True)
        tasks.release_access(token)
