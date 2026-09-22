"""Per-host reasoning choices. Metadata probes never submit a model prompt."""
import json
import threading
import time
from importlib.resources import files

_PROFILES = json.loads(files('briefloop').joinpath('static/runtime-reasoning.json').read_text())
_cache = {}
_lock = threading.Lock()


def options(backend, model, workspace, bridge, native=None):
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
