"""Attempt-scoped Scout evidence. The runner copies text; the model interprets it."""
import bisect
import hashlib
import json
import re
from pathlib import Path

from .models import ScoutResult
from .scout_tools import _squash, _NOISE, _QUOTES
from .store import dump, content_hash


def _key(store, config):
    target = Path(config['result_file']).resolve()
    if not target.is_relative_to(store.root) or not config.get('attempt_id'):
        raise ValueError('证据记录缺少本轮执行身份或有效结果路径')
    identity = [config['run_id'], str(target), config['attempt_id']]
    return 'scout_evidence:' + hashlib.sha256(dump(identity).encode()).hexdigest()


def begin(store, config):
    # A new turn has a new attempt ID. Never reset an existing attempt on retry.
    key = _key(store, config)
    with store.tx() as c:
        c.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',
                  (key, dump({'status': 'active', 'accepted': {}, 'rejected': {}, 'discarded': {}})))


def close(store, config):
    key = _key(store, config)
    with store.tx() as c:
        row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if row:
            data = json.loads(row['value']); data['status'] = 'closed'
            c.execute('UPDATE meta SET value=? WHERE key=?', (dump(data), key))


def _load(c, key):
    row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    if not row or json.loads(row['value'])['status'] != 'active':
        raise ValueError('本轮证据记录已关闭，不能接纳迟到提交')
    return json.loads(row['value'])


def _excerpt(store, config, item):
    sid = item['source_id']
    if sid not in store.source_ids(config['run_id']):
        raise ValueError('来源未登记到本报告')
    text = store.source_text(sid)
    if item.get('source_hash') != content_hash(text):
        raise ValueError('来源版本不匹配，请重新 source_read 并使用返回的 source_hash')
    quote = item.get('quote', '')
    if not isinstance(quote, str) or not 8 <= len(_squash(quote)) <= 240:
        raise ValueError('quote 需为 8–240 字符的连续原文锚点，包含关键主体、指标或数值')
    locator = re.fullmatch(r'line\s+(\d+)(?:\s*-\s*(\d+))?', item.get('locator', ''))
    if not locator:
        raise ValueError('locator 使用单一行段，例如 line 12-18')
    first, last = int(locator[1]), int(locator[2] or locator[1])
    if first < 1 or last < first or last - first >= 60:
        raise ValueError('行段应从 1 开始，最多 60 行；分别记录不同段落')
    lines = text.splitlines()
    # Map formatting-tolerant matching back to untouched original line numbers.
    clean, line_map, char_map = [], [], []
    for number, line in enumerate(lines, 1):
        for offset, char in enumerate(line.translate(_QUOTES)):
            if not _NOISE.fullmatch(char):
                clean.append(char); line_map.append(number); char_map.append(offset)
    clean = ''.join(clean); anchor = _squash(quote)
    def matches(start, end):
        positions = []; pos = clean.find(anchor, start, end)
        while pos >= 0:
            positions.append(pos)
            if len(positions) > 1: break
            pos = clean.find(anchor, pos + 1, end)
        return positions
    left = bisect.bisect_left(line_map, first); right = bisect.bisect_right(line_map, last)
    char_start, char_end = item.get('start_char'), item.get('end_char')
    char_range = char_start is not None or char_end is not None
    if char_range:
        if (type(char_start) is not int or type(char_end) is not int or first != last
                or first > len(lines) or not 0 <= char_start < char_end <= len(lines[first-1])):
            raise ValueError('字符范围仅用于单行，start_char 从 0 开始，end_char 不含结束字符且不可越界')
        offsets = char_map[left:right]
        right = left + bisect.bisect_left(offsets, char_end)
        left += bisect.bisect_left(offsets, char_start)
    found = matches(left, right); relocated = False
    if not found and not char_range:
        found = matches(0, len(clean)); relocated = True
    if len(found) != 1:
        raise ValueError('锚点不存在或有多处匹配，请给更完整锚点及准确行段')
    if relocated:
        first, last = line_map[found[0]], line_map[found[0] + len(anchor) - 1]
    excerpt = lines[first-1][char_start:char_end] if char_range else '\n'.join(lines[first-1:last])
    if last > len(lines) or len(excerpt) > 4000 or last-first >= 60:
        raise ValueError('摘录过长或行号越界，请收窄行段；长单行用 source_read 的 start_char 定位后，给 start_char/end_char（从 0 开始、不含结束字符）。保留必要单位、表头与脚注')
    value = {k: item[k] for k in ('source_id', 'facts', 'conflicts', 'coverage_status', 'claim_ids') if k in item}
    value.update(locator=f'line {first}-{last}' if first != last else f'line {first}', excerpt=excerpt)
    source = ScoutResult.model_validate({'sources': [value]}).sources[0]
    for claim_id in source.claim_ids:
        from .evidence import record as claim_record
        claim = claim_record(store, 'claims', claim_id)
        if claim['run_id'] != config['run_id'] or claim['data'].get('claim_role') != 'source_statement':
            raise ValueError('claim_ids 只能引用本报告的来源陈述')
    return {'source': source.model_dump(), 'source_hash': content_hash(text), 'relocated': relocated}


def record(store, config, args):
    items, discard = args.get('items', []), args.get('discard', [])
    if not isinstance(items, list) or not isinstance(discard, list) or len(items) + len(discard) > 16:
        raise ValueError('每次最多记录或移除 16 条证据')
    ids = [x.get('id') if isinstance(x, dict) else None for x in items + discard]
    if any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('每条提供唯一稳定 id；修正重用原 id')
    if any(not isinstance(x.get('reason'), str) or not x['reason'].strip() for x in discard):
        raise ValueError('移除证据需要 reason，未覆盖内容须写入最终 gaps')
    accepted, rejected = [], []
    with store.tx() as c:
        key = _key(store, config); data = _load(c, key)
        if len(set(data['accepted']) | set(data['rejected']) | set(ids)) > 500:
            raise ValueError('本槽位证据超过 500 条，请聚焦任务')
        for item in items:
            eid = item['id']
            try:
                value = _excerpt(store, config, item)
            except (ValueError, KeyError, OSError) as exc:
                message = str(exc)[:500]; data['rejected'][eid] = message
                rejected.append({'id': eid, 'error': message}); continue
            data['accepted'][eid] = value; data['rejected'].pop(eid, None); data['discarded'].pop(eid, None)
            accepted.append({'id': eid, 'locator': value['source']['locator'],
                             'excerpt': value['source']['excerpt'], 'relocated': value['relocated']})
        for item in discard:
            data['accepted'].pop(item['id'], None); data['rejected'].pop(item['id'], None)
            data['discarded'][item['id']] = item['reason']
        c.execute('UPDATE meta SET value=? WHERE key=?', (dump(data), key))
    return {'accepted': accepted, 'rejected': rejected, 'total': len(data['accepted']),
            'pending_ids': list(data['rejected'])}


def collect(store, config):
    with store.tx() as c:
        data = _load(c, _key(store, config))
        if data['rejected']:
            raise ValueError('还有未处理证据：' + ', '.join(data['rejected']) + '；只重交这些 id，或说明原因移除')
        for item in data['accepted'].values():
            if content_hash(store.source_text(item['source']['source_id'])) != item['source_hash']:
                raise ValueError('已记录来源版本变化，请重新读取并记录')
        return [x['source'] for x in data['accepted'].values()], data['discarded']
