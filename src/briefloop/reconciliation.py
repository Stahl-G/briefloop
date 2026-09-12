"""Immutable pre-writing evidence comparison snapshots.

This module holds the contract, reference validation, the frozen candidate
index and the snapshot files. It never decides truth: comparability, relation
labels and support are the agent's judgement; here we only prove that the
referenced sources, spans, claims, requirements and conflicts exist and belong
to the run, and that the examined/unexamined split covers the frozen set.
"""
import hashlib
import json
from .store import dump, uid, now

SCHEMA_VERSION = 1
STATUSES = ('complete', 'partial', 'not_applicable', 'failed')
RELATIONS = ('compatible', 'different_scope', 'temporal_sequence', 'correction',
             'supersession', 'republication', 'attributed_difference', 'contradiction', 'unknown')


class ReconciliationError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def _folder(store, run_id):
    return store.root / 'research' / run_id / 'reconciliations'


def _path(store, run_id, reconciliation_id):
    return _folder(store, run_id) / (reconciliation_id + '.json')


def _requirements_fingerprint(requirements):
    keys = ('title', 'objective', 'audience', 'language', 'period', 'writing_mode',
            'report_profile', 'target_words', 'max_words', 'key_questions', 'manual_sections', 'allow_web')
    return _digest({key: requirements.get(key) for key in keys})


def candidates(store, run_id):
    """The frozen candidate set: every registered source and source statement of the run.

    Style references are excluded; unextracted sources stay in the list so an
    empty comparison cannot hide a source that was never read.
    """
    run = store.one('runs', run_id)
    requirements = json.loads(run['requirements'])
    references = set(requirements.get('reference_source_ids', []))
    sources = []
    for source_id in store.source_ids(run_id):
        if source_id in references:
            continue
        source = store.one('sources', source_id)
        sources.append({'source_id': source_id, 'name': source['name'], 'status': source['status'], 'hash': source['hash']})
    statements = []
    for row in store.rows('SELECT * FROM claims WHERE run_id=? ORDER BY rowid', (run_id,)):
        data = json.loads(row['data'])
        if data.get('claim_role') != 'source_statement':
            continue
        statements.append({'claim_id': row['id'], 'statement': data['statement'], 'kind': data['kind'],
                           'entity': data.get('entity', ''), 'metric': data.get('metric', ''),
                           'period': data.get('period', ''), 'scope': data.get('scope', ''),
                           'attribution': data.get('attribution', ''),
                           'supports': [support['span_id'] for support in data.get('supports', [])]})
    return {'run_id': run_id, 'requirements_fingerprint': _requirements_fingerprint(requirements),
            'sources': sources, 'statements': statements,
            'statement_claim_ids': [statement['claim_id'] for statement in statements]}


def _input_fingerprint(index):
    return _digest({'requirements': index['requirements_fingerprint'],
                    'sources': [(source['source_id'], source['hash']) for source in index['sources']],
                    'statements': [(statement['claim_id'], statement['statement'], statement['supports']) for statement in index['statements']]})


def _validate_relation(store, run_id, relation, allowed_claims, allowed_sources, requirement_ids):
    from .evidence import record
    if not isinstance(relation, dict):
        raise ReconciliationError('关系条目必须是对象')
    label = relation.get('relation')
    if label not in RELATIONS:
        raise ReconciliationError('未知关系：' + str(label))
    members = list(dict.fromkeys(relation.get('member_claim_ids', []) or []))
    if len(members) < 2:
        raise ReconciliationError('一条关系至少需要两个不同的候选陈述')
    for claim_id in members:
        if claim_id not in allowed_claims:
            raise ReconciliationError('关系引用了不属于本轮的来源陈述：' + str(claim_id))
    for span_id in relation.get('basis_span_ids', []) or []:
        span = record(store, 'evidence_spans', span_id)
        if span['source_id'] not in allowed_sources:
            raise ReconciliationError('关系依据片段不属于本轮来源：' + str(span_id))
    for requirement_id in relation.get('affected_requirement_ids', []) or []:
        if requirement_id not in requirement_ids:
            raise ReconciliationError('关系关联了未登记的报告要求：' + str(requirement_id))
    if relation.get('conflict_id'):
        if not store.rows('SELECT id FROM conflicts WHERE id=?', (relation['conflict_id'],)):
            raise ReconciliationError('关系引用的冲突不存在：' + str(relation['conflict_id']))


