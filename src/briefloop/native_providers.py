"""Local native-engine provider configuration, never included in workspace data."""
import json
import copy
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from .connectors.config import atomic_json, ConnectorError

_LOCK = threading.RLock()
_CATALOG_LOCK = threading.Lock()
_CATALOG_PENDING = {}
_CATALOG_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="native-catalog")
PROTOCOLS = {'chat-completions':'openai-completions', 'responses':'openai-responses', 'anthropic-messages':'anthropic-messages', 'openai': 'openai-completions', 'openai-compatible': 'openai-completions',
             'openai-responses': 'openai-responses', 'anthropic': 'anthropic-messages', 'google': 'google-generative-ai'}


def config_path():
    return Path.home() / '.config' / 'briefloop' / 'native-engine' / 'providers.json'


def _read():
    path = config_path()
    if path.is_symlink():
        raise ValueError('内置引擎配置文件不能是符号链接')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


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
        reasoning = body.get('supports_reasoning', previous.get('supports_reasoning'))
        if reasoning is not None and type(reasoning) is not bool:
            raise ValueError('模型推理能力必须为 true、false 或 null')
        rows[provider + '/' + model] = {'provider': provider, 'model': model, 'name': str(body.get('name') or provider),
            'protocol': protocol, 'api': PROTOCOLS[protocol], 'base_url': url, 'api_key': key, 'context_limit': context, 'output_limit': output,
            'supports_images': body.get('supports_images') if type(body.get('supports_images')) is bool else None,
            'supports_reasoning': reasoning}
        # One endpoint/key per provider, matching engine provider scope.
        for row in rows.values():
            if row['provider'] == provider:
                row.update(api_key=key, base_url=url, protocol=protocol, api=PROTOCOLS[protocol])
        path = config_path();path.parent.mkdir(parents=True, exist_ok=True)
        # Reuse the existing private, atomic writer: POSIX mode or verified
        # Windows DACL is applied before any credential bytes reach the file.
        try:
            atomic_json(path, rows)
        except ConnectorError as exc:
            raise ValueError('无法保护内置引擎本地配置的访问权限，未保存凭据。') from exc
    return {'model': provider + '/' + model, 'saved': True}


def _catalog_future(config):
    # Register before queuing work, so concurrent page loads also coalesce
    # when all provider workers are occupied. Completed results are not cached.
    from .provider_catalog import read_provider_catalog
    identity = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).digest()
    with _CATALOG_LOCK:
        if pending := _CATALOG_PENDING.get(identity):
            return pending
        pending = Future()
        _CATALOG_PENDING[identity] = pending

    def read():
        try:
            result = read_provider_catalog(config)
            result.update(provider=config['provider'], name=config.get('name') or config['provider'])
            pending.set_result(result)
        except Exception as exc:
            pending.set_exception(exc)
        finally:
            with _CATALOG_LOCK:
                _CATALOG_PENDING.pop(identity, None)

    _CATALOG_POOL.submit(read)
    return pending


def _catalog_config(config, refresh=False):
    # No TTL: explicit refresh and ordinary fresh reads both contact the API.
    return copy.deepcopy(_catalog_future(config).result())


def _provider_configs():
    with _LOCK:
        configs = {}
        for row in _read().values():
            configs.setdefault(row['provider'], dict(row))
        return configs


def catalog(body):
    config = _provider_configs().get(body.get('provider'))
    if config is None:
        raise ValueError('请先保存 Native 提供方连接')
    return _catalog_config(config, refresh=bool(body.get('refresh', True)))


def model_catalog(refresh=False):
    from .provider_catalog import timestamp
    configs = _provider_configs()
    pending = [_catalog_future(config) for config in configs.values()]
    providers = [copy.deepcopy(future.result()) for future in pending]
    models = [{'id': item['provider'] + '/' + mid, 'provider': item['provider'], 'name': mid,
               'catalog_source': item['source'], 'catalog_status': item['status'],
               'refreshed_at': item['refreshed_at'], 'inference_tested': False}
              for item in providers if item['status'] == 'reachable' for mid in item['models']]
    statuses = {item['status'] for item in providers}
    status = (next(iter(statuses)) if len(statuses) == 1 else 'partial') if providers else 'unconfigured'
    return {'models': models, 'count': len(models), 'providers': providers,
            'source': 'provider_api', 'status': status, 'refreshed_at': timestamp(),
            'inference_tested': False, 'tools_tested': False}
