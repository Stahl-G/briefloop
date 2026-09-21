"""Saved writer candidates shared by Native tools and host CLI commands.

Candidates are attempt-local working files, not another report store. Only a
checked complete revision can become draft.json; publication still uses Store.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from .store import dump

CHECK_VERSION = 1


def _hash(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def _root(store, config):
    from .analyst import _sections_file
    return _sections_file(store, config).with_suffix('')


@contextmanager
def guard(store, config):
    from .platform_support import WorkspaceLock
    try:
        lock = WorkspaceLock(_root(store, config))
    except RuntimeError as exc:
        raise ValueError('稿件正在保存或检查，请稍后重试同一操作') from exc
    try:
        yield
    finally:
        lock.close()


def _write(path, value):
    from .native_roles import _atomic
    _atomic(path, dump(value))


def _read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _packet_hash(config):
    root = Path(config['packet_root']).resolve()
    manifest = root.parent / 'writing-packet.json'
    if manifest.exists():
        for name, expected in _read(manifest)['files'].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('已冻结写作材料被修改：' + name)
    return _hash({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in ('input.json', 'source-index.json')})


def _sections_hash(store, config):
    from .analyst import _sections_file
    path = _sections_file(store, config)
    return _hash(_read(path) if path.exists() else {})


def _revision(args):
    revision = args.get('revision')
    if set(args) != {'revision'} or not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('只传保存回执中的 revision；正文或元数据修改后先 save_draft，再检查新版本')
    return revision


def save(store, config, args):
    from .analyst import validate_draft, _assemble_sections
    with guard(store, config):
        value = dict(args)
        base = value.pop('base_revision', None)
        root = _root(store, config)
        if base is not None:
            prior = _candidate(store, config, {'revision': base}, allow_section_changes='section_ids' in value)
            value = {**prior['draft'], **value}
            if 'section_ids' in value:
                value.pop('editor_document', None)
                value.pop('markdown', None)
        section_hash = _sections_hash(store, config) if 'section_ids' in value else None
        value = validate_draft(store, config, _assemble_sections(store, config, value))
        candidate = {'draft': value, 'packet_hash': _packet_hash(config),
                     'attempt_id': config['attempt_id'], 'sections_hash': section_hash}
        revision = _hash(candidate)
        _write(root / (revision + '.json'), candidate)
        _write(root / 'current.json', {'revision': revision})
        from .length import count_brief
        return {'revision': revision, 'status': 'saved', 'body_units': count_brief(value['markdown']),
                'review_status': 'not_reviewed', 'next': 'check_draft：只传 revision，检查完整正文及元数据'}


def _candidate(store, config, args, *, allow_section_changes=False):
    from .analyst import validate_draft
    revision = _revision(args)
    root = _root(store, config)
    if not (root / 'current.json').exists() or _read(root / 'current.json')['revision'] != revision:
        raise ValueError('不是本轮最新稿件版本；请使用最新保存回执')
    candidate = _read(root / (revision + '.json'))
    if _hash(candidate) != revision or candidate['attempt_id'] != config['attempt_id']:
        raise ValueError('稿件版本内容或执行身份已改变')
    if candidate['packet_hash'] != _packet_hash(config):
        raise ValueError('写作包已改变，请创建新任务；原稿保留')
    if not allow_section_changes and candidate['sections_hash'] is not None and candidate['sections_hash'] != _sections_hash(store, config):
        raise ValueError('章节已修改，请重新 save_draft 组装并检查新版本')
    # Recheck live source identity even when diagnostics are cached.
    validate_draft(store, config, candidate['draft'])
    return candidate


def check(store, config, args):
    from .draft_checks import inspect_draft
    with guard(store, config):
        candidate = _candidate(store, config, args)
        path = _root(store, config) / ('checked-' + args['revision'] + '.json')
        if path.exists() and _read(path).get('check_version') == CHECK_VERSION:
            return {**_read(path), 'cached': True}
        task = _read(Path(config['packet_root']) / 'input.json')
        index = _read(Path(config['packet_root']) / 'source-index.json')['sources']
        result = {'revision': args['revision'], 'check_version': CHECK_VERSION,
                  'status': 'checked', 'review_status': 'not_reviewed',
                  'diagnostics': inspect_draft(candidate['draft'], task['requirements'], store=store,
                      allowed_sources={s['source_id'] for s in index if not s['reference_only']})}
        _write(path, result)
        return result


def submit(store, config, args, *, file_value=None):
    from .analyst import _output_path, validate_draft
    with guard(store, config):
        candidate = _candidate(store, config, args)
        receipt = _root(store, config) / ('checked-' + args['revision'] + '.json')
        if not receipt.exists() or _read(receipt).get('check_version') != CHECK_VERSION:
            raise ValueError('这个完整版本尚未检查；请先 check_draft')
        if file_value is not None and validate_draft(store, config, file_value) != candidate['draft']:
            raise ValueError('文件在检查后已修改，请重新 check-draft 检查并取得新 revision')
        path = _output_path(store, config)
        if config.get('revision') and not (path.parent / 'responses.json').exists():
            raise ValueError('交修订稿前先保存本轮发现处理说明 responses.json')
        _write(path, candidate['draft'])
        accepted = {'revision': args['revision'], 'attempt_id': config['attempt_id'],
                    'check_version': CHECK_VERSION}
        _write(path.parent / 'draft-accepted.json', accepted)
        return {'status': 'saved', **accepted, 'review_status': 'not_reviewed'}


def submitted(store, config):
    from .analyst import _output_path
    path = _output_path(store, config)
    receipt = _read(path.parent / 'draft-accepted.json')
    binding = path.parent / 'conversation.json'
    if binding.exists() and _read(binding).get('message_id') != receipt['attempt_id']:
        raise ValueError('提交来自其他执行轮次，不能覆盖当前任务')
    config = {**config, 'attempt_id': receipt['attempt_id']}
    with guard(store, config):
        candidate = _candidate(store, config, {'revision': receipt['revision']})
        if receipt.get('check_version') != CHECK_VERSION or _read(path) != candidate['draft']:
            raise ValueError('最终文件与已检查提交版本不同，请重新检查并提交')
        return candidate['draft']


def file_config(store, run_id, path):
    """Resolve a host call against its actual writer conversation, not user IDs."""
    from .analyst import _output_path
    path = Path(path).resolve()
    binding = _read(path.parent / 'conversation.json')
    if not binding.get('message_id'):
        raise ValueError('写作会话尚无执行身份')
    config = {'run_id': run_id, 'attempt_id': binding['message_id'],
              'packet_root': str(path.parent / 'packet'), 'result_file': str(path)}
    _output_path(store, config)
    from .chat_store import ChatStore
    chat = ChatStore(store)
    session = chat.session(binding['session_id'])
    if session['status'] in ('cancelled', 'interrupted', 'failed', 'stopping') or session.get('turn_id') not in (None, binding['message_id']):
        raise ValueError('写作会话已停止或切换轮次')
    message = next((m for m in chat.snapshot(binding['session_id'], private=True)['messages']
                    if m['id'] == binding['message_id']), None)
    if message is None or message['status'] in ('cancelled', 'failed', 'interrupted'):
        raise ValueError('写作任务已停止或执行身份不匹配')
    return config


def read_saved(store, config, args):
    """Recover saved work through the same role tools after SDK compaction."""
    from .analyst import _sections_file
    field = args.get('field', 'overview')
    fields = {'overview', 'body', 'citations', 'number_bindings', 'temporal_claims', 'gaps', 'research_notes'}
    if field not in fields:
        raise ValueError('未知稿件字段')
    offset, limit = args.get('offset', 0), args.get('limit', 5)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('offset 须为非负整数，limit 须为 1–20')
    with guard(store, config):
        root = _root(store, config)
        ledger_path = _sections_file(store, config)
        ledger = _read(ledger_path) if ledger_path.exists() else {}
        current = root / 'current.json'
        revision = _read(current)['revision'] if current.exists() else None
        candidate = _candidate(store, config, {'revision': revision}, allow_section_changes=True) if revision else None
        draft = candidate['draft'] if candidate else {}
        changed = bool(candidate and candidate['sections_hash'] is not None and candidate['sections_hash'] != _sections_hash(store, config))
        check_path = root / ('checked-' + revision + '.json') if revision else None
        checked = bool(check_path and not changed and check_path.exists()
                       and _read(check_path).get('check_version') == CHECK_VERSION)
        overview = {'revision': revision, 'title': draft.get('title'), 'section_ids': list(ledger),
                    'sections_changed_since_save': changed,
                    'body_blocks': len((draft.get('editor_document') or {}).get('content', [])),
                    'checked': checked,
                    'review_status': 'not_reviewed'}
        section_id = args.get('section_id')
        if section_id is not None:
            if field != 'body' or section_id not in ledger:
                raise ValueError('读取章节须用 field=body 和本轮已保存的 section_id')
            value = ledger[section_id]['editor_document']['content']
        elif field == 'overview':
            return overview
        elif not candidate:
            raise ValueError('还没有完整稿件；先读取已保存 section_id 的正文')
        elif field == 'body':
            value = draft['editor_document']['content']
        else:
            value = draft.get(field) or []
        if isinstance(value, str):
            # Long notes are read in 1000-character pages; never truncate silently.
            value = [value[i:i+1000] for i in range(0, len(value), 1000)]
        return {**overview, 'field': field, 'section_id': section_id, 'offset': offset,
                'items': value[offset:offset+limit], 'total': len(value), 'has_more': offset+limit < len(value)}
