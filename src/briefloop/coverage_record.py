"""Per-question coverage stated by the author, checked against actual tool receipts.

Statuses separate what the reader needs to know: answered, partial,
checked_not_found, read_failed and not_checked. The program never judges whether
an answer is right; it only checks that cited sources belong to the run and
that "what was checked" matches metered search/page records. Unmatched checks
(host-native search, memory) stay visible as self-reported.
"""
import json
import re

STATUSES = ('answered', 'partial', 'checked_not_found', 'read_failed', 'not_checked')
BLOCK = re.compile(r'```briefloop-coverage\s*\n(.*?)\n```\s*', re.S)
GUIDE = ('正文之后另起一个 ```briefloop-coverage 代码块（不属于正文），内容为 JSON：'
         '{"questions":[{"question":1,"status":"answered|partial|checked_not_found|read_failed|not_checked",'
         '"sources":["src_…"],"checked":["实际用过的查询词或网址"],"note":"一句话：答到哪一步或缺什么"}]}。'
         'question 是关键问题序号（从 1 开始），每题一项；checked_not_found 表示查过但没找到，not_checked 表示没查。如实填写，系统会对照实际检索和抓取记录核对。')


def split(text):
    """Return (report body without the block, parsed items or None, parse error)."""
    match = BLOCK.search(text)
    if not match:
        return text, None, None
    body = (text[:match.start()] + text[match.end():]).strip()
    try:
        value = json.loads(match[1])
        items = value.get('questions') if isinstance(value, dict) else None
        if not isinstance(items, list):
            raise ValueError('questions 必须为数组')
        return body, items, None
    except ValueError as exc:
        return body, None, str(exc)[:300]


def _receipts(store, run_id):
    queries, urls = {}, {}
    folder = store.root / 'discovery' / run_id
    for path in sorted(folder.glob('*.request.json')) if folder.exists() else []:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if record.get('query'):
            queries[' '.join(str(record['query']).split()).casefold()] = record.get('local_request_id') or path.stem
    for sid in store.source_ids(run_id):
        row = store.one('sources', sid)
        if row.get('url'):
            urls[row['url'].rstrip('/')] = {'source_id': sid, 'status': row['status']}
    return queries, urls


def bind(store, run_id, key_questions, items, error=None):
    """Normalized record for every key question; never raises on author mistakes."""
    queries, urls = _receipts(store, run_id)
    allowed = {sid: store.one('sources', sid)['status'] for sid in store.source_ids(run_id)}
    stated = {}
    for item in items or []:
        if isinstance(item, dict) and type(item.get('question')) is int and 1 <= item['question'] <= len(key_questions):
            stated.setdefault(item['question'], item)
    rows = []
    for index, question in enumerate(key_questions, 1):
        item = stated.get(index)
        if item is None:
            rows.append({'question_id': f'q{index}', 'question': question, 'status': 'not_checked', 'recorded': False,
                         'note': '作者未交代该问题', 'sources': [], 'checked': [], 'flags': ['not_stated']})
            continue
        status = item.get('status') if item.get('status') in STATUSES else 'not_checked'
        sources = [s for s in item.get('sources') or [] if isinstance(s, str) and s in allowed]
        checked = []
        for value in item.get('checked') or []:
            if not isinstance(value, str) or not value.strip():
                continue
            key = ' '.join(value.split()).casefold()
            if key in queries:
                checked.append({'value': value, 'receipt': 'metered_search', 'request_id': queries[key]})
            elif value.rstrip('/') in urls:
                checked.append({'value': value, 'receipt': 'page', **urls[value.rstrip('/')]})
            else:
                checked.append({'value': value, 'receipt': 'self_reported'})
        flags = []
        if len(sources) < len(item.get('sources') or []):
            flags.append('unknown_sources_dropped')
        if status in ('answered', 'partial') and not any(allowed[s] == 'ready' for s in sources):
            flags.append('no_readable_source')
        if status in ('checked_not_found', 'read_failed') and not any(c['receipt'] != 'self_reported' for c in checked):
            flags.append('check_self_reported')
        rows.append({'question_id': f'q{index}', 'question': question, 'status': status, 'recorded': True,
                     'note': str(item.get('note') or '')[:500], 'sources': sources, 'checked': checked[:20], 'flags': flags})
    return {'kind': 'coverage', 'items': rows, **({'parse_error': error} if error else {}),
            'summary': '作者对每个关键问题的覆盖说明；检查项已对照实际检索与抓取记录，未对上的标为自述。这不是事实核验结果。'}


def progress(record):
    """Task-card projection compatible with research-goal-progress.js."""
    if not record:
        return None
    return {'questions': [{'id': row['question_id'], 'question': row['question'], 'status': row['status'],
                           'recorded': row['recorded'], 'reason': row['note'],
                           'remaining_question': '' if row['status'] == 'answered' else row['note'],
                           'flags': row['flags'], 'checked': [c['value'] for c in row['checked'] if c['receipt'] != 'self_reported'],
                           'self_reported': [c['value'] for c in row['checked'] if c['receipt'] == 'self_reported'],
                           'evidence': [{'source_id': s, 'source_name': s, 'locator': '', 'excerpt': ''} for s in row['sources']]}
                          for row in record['items']]}
