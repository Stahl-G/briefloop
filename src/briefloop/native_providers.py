"""Local native-engine provider configuration, never included in workspace data."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

_LOCK = threading.RLock()
PROTOCOLS = {'chat-completions':'openai-completions', 'responses':'openai-responses', 'anthropic-messages':'anthropic-messages', 'openai': 'openai-completions', 'openai-compatible': 'openai-completions',
             'openai-responses': 'openai-responses', 'anthropic': 'anthropic-messages', 'google': 'google-generative-ai'}


def config_path():
    return Path.home() / '.config' / 'briefloop' / 'native-engine' / 'providers.json'


def _read():
    path = config_path()
    if path.is_symlink():
        raise ValueError('内置引擎配置文件不能是符号链接')
    return json.loads(path.read_text()) if path.exists() else {}


def configurations():
    with _LOCK:
        return [{k: v for k, v in row.items() if k != 'api_key'} | {'has_key': bool(row.get('api_key'))}
                for row in _read().values()]


def save(body):
    provider = str(body.get('provider') or '').strip()
    model = str(body.get('model') or '').strip()
    url = str(body.get('base_url') or '').strip().rstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', provider) or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}', model):
        raise ValueError('请输入有效的提供商 ID 和模型 ID')
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API 地址需为 HTTP(S) 接口地址，不含凭据、查询或片段')
    protocol = body.get('protocol') or 'openai-compatible'
    if protocol not in PROTOCOLS:
        raise ValueError('内置引擎暂不支持此接口协议')
    with _LOCK:
        rows = _read(); previous = rows.get(provider + '/' + model, {})
        key = str(body.get('api_key') or previous.get('api_key') or '').strip()
        if not key or key.startswith('!') or '\n' in key or '\r' in key:
            raise ValueError('请填写有效 API Key；密钥仅保存在本机')
        context = int(body['context_limit']) if body.get('context_limit') else None
        output = int(body['output_limit']) if body.get('output_limit') else None
        if (context is not None and not 1024 <= context <= 2000000) or (output is not None and not 256 <= output <= (context or 2000000)):
            raise ValueError('上下文和输出上限不在有效范围内')
        rows[provider + '/' + model] = {'provider': provider, 'model': model, 'name': str(body.get('name') or provider),
            'protocol': protocol, 'api': PROTOCOLS[protocol], 'base_url': url, 'api_key': key, 'context_limit': context, 'output_limit': output,
            'supports_images': body.get('supports_images') if type(body.get('supports_images')) is bool else None}
        # One endpoint/key per provider, matching engine provider scope.
        for row in rows.values():
            if row['provider'] == provider:
                row.update(api_key=key, base_url=url, protocol=protocol, api=PROTOCOLS[protocol])
        path = config_path();path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.providers-')
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(rows, stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    return {'model': provider + '/' + model, 'saved': True}


def catalog(body):
    provider = body.get('provider')
    return {'status':'reachable', 'models': [row['model'] for row in configurations() if row['provider'] == provider],
            'source': 'native_config', 'diagnostic': '显示已登记模型；也可直接输入新的模型 ID 保存。'}
