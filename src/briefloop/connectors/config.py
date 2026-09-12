"""Local connector settings. Saving configuration never executes a command."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import uuid


class ConnectorError(ValueError):
    def __init__(self, message: str, *, code: str = 'invalid_config'):
        super().__init__(message)
        self.code = code


def private_path(path: Path) -> None:
    if path.is_symlink():
        raise ConnectorError('连接器配置路径不能是符号链接。')
    try:
        if os.name == 'nt':
            from .windows_acl import protect_private
            protect_private(path)
        else:
            path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError as exc:
        raise ConnectorError('无法保护连接器本地文件的访问权限，未保存或读取凭据。', code='private_storage_unavailable') from exc


def atomic_json(path: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.save-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            private_path(Path(name))
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_config(value: dict) -> dict:
    allowed = {'name', 'transport', 'url', 'command', 'args', 'cwd', 'timeout_seconds', 'max_response_bytes'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConnectorError('连接配置包含不支持的字段；凭据请单独保存。')
    name = value.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        raise ConnectorError('请输入连接器名称（最多 160 个字符）。')
    transport = value.get('transport')
    result = {'name': name.strip(), 'transport': transport}
    if transport == 'http':
        url = value.get('url', '')
        if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
            raise ConnectorError('请输入有效的 MCP 地址。')
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            _ = parsed.port
            loopback = hostname == 'localhost'
            try:
                loopback |= ipaddress.ip_address(hostname or '').is_loopback
            except ValueError:
                pass
            if (not hostname or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or parsed.scheme not in ('https', 'http')
                    or (parsed.scheme == 'http' and not loopback)):
                raise ValueError()
        except ValueError:
            raise ConnectorError('远程 MCP 地址须使用 HTTPS；本机回环可用 HTTP。地址中不能包含凭据、查询串或片段。') from None
        result['url'] = url
    elif transport == 'stdio':
        command = value.get('command', '')
        args = value.get('args', [])
        if not isinstance(command, str) or not Path(command).is_absolute() or not os.access(command, os.X_OK) or not Path(command).is_file():
            raise ConnectorError('请选择已经安装的可执行文件的绝对路径；不会自动下载安装。')
        if (not isinstance(args, list) or len(args) > 100
                or any(not isinstance(arg, str) or '\0' in arg for arg in args)
                or sum(len(arg) for arg in args) > 32768):
            raise ConnectorError('命令参数必须是长度受限的字符串数组。')
        cwd = value.get('cwd')
        if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir()):
            raise ConnectorError('工作目录必须是存在的绝对路径。')
        result.update(command=command, args=args, cwd=cwd)
    else:
        raise ConnectorError('仅支持 HTTP 和 stdio 连接器。')
    for key, default, low, high in [('timeout_seconds', 20, .1, 120), ('max_response_bytes', 1048576, 1024, 8388608)]:
        item = value.get(key, default)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not low <= item <= high:
            raise ConnectorError(f'{key} 超出允许范围。')
        if key == 'max_response_bytes' and int(item) != item:
            raise ConnectorError('响应大小上限必须是整数。')
        result[key] = item
    return result


def validate_secrets(value: dict | None) -> dict:
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'bearer_token', 'env'}:
        raise ConnectorError('凭据仅支持 bearer_token 和 env。')
    token = value.get('bearer_token', '')
    env = value.get('env', {})
    if not isinstance(token, str) or len(token) > 16384 or '\n' in token or '\r' in token:
        raise ConnectorError('令牌格式无效。')
    if (not isinstance(env, dict) or len(env) > 100
            or any(not isinstance(k, str) or not k or '=' in k or '\0' in k
                   or not isinstance(v, str) or '\0' in v for k, v in env.items())
            or sum(len(k) + len(v) for k, v in env.items()) > 65536):
        raise ConnectorError('环境凭据必须是长度受限的字符串映射。')
    # Do not allow environment injection into the Python transport supervisor.
    if any(k.startswith('PYTHON') or k in ('LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH') for k in env):
        raise ConnectorError('不允许覆盖解释器或动态库加载环境。')
    return {'bearer_token': token, 'env': env}


class LocalConfig:
    def __init__(self, workspace: str | Path):
        self.directory = Path(workspace).resolve() / '.connectors'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise ConnectorError('连接器配置目录不能是符号链接。')
        private_path(self.directory)
        ignore = self.directory / '.gitignore'
        if not ignore.exists():
            descriptor = os.open(ignore, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                private_path(ignore)
                stream.write('*\n')
        private_path(ignore)
        self.credentials = self.directory / 'credentials'
        self.credentials.mkdir(mode=0o700, exist_ok=True)
        if self.credentials.is_symlink():
            raise ConnectorError('凭据目录不能是符号链接。')
        private_path(self.credentials)
        # Migrate existing files too: removing inheritance does not remove explicit grants.
        for credential in self.credentials.iterdir():
            private_path(credential)
        self.path = self.directory / 'connections.json'
        if self.path.is_symlink():
            raise ConnectorError('连接器配置文件不能是符号链接。')
        if self.path.exists():
            private_path(self.path)
        self.records = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(self.records, dict):
            raise ConnectorError('连接器配置格式无效。')

    def persist(self):
        atomic_json(self.path, self.records)

    def credential_path(self, binding: str) -> Path:
        if str(uuid.UUID(binding)) != binding:
            raise ConnectorError('凭据引用无效。')
        return self.credentials / (binding + '.json')

    def get_secrets(self, record: dict) -> dict:
        path = self.credential_path(record['credential_binding'])
        if path.is_symlink():
            raise ConnectorError('凭据文件不能是符号链接。')
        private_path(path)
        return validate_secrets(json.loads(path.read_text(encoding='utf-8')))

    def new_binding(self, secrets: dict) -> str:
        binding = str(uuid.uuid4())
        atomic_json(self.credential_path(binding), validate_secrets(secrets))
        return binding
