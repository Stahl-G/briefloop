"""Local CLI client: discover an existing service without initializing a Store."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlencode

from .workspaces import _workspace_id, _validated_info, _alive, _read_api


class RequestError(ValueError):
    """HTTP status without reflecting the server's possibly input-bearing error."""
    def __init__(self, status):
        self.status = status
        super().__init__(f'外部请求失败 (HTTP {status})')


def discover(workspace):
    root = Path(workspace).expanduser().resolve()
    wid = _workspace_id(root) if root.is_dir() else None
    result = {'path': str(root), 'workspace_id': wid, 'ready': False}
    if not wid:
        return {**result, 'status': 'not_workspace', 'message': '请选择已存在的 BriefLoop 工作区；本次未创建目录或数据库'}
    info = _validated_info(root, wid)
    if not info or not _alive(info['pid']):
        return {**result, 'status': 'service_unavailable', 'message': '请先在 BriefLoop 中打开此工作区'}
    if urlsplit(info['url']).hostname != '127.0.0.1':
        return {**result, 'status': 'service_unavailable', 'message': '工作区未使用受支持的本地服务地址'}
    try:
        caps = _read_api(info['url'], '/api/external/capabilities')
        if caps.get('protocol') != 1:
            return {**result, 'status': 'unsupported', 'message': '此版本未提供外部任务接口'}
        service_path = caps.get('workspace_path')
        if (not isinstance(service_path, str) or not Path(service_path).is_absolute()
                or Path(service_path).resolve() != root or caps.get('workspace_id') != wid):
            return {**result, 'status': 'identity_changed', 'message': '服务与选定工作区的路径或身份不匹配，未连接'}
        # Verify only the narrow identity response before reading runtime/task
        # state or obtaining a session token from a possibly copied service URL.
        runtime = _read_api(info['url'], '/api/runtime')
        status = _read_api(info['url'], '/api/service-status')
        if runtime.get('server_pid') != info['pid'] or status.get('pid') != info['pid'] or status.get('workspace_id') != wid:
            return {**result, 'status': 'identity_changed', 'message': '服务与工作区身份不匹配，未连接'}
        ready = bool(runtime.get('worker_alive')) and not status.get('draining')
        return {**result, 'url': info['url'], 'pid': info['pid'], 'ready': ready,
                'status': 'ready' if ready else 'not_ready', 'capabilities': caps}
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        return {**result, 'status': 'service_unavailable', 'message': '无法核验现有服务；本次未启动服务或任务'}


class Client:
    def __init__(self, workspace):
        self.info = discover(workspace)
        if not self.info['ready']:
            raise ValueError(self.info.get('message', '工作区服务尚未就绪'))

    def request(self, body):
        return self._post('/api/external/action', body)

    def stop_job(self, job_id):
        return self._post('/api/stop', {'job_id': job_id})

    def _post(self, path, body):
        if not isinstance(body, dict):
            raise ValueError('请求必须是 JSON 对象')
        wid = body.get('workspace_id', self.info['workspace_id'])
        if wid != self.info['workspace_id']:
            raise ValueError('请求不属于选定的工作区')
        token = _read_api(self.info['url'], '/api/session')['token']
        endpoint = urlsplit(self.info['url'])
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=15)
        try:
            connection.request('POST', path, body=json.dumps({**body, 'workspace_id': wid}, ensure_ascii=False).encode(),
                               headers={'Content-Type': 'application/json', 'X-BriefLoop-Token': token})
            response = connection.getresponse()
            data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError('响应过大；请通过 BriefLoop 页面读取报告')
            value = json.loads(data)
            if response.status != 200:
                raise RequestError(response.status)
            return value
        finally:
            connection.close()

    def download(self, job_id, output):
        status = self.request({'action': 'query', 'job_id': job_id})
        if not status.get('artifact_available'):
            raise ValueError(status.get('artifact_error', '该任务尚无可下载的 Word 文件'))
        destination = Path(output).expanduser().resolve()
        expected = status['sha256']
        if destination.exists():
            if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
                return {'path': str(destination), 'sha256': expected, 'reused': True, 'version_id': status['version_id']}
            raise ValueError('目标文件已存在且内容不同；请另选文件名，未覆盖')
        endpoint = urlsplit(self.info['url'])
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=15)
        temporary = None
        try:
            connection.request('GET', '/api/export-file?' + urlencode({'job': job_id, 'workspace_id': self.info['workspace_id']}))
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('Word 下载失败；请查询原任务，未重新排队')
            # Stream to a sibling file and verify before publishing the result.
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.briefloop-download-', delete=False) as stream:
                temporary = Path(stream.name)
                digest = hashlib.sha256()
                while block := response.read(1024 * 1024):
                    stream.write(block)
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError('Word 文件校验不一致，未保存为目标文件')
            # Same-directory linking publishes complete verified bytes atomically
            # and fails if another process created the destination meanwhile.
            os.link(temporary, destination)
            return {'path': str(destination), 'sha256': expected, 'reused': False, 'version_id': status['version_id']}
        finally:
            connection.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)
