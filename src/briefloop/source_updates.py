"""Append-only source lineage and later-disclosure impact records.

A fresh HTTP response proves acquisition, not that its claims supersede another
source. Semantic relationships remain proposals until the shared Conflict has
been independently reviewed. This module never edits a report, release or Wiki.
"""
from datetime import date, datetime, time, timezone
import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .models import Model
from .store import dump, now, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_snapshot_metadata(id TEXT PRIMARY KEY,
 source_id TEXT NOT NULL REFERENCES sources(id),logical_id TEXT NOT NULL,
 previous_id TEXT REFERENCES source_snapshot_metadata(id),data TEXT NOT NULL,created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS source_snapshot_metadata_source ON source_snapshot_metadata(source_id);
CREATE TABLE IF NOT EXISTS source_changes(id TEXT PRIMARY KEY,
 old_source_id TEXT NOT NULL REFERENCES sources(id),new_source_id TEXT NOT NULL REFERENCES sources(id),
 run_id TEXT REFERENCES runs(id),conflict_id TEXT REFERENCES conflicts(id),data TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_refreshes(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
 source_id TEXT NOT NULL REFERENCES sources(id),new_source_id TEXT REFERENCES sources(id),
 outcome TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
'''


def _interval(value):
    """Retain date precision instead of pretending a date was an exact instant."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('时间需填写 ISO 日期或带时区的时间')
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return (datetime.combine(day, time.min, timezone.utc),
                    datetime.combine(day, time.max, timezone.utc))
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('时间缺少时区')
        return moment, moment
    except (ValueError, TypeError) as exc:
        raise ValueError('时间需填写 ISO 日期或带时区的时间') from exc


class SourceTimes(Model):
    effective_start: str | None = None
    effective_end: str | None = None
    published_at: str | None = None
    available_at: str | None = None
    basis: str = ''

    @model_validator(mode='after')
    def valid_times(self):
        for name in ('effective_start', 'effective_end', 'published_at', 'available_at'):
            if getattr(self, name):
                _interval(getattr(self, name))
        if self.effective_start and self.effective_end:
            if _interval(self.effective_start)[0] > _interval(self.effective_end)[1]:
                raise ValueError('统计或事件有效期结束不能早于开始')
        if any(getattr(self, field) for field in ('effective_start', 'effective_end', 'published_at', 'available_at')) and not self.basis.strip():
            raise ValueError('时间信息需要保存原文定位或说明，不能由抓取时间推定')
        return self


class ChangeInput(Model):
    old_source_id: str
    new_source_id: str
    kind: Literal['correction', 'update', 'unknown'] = 'unknown'
    relation: Literal['corrects', 'supersedes', 'new_period', 'republication', 'unknown'] = 'unknown'
    description: str = Field(min_length=1, max_length=6000)
    scope: str = Field(min_length=1, max_length=3000)
    relationship_evidence: str = ''
    importance: Literal['core', 'supporting'] = 'core'
    conflict_id: str | None = None
    information_cutoff: str
    old_timing: SourceTimes | None = None
    new_timing: SourceTimes | None = None

    @model_validator(mode='after')
    def valid_change(self):
        _interval(self.information_cutoff)
        if self.old_source_id == self.new_source_id:
            raise ValueError('更新需要独立保存的新来源快照，不能重写原来源')
        if self.relation in ('corrects', 'supersedes', 'republication') and not self.relationship_evidence.strip():
            raise ValueError('更正、替代或转载关系需要具体原文定位与依据')
        return self


def _decode(row):
    return {**row, 'data': json.loads(row['data'])}


def _snapshot(store, source_id):
    from .media import source_files
    source, provenance, original = source_files(store, source_id)
    store.source_text(source_id)
    return {'source_id': source_id, 'text_hash': source['hash'],
            'raw_hash': hashlib.sha256(original.read_bytes()).hexdigest() if original else None,
            'url': source.get('url'), 'fetched_at': (provenance or {}).get('fetched_at'),
            'saved_at': source['created'], 'status': source['status']}


def register_snapshot(store, source_id, *, timing=None, logical_id=None, previous_id=None):
    """Enrich time metadata explicitly; source bytes and earlier metadata stay fixed.

    A correction to metadata is appended with previous_id. A source may have
    several immutable annotations but only one logical lineage identity.
    """
    snapshot = _snapshot(store, source_id)
    rows = store.rows('SELECT * FROM source_snapshot_metadata WHERE source_id=? ORDER BY rowid DESC', (source_id,))
    previous = _decode(rows[0]) if rows else None
    if previous_id and (not previous or previous['id'] != previous_id):
        raise ValueError('来源时间注释已变化，请基于最新注释保存')
    if previous and logical_id and logical_id != previous['logical_id']:
        raise ValueError('来源逻辑身份不能静默改写')
    logical_id = logical_id or (previous['logical_id'] if previous else uid('logical_source'))
    if timing is None and previous:
        timing = previous['data']['timing']
    data = {**snapshot, 'timing': SourceTimes.model_validate(timing or {}).model_dump()}
    if previous and data == previous['data']:
        return previous
    if previous and snapshot != {key: previous['data'][key] for key in snapshot}:
        raise ValueError('来源快照已变化，不能在原来源身份下更新')
    if previous and not previous_id:
        raise ValueError('更改来源时间注释需提供 previous_id，历史注释保留')
    identity = uid('source_metadata')
    with store.tx() as connection:
        current = connection.execute('SELECT id FROM source_snapshot_metadata WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (source_id,)).fetchone()
        if (current['id'] if current else None) != (previous['id'] if previous else None):
            raise ValueError('来源时间注释已变化，请重试')
        connection.execute('INSERT INTO source_snapshot_metadata VALUES(?,?,?,?,?,?)',
                           (identity, source_id, logical_id, previous_id, dump(data), now()))
    return _decode(store.rows('SELECT * FROM source_snapshot_metadata WHERE id=?', (identity,))[0])


def availability(timing, information_cutoff):
    """Publication, acquisition and event time cannot substitute first availability."""
    cutoff = _interval(information_cutoff)
    available = timing.get('available_at')
    if not available:
        return 'availability_unknown'
    interval = _interval(available)
    if interval[0] > cutoff[1]:
        return 'after_cutoff'
    if interval[1] <= cutoff[0]:
        return 'available_by_cutoff'
    return 'overlapping_date_precision'


def impacts(store, source_id):
    """Propagate through premises, while keeping indirect evidence distinct."""
    from .evidence import inspect_bindings
    spans = {row['id'] for row in store.rows('SELECT id FROM evidence_spans WHERE source_id=?', (source_id,))}
    claims = [_decode(row) for row in store.rows('SELECT * FROM claims ORDER BY rowid')]
    direct = {row['id'] for row in claims if any(item['span_id'] in spans for item in row['data'].get('supports', []))}
    from .figures import read_figure
    for row in claims:
        for figure_id in row['data'].get('figure_ids', []):
            if source_id in read_figure(store, figure_id)['source_ids']:
                direct.add(row['id'])
    affected = set(direct)
    while True:
        more = {row['id'] for row in claims if affected.intersection(row['data'].get('premise_claim_ids', []))}
        if more.issubset(affected):
            break
        affected.update(more)
    versions = []
    from .document_model import brief_document, source_ids
    affected_runs = {row['run_id'] for row in claims if row['id'] in affected}
    for brief in store.rows('SELECT * FROM briefs ORDER BY rowid'):
        bound = ([row for row in inspect_bindings(store, brief['id'])['bindings'] if row['claim_id'] in affected]
                 if brief['run_id'] in affected_runs else [])
        # A removed block no longer carries a current claim, though its parent
        # version is independently retained in this list.
        bound = [row for row in bound if row['status'] != 'anchor_missing']
        detail = json.loads(brief['detail'])
        citations = {item['source_id'] for item in detail.get('citations', [])}
        cited = source_id in citations or source_id in source_ids(brief_document(brief))
        if bound or cited:
            versions.append({'version_id': brief['id'], 'run_id': brief['run_id'], 'brief_hash': brief['hash'],
                             'claim_ids': sorted({row['claim_id'] for row in bound}), 'cited': cited})
    releases = []
    if store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='releases'"):
        version_ids = {row['version_id'] for row in versions}
        releases = [{'release_id': row['id'], 'version_id': row['version_id'], 'fingerprint': row['fingerprint'], 'status': row['status']}
                    for row in store.rows('SELECT id,version_id,fingerprint,status FROM releases ORDER BY rowid')
                    if row['version_id'] in version_ids]
    return {'source_id': source_id, 'direct_claim_ids': sorted(direct),
            'indirect_claim_ids': sorted(affected - direct), 'versions': versions, 'releases': releases}


def record_change(store, request, *, run_id=None):
    value = ChangeInput.model_validate(request)
    if run_id:
        allowed = set(store.source_ids(run_id))
        if not {value.old_source_id, value.new_source_id}.issubset(allowed):
            raise ValueError('更新的新旧来源都必须登记到本轮报告')
    old = register_snapshot(store, value.old_source_id, timing=value.old_timing)
    new_rows = store.rows('SELECT logical_id FROM source_snapshot_metadata WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (value.new_source_id,))
    new = register_snapshot(store, value.new_source_id, timing=value.new_timing,
                            logical_id=new_rows[0]['logical_id'] if new_rows else old['logical_id'])
    from .conflicts import create
    kind = 'correction' if value.kind == 'correction' else 'different_scope' if value.relation == 'new_period' else 'unknown'
    if value.conflict_id:
        conflicts = store.rows('SELECT * FROM conflicts WHERE id=?', (value.conflict_id,))
        if not conflicts or set(json.loads(conflicts[0]['data'])['source_ids']) != {value.old_source_id, value.new_source_id}:
            raise ValueError('更新关联的冲突不属于同一对来源')
        if conflicts[0]['status'] == 'resolved':
            raise ValueError('已有冲突已完成复核；新的更正声明需要新的复核记录')
        conflict = conflicts[0]
    else:
        conflict = create(store, source_ids=[value.old_source_id, value.new_source_id],
                          description=value.description + '\n适用范围：' + value.scope,
                          kind=kind, importance=value.importance)
    # The conflict is workspace-scoped so a later run reusing either snapshot
    # must still see it, rather than losing a warning with the original run.
    data = {**value.model_dump(exclude={'old_source_id', 'new_source_id', 'old_timing', 'new_timing'}),
            'old_snapshot': old, 'new_snapshot': new,
            'old_availability': availability(old['data']['timing'], value.information_cutoff),
            'new_availability': availability(new['data']['timing'], value.information_cutoff),
            'impacts_at_detection': impacts(store, value.old_source_id),
            'change_type': value.kind, 'classification_status': 'proposed',
            'note': '记录更新提示不表示已复核，不自动修改旧稿或正式件；日期更新、官方身份及转载数量都不决定采用。'}
    identity = uid('source_change')
    with store.tx() as connection:
        connection.execute('INSERT INTO source_changes VALUES(?,?,?,?,?,?,?)',
                           (identity, value.old_source_id, value.new_source_id, run_id, conflict['id'], dump(data), now()))
    store.event(None, 'source_update_warning', {'change_id': identity, 'conflict_id': conflict['id'],
                                             'source_ids': [value.old_source_id, value.new_source_id]})
    return get_change(store, identity)


def get_change(store, identity):
    rows = store.rows('SELECT * FROM source_changes WHERE id=?', (identity,))
    if not rows:
        raise ValueError('来源更新记录不存在')
    value = _decode(rows[0])
    conflict = store.rows('SELECT * FROM conflicts WHERE id=?', (value['conflict_id'],))
    value['review'] = _decode(conflict[0]) if conflict else None
    value['review_status'] = conflict[0]['status'] if conflict else 'unreviewed'
    value['current_impacts'] = impacts(store, value['old_source_id'])
    return value


def for_run(store, run_id):
    sources = set(store.source_ids(run_id))
    return [get_change(store, row['id']) for row in store.rows('SELECT * FROM source_changes ORDER BY rowid')
            if row['run_id'] == run_id or sources.intersection((row['old_source_id'], row['new_source_id']))]


def for_version(store, version_id):
    store.one('briefs', version_id)
    result = []
    for row in store.rows('SELECT id FROM source_changes ORDER BY rowid'):
        value = get_change(store, row['id'])
        if any(item['version_id'] == version_id for item in value['current_impacts']['versions']):
            result.append(value)
    return result


def refresh(store, run_id, source_id, *, information_cutoff, trigger='manual'):
    """Actually acquire a new snapshot, within the same run's existing budget.

    Deliberately bypass existing_for_run: its cached response cannot demonstrate
    freshness. No model or semantic change classification occurs here.
    """
    _interval(information_cutoff)
    if trigger not in ('manual', 'next_run', 'research_refresh'):
        raise ValueError('来源更新只在手动复查、下次报告或刷新研究时触发')
    run = store.one('runs', run_id)
    if source_id not in store.source_ids(run_id):
        raise ValueError('复查来源未登记到本轮报告')
    source = store.one('sources', source_id)
    data = {'trigger': trigger, 'information_cutoff': information_cutoff,
            'old_snapshot': _snapshot(store, source_id), 'started_at': now()}
    new_source = None
    if not json.loads(run['requirements']).get('allow_web'):
        outcome = 'not_authorized'
    elif not source.get('url'):
        outcome = 'local_source_requires_upload'
    else:
        from . import research_budget, sources
        try:
            data['budget'] = research_budget.reserve_pages(store, run_id, [source['url']])
        except research_budget.BudgetExhausted as exc:
            outcome = 'budget_exhausted'
            data['budget'] = exc.result['budget']
        else:
            new_source = sources.fetch(store, source['url'])
            store.attach_source(run_id, new_source['id'])
            data['new_snapshot'] = _snapshot(store, new_source['id'])
            if new_source['status'] != 'ready':
                outcome = 'fetch_failed'
                data['error'] = new_source.get('error')
            else:
                old = data['old_snapshot']; new = data['new_snapshot']
                # Raw response changes can matter even when extraction is equal.
                outcome = 'unchanged_snapshot' if (old['text_hash'], old['raw_hash']) == (new['text_hash'], new['raw_hash']) else 'changed_needs_review'
                if outcome == 'changed_needs_review':
                    from .conflicts import create
                    conflict = create(store, source_ids=[source_id, new_source['id']],
                                      description='重新取得的来源快照与旧快照不同，需核对变化范围及对已引用结论的影响。',
                                      kind='unknown', importance='core')
                    data['conflict_id'] = conflict['id']
    data['completed_at'] = now()
    data['note'] = '仅比较实际取得快照；差异需主 Agent 分类并登记 source_change 交 Reviewer，不自动推定新值正确。'
    identity = uid('source_refresh')
    with store.tx() as connection:
        connection.execute('INSERT INTO source_refreshes VALUES(?,?,?,?,?,?,?)',
                           (identity, run_id, source_id, new_source['id'] if new_source else None, outcome, dump(data), now()))
    result = _decode(store.rows('SELECT * FROM source_refreshes WHERE id=?', (identity,))[0])
    store.event(None, 'source_refresh', {'refresh_id': identity, 'run_id': run_id, 'source_id': source_id, 'outcome': outcome})
    return result
