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


def replace_section_text(section, replacements, expected_hash):
    """Apply exact edits to a copy, preserving rich nodes and citation bindings."""
    from copy import deepcopy
    if not section or expected_hash != _hash(section):
        raise ValueError('章节版本不匹配；先 read_draft 读取当前章节及 section_hash，再修改')
    if not isinstance(replacements, list) or not 1 <= len(replacements) <= 24:
        raise ValueError('text_replacements 需要 1–24 项精确文字替换')
    value = deepcopy(section)
    def text_nodes(node):
        if node.get('type') == 'text':
            yield node
        for child in node.get('content', []):
            yield from text_nodes(child)
    for edit in replacements:
        if (not isinstance(edit, dict) or set(edit) != {'old_text', 'new_text'}
                or not isinstance(edit['old_text'], str) or not edit['old_text']
                or not isinstance(edit['new_text'], str)):
            raise ValueError('替换项需要非空 old_text 和字符串 new_text')
        nodes = list(text_nodes(value['editor_document']))
        matches = sum(node['text'].count(edit['old_text']) for node in nodes)
        if matches != 1:
            raise ValueError(f'old_text 在当前章节命中 {matches} 处；须在单个文字节点内唯一匹配，本批修改未保存')
        for node in nodes:
            if edit['old_text'] in node['text']:
                node['text'] = node['text'].replace(edit['old_text'], edit['new_text'], 1)
    def remove_empty_text(node):
        if 'content' in node:
            node['content'] = [child for child in node['content']
                               if child.get('type') != 'text' or child.get('text')]
            for child in node['content']:
                remove_empty_text(child)
    remove_empty_text(value['editor_document'])
    return value


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
    with guard(store, config):
        return _save_locked(store, config, args)


def _save_locked(store, config, args, *, writer_request=None, response_fields=None):
    """Caller must hold guard; shared by atomic writer-input operations."""
    from .analyst import validate_draft, _assemble_sections
    value = dict(args)
    base = value.pop('base_revision', None)
    root = _root(store, config)
    current_path = root / 'current.json'
    previous = _read(current_path) if current_path.exists() else {}
    # Keep compact tombstones for accepted writer-input identities. Evicting
    # keys would make a content-hash cycle A -> B -> A accept an old A -> B
    # mutation again. Only the latest request needs its full replay response.
    # Every save, including the older rich-JSON path, retires that response.
    history = dict(previous.get('writer_input_history') or {})
    last = previous.get('writer_input_receipt') or {}
    if (isinstance(last, dict) and isinstance(last.get('operation'), str)
            and isinstance(last.get('args_hash'), str)
            and isinstance(last.get('response'), dict)
            and last['response'].get('revision') == previous.get('revision')):
        key = _writer_request_key(last['operation'], last['args_hash'])
        history.setdefault(key, True)
    writer_identity = None
    if writer_request is not None:
        operation, request_args = writer_request
        args_hash = _hash(request_args)
        writer_identity = _writer_request_key(operation, args_hash)
        if writer_identity in history:
            raise ValueError('同一写入请求已接纳；请读取当前稿件版本，不能重复执行')
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
    from .length import count_brief
    response = {'revision': revision, 'status': 'saved', 'body_units': count_brief(value['markdown']),
            'review_status': 'not_reviewed', 'next': 'check_draft：只传 revision，检查完整正文及元数据',
            'update': '局部改稿用 base_revision 加改变的字段；修改章节后传 base_revision 与完整有序 section_ids，不重复未改元数据。'}
    if response_fields:
        response.update(response_fields)
    current = {'revision': revision}
    if writer_request is not None:
        current['writer_input_receipt'] = {
            'operation': operation, 'args_hash': args_hash, 'response': response}
    if history:
        current['writer_input_history'] = history
    # The new revision and its replay receipt become visible together. A crash
    # before this write leaves an unreferenced candidate, which a retry can
    # safely recreate against the still-current base revision.
    _write(current_path, current)
    return response


def _writer_request_key(operation, args_hash):
    return _hash({'operation': operation, 'args_hash': args_hash})


def _replay_writer_input_locked(store, config, operation, request_args):
    """Replay only the latest identical write, after validating its live scope."""
    path = _root(store, config) / 'current.json'
    if not path.exists():
        return None
    current = _read(path)
    receipt = current.get('writer_input_receipt') or {}
    args_hash = _hash(request_args)
    if receipt.get('operation') == operation and receipt.get('args_hash') == args_hash:
        response = receipt.get('response')
    else:
        history = current.get('writer_input_history') or {}
        if _writer_request_key(operation, args_hash) not in history:
            return None
        raise ValueError('同一写入请求已被后续修订覆盖；请读取当前稿件版本，不能重复执行')
    if not isinstance(response, dict) or response.get('revision') != current.get('revision'):
        raise ValueError('当前稿件的写入回执与修订版本不一致')
    # This checks the current revision, attempt ID, frozen packet, section
    # snapshot, and live source identity before returning a cached response.
    _candidate(store, config, {'revision': current['revision']})
    return {**response, 'replayed': True}


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
    marker = path.parent / 'conversation.json'
    if not marker.exists():
        raise ValueError('写作任务尚未准备好：缺少 conversation.json 会话绑定；请由任务启动器完成会话初始化后重试。不要自行创建身份或修改工作区。')
    binding = _read(marker)
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
            overview['section_hash'] = _hash(ledger[section_id])
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
        from .writer_input import block_keys, evidence_key
        identities = {}
        if field == 'body' and not section_id:
            identities['block_keys'] = block_keys(value)[offset:offset+limit]
        elif field in ('citations', 'number_bindings', 'temporal_claims'):
            identities['record_keys'] = [evidence_key(item) for item in value[offset:offset+limit]]
        return {**overview, **identities, 'field': field, 'section_id': section_id, 'offset': offset,
                'items': value[offset:offset+limit], 'total': len(value), 'has_more': offset+limit < len(value)}
