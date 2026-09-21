"""Per-host reasoning choices. Metadata probes never submit a model prompt."""
import json
import threading
import time
from importlib.resources import files

_PROFILES = json.loads(files('briefloop').joinpath('static/runtime-reasoning.json').read_text())
_cache = {}
_lock = threading.Lock()


def options(backend, model, workspace, bridge):
    from .backends import validate_backend
    backend = validate_backend(backend)
    if not isinstance(model, str) or len(model) > 100:
        raise ValueError('无效模型 ID')
    profile = _PROFILES[backend]
    if profile['kind'] not in ('negotiated', 'variant') and not (backend == 'antigravity' and model.startswith('gemini-')):
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
