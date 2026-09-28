import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'semantic-experiment.v2'
NUMBER = re.compile(r'(?<![\d.])[+−-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[%％])?')


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else dump(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def json_lines(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(dump(value) + '\n')


class ReadOnlyStore:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.db = sqlite3.connect((self.root / 'briefloop.db').as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.db.execute('BEGIN')

    def close(self):
        self.db.close()

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def one(self, table, identity):
        if table not in {r['name'] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}:
            raise ValueError('未知对象表')
        rows = self.rows('SELECT * FROM "' + table + '" WHERE id=?', (identity,))
        if len(rows) != 1:
            raise ValueError('对象不存在或不唯一')
        return rows[0]

    def source_text(self, sid):
        from briefloop.media import safe_source_path
        source = self.one('sources', sid)
        text = safe_source_path(self, source['path']).read_text(encoding='utf-8')
        if hashlib.sha256(text.encode()).hexdigest() != source['hash']:
            raise ValueError('来源哈希不匹配')
        return text

    def source_ids(self, run_id):
        run = self.one('runs', run_id)
        acquired = self.rows('SELECT source_id FROM run_sources WHERE run_id=? ORDER BY rowid', (run_id,))
        return list(dict.fromkeys(json.loads(run['source_ids']) + [r['source_id'] for r in acquired]))


def located_evidence(store, binding):
    from briefloop.delivery_checks import _number_locator
    from briefloop.evidence import EvidenceInput, _read_location
    result = {'source_id': binding.get('source_id'), 'locator': binding.get('locator'),
              'excerpt': binding.get('source_excerpt', ''), 'status': 'unavailable'}
    try:
        locator = _number_locator(binding['locator'])
        source, location = _read_location(store, EvidenceInput(
            source_id=binding['source_id'], locator=locator, excerpt=binding.get('source_excerpt', '')))
        result.update(source_name=source['name'], source_hash=source['hash'],
                      locator=location['locator'], target=location['located_text'],
                      raw_hash=location.get('raw_hash'), status=location['location_status'])
        if locator.kind == 'text':
            lines = store.source_text(source['id']).splitlines()
            left, right = max(0, locator.start_line - 61), min(len(lines), locator.end_line + 10)
            result['context'] = '\n'.join(lines[left:right])
            result['context_lines'] = [left + 1, right]
            if location['located_text'] not in result['context']:
                raise ValueError('目标原文不在上下文中')
        else:
            result['context'] = location['located_text']
        result['context_scope'] = '定位邻域保留完整行；不保证所需语义上下文齐全'
    except (ValueError, OSError, KeyError) as exc:
        result['status'] = 'unavailable'
        result['reason'] = str(exc)
    return result


def report_context(requirements):
    return {k: requirements[k] for k in ('title', 'objective', 'audience', 'key_questions', 'period',
            'period_start', 'period_end', 'report_date', 'reader_contract') if k in requirements}


def make_case(task, state, provenance, evidence=(), observed=None, origin='natural', safety=None):
    case = {'schema': SCHEMA, 'task': task, 'state': state, 'provenance': provenance,
            'evidence': list(evidence), 'observed': observed or {}, 'origin': origin,
            'safety': safety or {}, 'input_status': 'ready'}
    case['case_id'] = digest(case)
    return case


def report_cases(store, version_id, exposure):
    from briefloop.delivery_checks import check_numbers
    brief = store.one('briefs', version_id)
    run = store.one('runs', brief['run_id'])
    requirements = json.loads(run['requirements'])
    if requirements.get('result_format') == 'grounded_qa_v1':
        raise ValueError('本实验面向报告；OfficeQA 仅保留为历史诊断，不导入其答案评分')
    detail = json.loads(brief['detail'])
    bindings = detail.get('number_bindings', [])
    allowed = set(store.source_ids(run['id'])) - set(requirements.get('reference_source_ids', []))
    checks = check_numbers(brief['markdown'], bindings, store, allowed)
    sources = [{'id': sid, 'hash': store.one('sources', sid)['hash']} for sid in sorted(allowed)]
    provenance = {'version_id': version_id, 'run_id': run['id'], 'brief_hash': brief['hash'],
                  'content_sha256': digest(brief['markdown'].encode()), 'sources': sources, 'exposure': exposure}
    offsets = []
    for index, binding in enumerate(bindings):
        quote, token = binding.get('report_quote', ''), binding.get('number_text', '')
        if quote and token and brief['markdown'].count(quote) == 1 and quote.count(token) == 1:
            start = brief['markdown'].index(quote) + quote.index(token)
            offsets.append((start, start + len(token), index))
    cases = []
    for match in NUMBER.finditer(brief['markdown']):
        start, end = match.span()
        left = brief['markdown'].rfind('\n', 0, start) + 1
        right = brief['markdown'].find('\n', end)
        right = len(brief['markdown']) if right < 0 else right
        indexes = [index for a, b, index in offsets if a <= start and end <= b]
        attached = [located_evidence(store, bindings[i]) for i in indexes]
        states = [checks[i] for i in indexes]
        status = ('mismatch' if any(s['checked'] and not s['found'] for s in states) else
                  'checked' if states and all(s['checked'] and s['found'] for s in states) else
                  'unchecked' if indexes else 'unbound')
        state = {'report': report_context(requirements), 'paragraph': brief['markdown'][left:right],
                 'target': {'text': match.group(), 'start': start - left, 'end': end - left}}
        cases.append(make_case('numeric_omission', state, {**provenance, 'body_range': [start, end]}, attached,
            {'verification': status, 'binding_indexes': indexes, 'checks': states},
            safety={'must_review': status == 'mismatch'}))
    return cases, brief, requirements


def source_material(store, sid):
    source = store.one('sources', sid)
    return {'source_id': sid, 'source_name': source['name'], 'source_hash': source['hash'],
            'text': store.source_text(sid)}


def change_cases(store, brief, requirements, exposure):
    from briefloop.source_updates import impacts
    from briefloop.evidence import inspect_bindings
    result = []
    allowed = set(store.source_ids(brief['run_id']))
    tables = {r['name'] for r in store.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'source_changes' not in tables:
        return result
    current_bindings = None
    for change in store.rows('SELECT * FROM source_changes ORDER BY rowid'):
        if change['run_id'] != brief['run_id'] and change['old_source_id'] not in allowed:
            continue
        data = json.loads(change['data'])
        old, new = source_material(store, change['old_source_id']), source_material(store, change['new_source_id'])
        impact = impacts(store, change['old_source_id'])
        conflict = store.one('conflicts', change['conflict_id']) if change.get('conflict_id') else None
        conflict_data = json.loads(conflict['data']) if conflict else {}
        must_review = bool(conflict and conflict['status'] != 'resolved' and conflict_data.get('importance') == 'core')
        provenance = {'version_id': brief['id'], 'run_id': brief['run_id'], 'brief_hash': brief['hash'],
                      'change_id': change['id'], 'exposure': exposure,
                      'sources': [{'id': x['source_id'], 'hash': x['source_hash']} for x in (old, new)]}
        state = {'report': report_context(requirements), 'old_source': old, 'new_source': new,
                 'author_proposal_not_verified': {k: data.get(k) for k in
                    ('description', 'scope', 'relation', 'information_cutoff', 'change_type')},
                 'timing': {**{k: data.get(k) for k in ('old_availability', 'new_availability')},
                            'old': data.get('old_snapshot', {}).get('data', {}).get('timing', {}),
                            'new': data.get('new_snapshot', {}).get('data', {}).get('timing', {})}}
        observed = {'impacts': impact, 'conflict_status': conflict['status'] if conflict else None}
        result.append(make_case('change_materiality', state, provenance, observed=observed,
                               safety={'must_review': must_review}))
        if current_bindings is None:
            current_bindings = inspect_bindings(store, brief['id'])['bindings']
        affected = set(impact['direct_claim_ids'] + impact['indirect_claim_ids'])
        for binding in current_bindings:
            if binding['claim_id'] not in affected or binding['status'] == 'anchor_missing':
                continue
            claim = binding['claim']['data']
            input_sources = {x['id']: x['hash'] for x in provenance['sources']}
            def dependencies(closure):
                for evidence in closure.get('evidence', []):
                    source = store.one('sources', evidence['source_id'])
                    input_sources[source['id']] = source['hash']
                value = closure.get('claim', {}).get('data', {})
                return {'claim_id': closure['claim_id'], 'status': closure['status'],
                        'statement': value.get('statement'), 'scope': value.get('scope'),
                        'assumptions': value.get('assumptions', []),
                        'recorded_reasoning_not_verified': value.get('reasoning', ''),
                        'evidence': [located_evidence(store, {'source_id': e['source_id'],
                            'locator': dump(e['data']['locator']), 'source_excerpt': e['data']['excerpt']})
                            for e in closure.get('evidence', [])],
                        'premises': [dependencies(p) for p in closure.get('premises', [])]}
            closure = dependencies(binding)
            update_state = {**state, 'old_conclusion': {'statement': claim['statement'],
                'report_quote': binding['quote'], 'premise_claim_ids': claim.get('premise_claim_ids', []),
                'scope': claim.get('scope', ''), 'period': claim.get('period', '')},
                'dependency_context': closure}
            case = make_case('conclusion_update', update_state,
                {**provenance, 'claim_id': binding['claim_id'], 'block_id': binding['block_id'],
                 'sources': [{'id': sid, 'hash': source_hash} for sid, source_hash in sorted(input_sources.items())]},
                observed={**observed, 'binding_status': binding['status']},
                safety={'must_review': must_review})
            if binding['status'] not in ('unreviewed',):
                case['input_status'] = 'unavailable'
            result.append(case)
    return result


def assign_splits(cases, seed):
    parent = {}
    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for case in cases:
        p = case['provenance']
        keys = ['report:' + p['run_id']] + ['source:' + s['hash'] for s in p.get('sources', [])]
        for key in keys[1:]:
            a, b = root(keys[0]), root(key)
            parent[max(a, b)] = min(a, b)
    for case in cases:
        group = root('report:' + case['provenance']['run_id'])
        case['group_id'] = digest(group)
        number = int(digest([seed, group])[:8], 16) % 10
        case['split'] = 'dev' if number < 6 else 'validation' if number < 8 else 'test'


def freeze_dataset(cases, out, seed='semantic-v2', metadata=None):
    out = Path(out)
    if len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('重复检查对象；请去重报告版本')
    assign_splits(cases, seed)
    manifest = {'schema': SCHEMA, 'created': now(), 'seed': seed, 'metadata': metadata or {},
                'cases': {c['case_id']: digest(c) for c in cases},
                'split_counts': {s: sum(c['split'] == s for c in cases) for s in ('dev', 'validation', 'test')}}
    manifest['dataset_id'] = digest({k: v for k, v in manifest.items() if k != 'created'})
    out.mkdir(parents=True, exist_ok=False)
    for case in cases:
        save_new(out / (case['case_id'] + '.json'), case)
    save_new(out / 'manifest.json', manifest)
    return manifest


def load_dataset(folder):
    folder = Path(folder)
    manifest = read_json(folder / 'manifest.json')
    expected = digest({k: v for k, v in manifest.items() if k not in ('created', 'dataset_id')})
    if manifest.get('schema') != SCHEMA or manifest.get('dataset_id') != expected:
        raise ValueError('数据集清单身份不匹配')
    cases = []
    for identity, expected in manifest['cases'].items():
        if not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('无效对象 ID')
        case = read_json(folder / (identity + '.json'))
        if digest(case) != expected or case['case_id'] != identity:
            raise ValueError('检查对象已被修改')
        cases.append(case)
    return manifest, cases


def export_workspaces(selections, out, exposure, seed, origin='natural'):
    cases = []
    for workspace, versions in selections:
        store = ReadOnlyStore(workspace)
        try:
            for version in versions:
                numbers, brief, requirements = report_cases(store, version, exposure)
                cases.extend(numbers)
                cases.extend(change_cases(store, brief, requirements, exposure))
        finally:
            store.close()
    if origin not in ('natural', 'synthetic'):
        raise ValueError('需区分自然报告与合成报告')
    for case in cases:
        case['origin'] = origin
        case['case_id'] = digest({k: v for k, v in case.items() if k != 'case_id'})
    import briefloop.delivery_checks as checks
    return freeze_dataset(cases, out, seed, {'exporter_sha256': digest(Path(__file__).read_bytes()),
        'checks_sha256': digest(Path(checks.__file__).read_bytes()),
        'boundary': '只读指定稿件与当前来源快照，不继承旧审阅通过状态，不向生产写回'})
