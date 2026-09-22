"""Bounded, on-demand literal search of saved source text. No persistent index."""
import json
import re
from urllib.parse import unquote, urlsplit
from .search_policy import annotate_sources, LABELS
from .store import Conflict, SourceTooLarge, content_hash, dump

MAX_CANDIDATES = 40
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024


def source_media_metadata(store, source):
    path = store.root / 'sources' / (source['id'] + '.provenance.json')
    try:
        if not path.resolve().is_relative_to(store.root):
            raise ValueError('Invalid metadata path')
        # This projection needs only small metadata, never original media bytes.
        with path.open('rb') as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError('Metadata too large')
        meta = json.loads(raw)
        source.update(media_type=meta.get('media_type'), needs_visual=bool(meta.get('needs_visual', False)))
    except FileNotFoundError:
        pass
    except (ValueError, OSError, AttributeError):
        source['media_metadata_unreadable'] = True
    return source


def _metadata_fields(source, needle):
    fields = [k for k in ('name', 'url') if needle in (source.get(k) or '').casefold()]
    # Preserve the library's readable URL filename/title search as well as raw URLs.
    try:
        url = source.get('url') or source['name']
        decoded = unquote(url)
        title = re.sub(r'\.[a-zA-Z0-9]{1,5}$', '', urlsplit(decoded).path.rstrip('/').rsplit('/', 1)[-1])
        title = re.sub('[-_]+', ' ', title)
        if 'url' not in fields and (needle in decoded.casefold() or needle in title.casefold()):
            fields.append('url' if source.get('url') else 'name')
    except ValueError:
        pass
    return list(dict.fromkeys(fields))


def _media_revision(store, source):
    path = store.root / 'sources' / (source['id'] + '.provenance.json')
    try:
        if not path.resolve().is_relative_to(store.root):
            return 'outside'
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except OSError:
        return None


def _hit(line, needle, number):
    offset = line.casefold().find(needle)
    if offset < 0:
        return None
    # Case folding can expand characters. Recover original offsets without a
    # per-character array, including when the match is near the end of a long line.
    folded = 0
    start = end = None
    for index, char in enumerate(line):
        following = folded + len(char.casefold())
        if start is None and following > offset:
            start = index
        if following >= offset + len(needle):
            end = index + 1
            break
        folded = following
    left = max(0, start - 64)
    right = min(len(line), max(end, left + 320), left + 382)
    return {'start_line': number, 'end_line': number,
            'context': ('…' if left else '') + line[left:right] + ('…' if right < len(line) else '')}


def search(store, query, *, run_id='', source_type='', channel='', status='', cursor='', limit=20):
    query = str(query).strip()
    if not query or len(query) > 256 or any(c in query for c in ('\r', '\n', '\x00')):
        raise ValueError('请输入1–256字的单行搜索文字')
    if source_type not in ('', 'web', 'file') or status not in ('', 'ready', 'visual', 'failed'):
        raise ValueError('来源搜索筛选无效')
    if channel not in ('', 'unrecorded', *LABELS):
        raise ValueError('发现渠道无效')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('每页结果数必须为1–20')
    allowed = None
    if run_id:
        run = store.one('runs', run_id)
        if run.get('mode', 'normal') != 'normal' or run_id in store.deleted_reports():
            raise ValueError('报告不可用')
        allowed = set(store.source_ids(run_id))
    rows = annotate_sources(store, store.rows('SELECT * FROM sources ORDER BY created,id'))
    rows = [s for s in rows if (allowed is None or s['id'] in allowed)
            and (not source_type or bool(s.get('url')) == (source_type == 'web'))
            and (not channel or (not s['discovery_providers'] if channel == 'unrecorded'
                                 else channel in s['discovery_providers']))]
    key = content_hash(dump([store.meta('workspace_id'), query, run_id, source_type, channel, status,
                             [[*[s[k] for k in ('id', 'hash', 'status', 'name', 'url', 'discovery_providers')],
                               _media_revision(store, s)] for s in rows]]))
    offset = 0
    if cursor:
        try:
            saved_key, raw_offset = cursor.split(':')
            offset = int(raw_offset)
        except (ValueError, AttributeError):
            raise ValueError('搜索续页位置无效') from None
        if saved_key != key:
            raise Conflict('材料或筛选范围已变化，请重新搜索')
        if not 0 <= offset < len(rows):
            raise ValueError('搜索续页位置无效')
    items = []
    unsearched = []
    used_bytes = scanned = 0
    needle = query.casefold()
    while offset < len(rows) and scanned < MAX_CANDIDATES and len(items) < limit:
        # Reserve a full single-file allowance before admission. Errors may occur
        # after reading, so charge that allowance conservatively on failure.
        if MAX_PAGE_BYTES - used_bytes < MAX_SOURCE_BYTES:
            break
        source = source_media_metadata(store, rows[offset])
        offset += 1
        scanned += 1
        state = 'failed' if source['status'] == 'failed' else 'visual' if source.get('needs_visual') else 'ready'
        metadata_unreadable = source.get('media_metadata_unreadable')
        if status and state != status and not metadata_unreadable:
            continue
        fields = _metadata_fields(source, needle)
        hits = []
        reason = None
        if metadata_unreadable:
            reason = 'metadata_unreadable'
        elif state == 'failed':
            reason = 'failed'
        else:
            try:
                text = store.source_text(source['id'], max_bytes=MAX_SOURCE_BYTES)
                used_bytes += len(text.encode('utf-8'))
                if content_hash(text) != source['hash']:
                    raise Conflict('Source identity changed during search')
                for number, line in enumerate(text.splitlines(), 1):
                    hit = _hit(line, needle, number)
                    if hit:
                        hits.append(hit)
                        if len(hits) == 2:
                            break
                if hits:
                    fields.append('body')
                if state == 'visual':
                    reason = 'visual_partial'
                elif not text.strip():
                    reason = 'empty_text'
            except SourceTooLarge:
                used_bytes += MAX_SOURCE_BYTES
                reason = 'too_large'
            except Conflict:
                used_bytes += MAX_SOURCE_BYTES
                reason = 'changed'
            except (ValueError, OSError):
                used_bytes += MAX_SOURCE_BYTES
                reason = 'unreadable'
        if reason:
            unsearched.append({'source_id': source['id'], 'name': source['name'], 'reason': reason})
        if fields:
            items.append({'source_id': source['id'], 'source_hash': source['hash'],
                          'match_fields': fields, 'body_status': reason or 'searched', 'hits': hits})
    exhausted = offset == len(rows)
    return {'query': query, 'items': items, 'scanned_candidates': scanned, 'total_candidates': len(rows),
            'next_cursor': None if exhausted else key + ':' + str(offset), 'exhausted': exhausted,
            'unsearched_count': len(unsearched), 'unsearched': unsearched,
            'max_source_bytes': MAX_SOURCE_BYTES}
