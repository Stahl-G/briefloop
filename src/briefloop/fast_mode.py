"""Conservative, no-inference Codex Fast capability projection.

Model catalog support is not account entitlement. No credential values or full
host configuration leave this module. API Priority is deliberately not exposed.
"""
import os
from urllib.parse import urlsplit


def validate_tier(value):
    if value not in (None, 'default', 'fast'):
        raise ValueError('service_tier 必须为 null、default 或 fast')
    return value


def _official_url(value, hosts):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (parsed.scheme == 'https' and parsed.hostname in hosts
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


def capability(client, runtime, cwd, *, environment=None):
    """Read host metadata, never start a model turn or refresh credentials."""
    result = {'backend': 'codex', 'model': runtime['model'],
              'model_provider': runtime.get('model_provider'),
              'official_connection': False, 'fast_supported': False,
              'account_availability': 'unknown', 'enabled': False,
              'reason': '尚未确认当前宿主的 Fast 能力'}
    env = os.environ if environment is None else environment
    try:
        config = client.request('config/read', {'cwd': str(cwd), 'includeLayers': False})['config']
        provider = runtime.get('model_provider') or config.get('model_provider') or 'openai'
        account = client.request('account/read', {'refreshToken': False}).get('account') or {}
        providers = config.get('model_providers') or {}
        selected = providers.get(provider) or {}
        # Proxy transport alone is not a provider override. Only effective API
        # endpoint overrides matter; unknown custom providers remain unverified.
        chat_url = config.get('chatgpt_base_url') or 'https://chatgpt.com/backend-api'
        api_url = selected.get('base_url') or env.get('OPENAI_BASE_URL') or config.get('openai_base_url')
        official = (provider == 'openai' and (config.get('model_provider') or 'openai') == provider and not selected
                    and _official_url(chat_url, {'chatgpt.com'})
                    and (not api_url or _official_url(api_url, {'api.openai.com'})))
        result['official_connection'] = official
        if not official:
            result['reason'] = '当前提供商或接口地址尚未确认支持官方 Codex Fast'
            return result
        if account.get('type') != 'chatgpt':
            result.update(account_availability='unavailable', reason='此入口仅支持 ChatGPT 登录的 Codex Fast，不启用 API Priority')
            return result
        model = runtime['model'] if runtime['model'] != 'default' else config.get('model')
        cursor = None
        for _ in range(10):
            page = client.request('model/list', {'includeHidden': True, 'cursor': cursor})
            match = next((item for item in page.get('data', [])
                          if item.get('model') == model or model is None and item.get('isDefault')), None)
            if match:
                tiers = match.get('serviceTiers') or []
                result['fast_supported'] = any(t.get('id') in ('fast', 'priority') for t in tiers)
                break
            cursor = page.get('nextCursor')
            if not cursor:
                break
        if not result['fast_supported']:
            result['reason'] = '宿主未声明该模型支持 Fast'
        else:
            # account/read exposes login and plan, not a Fast entitlement. Do not
            # turn a model capability into an invented organization permission.
            result['reason'] = '模型支持 Fast，但当前宿主未提供可核验的账户 Fast 授权'
        return result
    except Exception:
        # Host errors may contain endpoint/auth details; do not send them to UI.
        result['reason'] = '无法读取当前宿主的 Fast 能力，请检查 Codex 连接'
        return result


def inherited_tier(client, cwd):
    config = client.request('config/read', {'cwd': str(cwd), 'includeLayers': False})['config']
    tier = config.get('service_tier')
    # Null/omission in turn/start inherits the existing THREAD override. Use an
    # explicit standard tier to clear it when the local default has no override.
    return tier or 'default'