def _content_hash(record):
    body = {key: value for key, value in record.items() if key not in ('id', 'content_hash', 'input_fingerprint', 'created')}
    return _digest(body)


def _stored(store, run_id):
    folder = _folder(store, run_id)
    if not folder.is_dir():
        return []
    records = []
    for path in sorted(folder.glob('*.json')):
        try:
            records.append(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError, OSError):
            continue
    return records


def save(store, run_id, payload):
    if not isinstance(payload, dict):
        raise ReconciliationError('对照内容必须是对象')
    status = payload.get('status')
    if status not in STATUSES:
        raise ReconciliationError('对照状态无效：' + str(status))
    index = candidates(store, run_id)
    allowed_claims = set(index['statement_claim_ids'])
    allowed_sources = {source['source_id'] for source in index['sources']}
    from .deliverable_spec import requirement_items
    requirement_ids = {item['requirement_id'] for item in requirement_items(json.loads(store.one('runs', run_id)['requirements']))}
    relations = []
    for relation in payload.get('relations', []) or []:
        _validate_relation(store, run_id, relation, allowed_claims, allowed_sources, requirement_ids)
        relations.append(relation)
    examined = list(dict.fromkeys(payload.get('examined_claim_ids', []) or []))
    unexamined = list(dict.fromkeys(payload.get('unexamined_claim_ids', []) or []))
    unknown = (set(examined) | set(unexamined)) - allowed_claims
    if unknown:
        raise ReconciliationError('对照划分引用了不属于本轮的候选陈述：' + ', '.join(sorted(unknown)))
    if set(examined) & set(unexamined):
        raise ReconciliationError('已检查与未检查候选不能重叠')
    if set(examined) | set(unexamined) != allowed_claims:
        raise ReconciliationError('对照必须明确划分本轮全部来源陈述（examined ∪ unexamined）')
    open_questions = []
    for question in payload.get('open_questions', []) or []:
        if not isinstance(question, dict) or not str(question.get('question', '')).strip():
            raise ReconciliationError('待查问题需要 question 文本')
        open_questions.append(question)
    record = {'schema_version': SCHEMA_VERSION, 'run_id': run_id, 'status': status,
              'requirements_fingerprint': index['requirements_fingerprint'],
              'input_fingerprint': _input_fingerprint(index),
              'examined_claim_ids': examined, 'unexamined_claim_ids': unexamined,
              'relations': relations, 'open_questions': open_questions,
              'coverage_notes': str(payload.get('coverage_notes', '')), 'created': now()}
    record['content_hash'] = _content_hash(record)
    for existing in _stored(store, run_id):
        if existing.get('input_fingerprint') == record['input_fingerprint'] and existing.get('content_hash') == record['content_hash']:
            return existing
    record['id'] = uid('recon')
    path = _path(store, run_id, record['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(dump(record), encoding='utf-8')
    temporary.replace(path)
    return record


def exists(store, run_id, reconciliation_id):
    return bool(reconciliation_id) and _path(store, run_id, reconciliation_id).exists()


def read(store, run_id, reconciliation_id):
    path = _path(store, run_id, reconciliation_id)
    if not path.exists():
        raise ReconciliationError('对照记录不存在：' + str(reconciliation_id))
    record = json.loads(path.read_text(encoding='utf-8'))
    current = _input_fingerprint(candidates(store, run_id))
    record['stale'] = record.get('input_fingerprint') != current
    return record
