"""Provider-only Fast eligibility; the Codex service adjudicates requests."""
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
    """Resolve the selected provider from host config, without model/account probes.

    enabled describes provider eligibility, not account authorization or a
    guarantee that the service accepts Fast for the selected model.
    """
    result = {'backend': 'codex', 'model': runtime['model'],
              'model_provider': runtime.get('model_provider'),
              'official_connection': False, 'fast_supported': False,
              'account_availability': 'unknown', 'enabled': False,
              'reason': '尚未确认当前宿主的 Fast 能力'}
    env = os.environ if environment is None else environment
    try:
        config = client.request('config/read', {'cwd': str(cwd), 'includeLayers': False})['config']
        provider = runtime.get('model_provider') or config.get('model_provider') or 'openai'
        providers = config.get('model_providers') or {}
        selected = providers.get(provider) or {}
        # Proxy transport alone is not a provider override. Only effective API
        # endpoint overrides matter; unknown custom providers remain unverified.
        chat_url = config.get('chatgpt_base_url') or 'https://chatgpt.com/backend-api'
        api_url = selected.get('base_url') or env.get('OPENAI_BASE_URL') or config.get('openai_base_url')
        official = (provider == 'openai'
                    and _official_url(chat_url, {'chatgpt.com'})
                    and (not api_url or _official_url(api_url, {'api.openai.com'})))
        result['official_connection'] = official
        if not official:
            result['reason'] = '当前提供商或接口地址尚未确认支持官方 Codex Fast'
            return result
        result.update(fast_supported=True, enabled=True,
                      reason='OpenAI 提供商可选择 Fast；实际请求由服务处理')
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
