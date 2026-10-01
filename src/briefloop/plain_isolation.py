"""How a plain text turn (fast drafts, their evidence pass, fast research planning) is held to its materials.

A plain turn gets every source in its prompt and must answer with text only.
Engines that can actually withhold tools and the network run it that way
("enforced"). Bridge CLIs keep their own tools and only run with their native
permission; BriefLoop cannot switch those off, so it does not claim to. The turn
runs anyway in an empty directory outside the workspace, every tool event the
CLI reports is recorded, and the draft says what was observed ("observed").
Formal delivery still needs an isolated independent review; this only decides
how a working draft is produced and labelled.
"""
import hashlib
import json
import tempfile
from pathlib import Path

# Hosts whose own permission model removes tools and network for the turn.
ENFORCED = ('codex', 'opencode', 'briefloop-native', 'pi')
RECORD = 'isolation.json'


def level(backend):
    return 'enforced' if backend in ENFORCED else 'observed'


def runtime(backend):
    """Runtime settings for a plain turn on this backend."""
    if backend == 'pi':
        # Pi's own "none" mode closes every tool; see BridgeHarness._host_instructions.
        return {'host_options': {'mode': 'none'}}
    if level(backend) == 'enforced':
        return {'permission': 'read-only'}
    return {}


def working_directory(backend, folder):
    """An empty directory outside the workspace for observed turns, stable across resumes."""
    if level(backend) == 'enforced':
        return folder
    digest = hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:20]
    path = Path(tempfile.gettempdir()) / 'briefloop-plain-turns' / digest
    path.mkdir(parents=True, exist_ok=True)
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
    if value['level'] == 'observed':
        value['tools'] = tool_uses(folder)
    Path(folder, RECORD).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return value


def summary(value):
    if value['level'] == 'enforced':
        return '写作期间执行引擎已关闭工具和联网，仅依据所选材料。'
    if value.get('tools'):
        return ('执行引擎（' + value['backend'] + '）不能由 BriefLoop 关闭自带工具；写作期间它报告使用了：'
                + '、'.join(value['tools']) + '。正文可能含所选材料以外的信息，后台补依据时会单独列出找不到原文的结论。')
    return ('执行引擎（' + value['backend'] + '）不能由 BriefLoop 关闭自带工具；本轮未观察到工具调用，'
            '但这只是引擎报告的记录，不等于强制隔离。后台补依据时会单独列出找不到原文的结论。')
