"""Read model IDs from an explicitly configured provider, without inference.

Credentials are sent only to the configured origin. Redirects are refused; neither
upstream error bodies nor exception strings are included in the public result.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

_MODEL_ID = re.compile(r'[A-Za-z0-9_./:-]{1,160}')
_PROTOCOLS = {
    'openai': 'openai', 'openai-compatible': 'openai', 'chat-completions': 'openai',
    'openai-completions': 'openai', 'responses': 'openai', 'openai-responses': 'openai',
    'anthropic': 'anthropic', 'anthropic-messages': 'anthropic',
    'google': 'google', 'google-generative-ai': 'google',
}
_DIAGNOSTICS = {
    'reachable': '已读取提供方实时模型目录；尚未验证推理或工具调用。',
    'auth_failed': '提供方拒绝了目录请求的凭据。',
    'forbidden': '提供方不允许读取模型目录。',
    'insufficient_balance': '提供方报告余额不足。',
    'catalog_unavailable': '提供方未提供此模型目录接口；可手动输入模型 ID。',
    'rate_limited': '模型目录请求受到限流，请稍后刷新。',
    'upstream_unavailable': '提供方模型目录暂不可用。',
    'connection_failed': '无法连接提供方模型目录。',
    'invalid_catalog': '提供方返回的模型目录格式无效或超出读取限制。',
    'redirect_blocked': '提供方目录发生重定向，已阻止转发凭据。',
    'unsupported_protocol': '此协议暂无模型目录读取方式；可手动输入模型 ID。',
    'invalid_endpoint': '请保存有效且不含凭据或查询参数的 API 地址。',
    'http_error': '提供方拒绝了模型目录请求。',
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_provider_catalog(config):
    """Return only safe model IDs and directory status from one saved connection."""
    result = {'kind': 'catalog', 'source': 'provider_api', 'inference_tested': False,
              'tools_tested': False, 'models': [], 'authenticated': 'unknown'}

    def finish(status, **extra):
        return {**result, 'status': status, 'diagnostic': _DIAGNOSTICS[status],
                'refreshed_at': timestamp(), **extra}

    protocol = _PROTOCOLS.get(config.get('api') or config.get('protocol'))
    if not protocol:
        return finish('unsupported_protocol')
    base = str(config.get('base_url') or '').rstrip('/')
    try:
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return finish('invalid_endpoint')
        parsed.port
    except ValueError:
        return finish('invalid_endpoint')
    key = str(config.get('api_key') or '')
    if '\n' in key or '\r' in key:
        return finish('auth_failed')
    # Identify our metadata client honestly. Some provider CDNs reject the
    # generic Python-urllib user agent even for their documented public API.
    from . import __version__
    headers = {'Accept': 'application/json', 'User-Agent': f'BriefLoop/{__version__} model-catalog'}
    if protocol == 'anthropic':
        headers['anthropic-version'] = '2023-06-01'
        if key:
            headers['x-api-key'] = key
        if not parsed.path.rstrip('/').endswith('/v1'):
            base += '/v1'
    elif protocol == 'google':
        if key:
            headers['x-goog-api-key'] = key
        if not re.search(r'/v\d+(?:beta\d*|alpha\d*)?$', parsed.path.rstrip('/')):
            base += '/v1beta'
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    result['credential_sent'] = bool(key)
    url = base + '/models'
    # DeepSeek's Anthropic-compatible inference path shares its documented
    # OpenAI-format model directory at /models on the same configured origin.
    if protocol == 'anthropic' and parsed.hostname == 'api.deepseek.com':
        url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/models', '', ''))
        protocol = 'openai'
        headers = {'Accept': 'application/json', 'User-Agent': headers['User-Agent'],
                   **({'Authorization': 'Bearer ' + key} if key else {})}
    context = ssl.create_default_context()
    trust = ssl.get_default_verify_paths()
    if not trust.cafile and not trust.capath and not os.environ.get('SSL_CERT_FILE') and Path('/etc/ssl/cert.pem').is_file():
        context.load_verify_locations('/etc/ssl/cert.pem')
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))
    models, cursors = set(), set()
    params = {'limit': '1000'} if protocol == 'anthropic' else {'pageSize': '1000'} if protocol == 'google' else {}
    deadline = time.monotonic() + 20
    try:
        for _ in range(20):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish('connection_failed')
            request_url = url + ('?' + urllib.parse.urlencode(params) if params else '')
            request = urllib.request.Request(request_url, headers=headers)
            with opener.open(request, timeout=min(12, remaining)) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return finish('invalid_catalog')
                data = json.loads(raw)
            if not isinstance(data, dict):
                return finish('invalid_catalog')
            entries = data.get('models' if protocol == 'google' else 'data')
            if not isinstance(entries, list):
                return finish('invalid_catalog')
            for entry in entries:
                if not isinstance(entry, dict):
                    return finish('invalid_catalog')
                mid = entry.get('name' if protocol == 'google' else 'id')
                if protocol == 'google' and isinstance(mid, str):
                    mid = mid.removeprefix('models/')
                    # The Native Google adapter invokes generateContent only.
                    if 'generateContent' not in entry.get('supportedGenerationMethods', []):
                        continue
                if not isinstance(mid, str) or not _MODEL_ID.fullmatch(mid) or (key and key in mid):
                    return finish('invalid_catalog')
                models.add(mid)
            if protocol == 'anthropic' and data.get('has_more'):
                cursor, param = data.get('last_id'), 'after_id'
            elif protocol == 'google' and data.get('nextPageToken'):
                cursor, param = data.get('nextPageToken'), 'pageToken'
            else:
                return finish('reachable', models=sorted(models))
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
                return finish('invalid_catalog')
            cursors.add(cursor)
            params[param] = cursor
        return finish('invalid_catalog')
    except urllib.error.HTTPError as exc:
        kinds = {401: 'auth_failed', 402: 'insufficient_balance', 403: 'forbidden',
                 404: 'catalog_unavailable', 405: 'catalog_unavailable', 429: 'rate_limited'}
        status = 'redirect_blocked' if 300 <= exc.code < 400 else kinds.get(exc.code, 'upstream_unavailable' if exc.code >= 500 else 'http_error')
        return finish(status, http_status=exc.code)
    except (urllib.error.URLError, OSError, TimeoutError):
        return finish('connection_failed')
    except (ValueError, TypeError, AttributeError):
        return finish('invalid_catalog')
