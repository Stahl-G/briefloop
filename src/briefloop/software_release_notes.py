"""Version-bound changelog text; independent of update installation decisions."""
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RELEASES = 'https://github.com/Stahl-G/briefloop/releases/tag/v'
_cache = {}
_lock = threading.Lock()


def bundled_notes(version):
    try:
        value = json.loads((Path(__file__).parent / 'static' / 'release-notes.json').read_text(encoding='utf-8'))
        if value.get('version') == version and isinstance(value.get('notes'), str):
            return {'version': version, 'state': 'loaded' if value['notes'].strip() else 'empty',
                    'notes': value['notes'], 'source': 'bundled', 'url': RELEASES + version}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def release_notes(version):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('发布说明需要明确的稳定版本号')
    with _lock:
        cached = _cache.get(version)
        if cached and time.monotonic() - cached[0] < 300:
            return dict(cached[1])
    result = {'version': version, 'notes': '', 'source': 'github', 'url': RELEASES + version}
    try:
        request = Request('https://api.github.com/repos/Stahl-G/briefloop/releases/tags/v' + version,
                          headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'BriefLoop-Release-Notes'})
        with urlopen(request, timeout=10) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError('release response too large')
        value = json.loads(raw)
        if (value.get('tag_name') != 'v' + version or value.get('draft') is not False
                or value.get('prerelease') is not False or not isinstance(value.get('body'), (str, type(None)))):
            raise ValueError('release version does not match')
        notes = (value.get('body') or '')[:20000]
        result.update(state='loaded' if notes.strip() else 'empty', notes=notes)
        with _lock:
            if len(_cache) >= 20:
                _cache.clear()
            _cache[version] = (time.monotonic(), dict(result))
    except HTTPError as exc:
        result.update(state='unavailable' if exc.code == 404 else 'error',
                      message='该版本尚无公开发布说明。' if exc.code == 404 else f'发布说明获取失败（HTTP {exc.code}），请稍后重试或查看官方发布。')
    except (OSError, URLError, ValueError, TypeError, AttributeError):
        result.update(state='error', message='无法获取该版本的发布说明，请检查网络后重试；当前安装不受影响。')
    return result
