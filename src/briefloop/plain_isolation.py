"""Host restrictions and reported tool use for plain writing turns.

Native/Pi request tool-free execution. Codex/OpenCode preserve read-only host
permissions, which are not a selected-material-only sandbox. Other bridge CLIs
use native permissions in a private external directory. Every host's reported
tool activity is retained; none of these records proves factual support.
Independent review keeps its separate, strict permission requirements.
"""
import os
import stat
import json
import tempfile
from pathlib import Path

# Keep permission selection separate from the strength of its public assurance.
GUARDED = ('codex', 'opencode', 'briefloop-native', 'pi')
TOOL_FREE = ('briefloop-native', 'pi')
RECORD = 'isolation.json'


def level(backend):
    return 'enforced' if backend in TOOL_FREE else 'restricted' if backend in GUARDED else 'observed'


def runtime(backend):
    """Runtime settings for a plain turn on this backend."""
    if backend == 'pi':
        # Pi's own "none" mode closes every tool; see BridgeHarness._host_instructions.
        return {'host_options': {'mode': 'none'}}
    if backend in GUARDED:
        return {'permission': 'read-only'}
    return {}


def working_directory(backend, folder):
    """An empty directory outside the workspace for observed turns, stable across resumes."""
    if backend in GUARDED:
        return folder
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / 'plain-working-directory.json'
    if not marker.exists():
        allocated = Path(tempfile.mkdtemp(prefix='briefloop-plain-turn-'))
        temporary = None
        published = False
        try:
            if os.name == 'nt':
                from .connectors.windows_acl import protect_private
                protect_private(allocated)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder,
                                             prefix='.plain-cwd-', delete=False) as output:
                temporary = Path(output.name)
                json.dump({'path': str(allocated)}, output)
                output.flush()
                os.fsync(output.fileno())
            try:
                # Atomic create-if-absent: concurrent callers retain the first winner.
                os.link(temporary, marker)
                published = True
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not published:
                allocated.rmdir()
    if marker.is_symlink():
        raise ValueError('Plain-turn working directory marker cannot be a symlink')
    path = Path(json.loads(marker.read_text(encoding='utf-8'))['path'])
    metadata = path.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or path.is_symlink()
            or path.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            or not path.name.startswith('briefloop-plain-turn-')
            or (hasattr(os, 'getuid') and metadata.st_uid != os.getuid())
            or (os.name != 'nt' and stat.S_IMODE(metadata.st_mode) & 0o077)):
        raise ValueError('Plain-turn working directory is not a private temporary directory')
    if os.name == 'nt':
        from .connectors.windows_acl import verify_private
        verify_private(path)
    return path


def tool_uses(folder):
    """Tool calls the host reported during the turn, from its projected event log."""
    path = Path(folder) / 'events.jsonl'
    if not path.exists():
        return []
    names = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = (event.get('data') or {}).get('item') if isinstance(event.get('data'), dict) else None
        if isinstance(item, dict) and item.get('type') not in (None, 'agentMessage', 'reasoning', 'userMessage'):
            names[item.get('id') or len(names)] = str(item.get('tool') or item.get('type'))
    return sorted(set(names.values()))


def record(backend, folder):
    """Write and return what is known about one finished plain turn."""
    value = {'backend': backend, 'level': level(backend)}
    value['tools'] = tool_uses(folder)
    Path(folder, RECORD).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return value


def summary(value):
    observed = '、'.join(value.get('tools') or []) or '无'
    if value['level'] == 'enforced':
        return '本轮配置要求关闭工具；引擎报告的工具活动：' + observed + '。该配置不保证正文所有结论均有材料支持。'
    if value['level'] == 'restricted':
        return '本轮使用只读权限，仍可能读取所选材料之外的信息；引擎报告的工具活动：' + observed + '。这不等于工具全部关闭或材料隔离。'
    if value.get('tools'):
        return ('执行引擎（' + value['backend'] + '）不能由 BriefLoop 关闭自带工具；写作期间它报告使用了：'
                + '、'.join(value['tools']) + '。正文可能含所选材料以外的信息，后台补依据时会单独列出找不到原文的结论。')
    return ('执行引擎（' + value['backend'] + '）不能由 BriefLoop 关闭自带工具；本轮未观察到工具调用，'
            '但这只是引擎报告的记录，不等于强制隔离。后台补依据时会单独列出找不到原文的结论。')
