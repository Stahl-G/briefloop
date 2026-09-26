"""Per-host reasoning choices. Metadata probes never submit a model prompt."""
import json
import threading
import time
from importlib.resources import files

_PROFILES = json.loads(files('briefloop').joinpath('static/runtime-reasoning.json').read_text())
_cache = {}
_lock = threading.Lock()


def options(backend, model, workspace, bridge, native=None, opencode=None):
    from .backends import validate_backend
    backend = validate_backend(backend)
    if not isinstance(model, str) or len(model) > 100:
        raise ValueError('无效模型 ID')
    if backend == 'briefloop-native':
        if native is None:
            raise ValueError('内置引擎目录不可用')
        selected = next((item for item in native.list_models() if item['id'] == model), None)
        if selected is None:
            raise ValueError('所选模型尚未登记或不可用，请先配置内置引擎提供商')
        return {'backend': backend, 'kind': 'levels',
                'options': [{'id': level, 'name': level} for level in selected.get('thinking_levels', [])],
                'note': '使用内置 Pi SDK 的模型档位；模型默认沿用 BriefLoop 的 low，实际能力取决于提供商。'}
    profile = _PROFILES[backend]
    if backend == 'opencode':
        # Models and variants must come from the same owned provider API. V2
        # removed `models --verbose`; probing that old CLI would silently show
        # an empty effort list even while the model picker uses the live API.
        if opencode is None:
            raise ValueError('OpenCode 模型目录不可用')
        if not model or model == 'default':
            return {**profile, 'backend': backend, 'options': [], 'source': 'host',
                    'availability': 'select_model', 'note': '选择具体模型后读取其公开档位；未推断宿主默认模型的能力。'}
        selected = next((item for item in opencode.list_models() if item['id'] == model), None)
        if selected is None:
            raise ValueError('OpenCode 未在当前目录中返回所选模型，请刷新模型目录')
        variants = selected.get('variants')
        if variants is None:
            return {**profile, 'backend': backend, 'options': [], 'source': 'host',
                    'availability': 'not_advertised', 'note': 'OpenCode 未公开所选模型的档位；未添加推测选项。'}
        return {**profile, 'backend': backend, 'source': 'host',
                'availability': 'advertised' if variants else 'no_variants',
                'options': [{'id': value, 'name': value} for value in variants],
                'note': '使用 OpenCode 当前模型目录公开的档位。' if variants else 'OpenCode 当前目录未提供该模型的独立档位。'}
    probe = (profile['kind'] in ('negotiated', 'variant') or backend == 'codex'
             or backend == 'antigravity' and model.startswith('gemini-'))
    if not probe:
        return {**profile, 'backend': backend,
                'options': [{'id': value, 'name': value} for value in profile.get('levels', [])]}
    key = (backend, model, str(workspace))
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
    result = bridge.call('reasoning_options', {'runtime_id': backend, 'model': model,
                         'cwd': str(workspace)}, timeout=45)
    result = {**result, 'backend': backend}
    with _lock:
        if len(_cache) >= 64:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), result)
    return result
