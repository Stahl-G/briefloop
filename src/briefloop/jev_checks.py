"""User-requested, version-bound evidence prechecks using the existing job store."""
import hashlib
import json
import time

from . import jev
from .evidence_context import located_context
from .store import Conflict, dump, now, uid

PROTOCOL = 'jev-evidence-observation-v1'
SCOPE = '仅预检本版本已明确绑定的结论与所列原文上下文；未覆盖全稿，不是事实保证或独立审阅。'


def snapshot(store, version_id):
    brief = store.brief_view(version_id)
    detail = json.loads(brief['detail'])
    allowed = set(store.source_ids(brief['run_id']))
    grouped, unbound, sources = {}, 0, {}
    for citation in detail.get('citations', []):
        quote = citation.get('report_quote', '')
        if not quote or quote not in brief['markdown']:
            unbound += 1
            continue
        item = grouped.setdefault(quote, {'statement': quote, 'evidence': [], 'unavailable': []})
        source_id = citation.get('source_id')
        if source_id not in allowed:
            item['unavailable'].append('引用来源未绑定到本次报告')
            continue
        try:
            if source_id not in sources:
                row = store.one('sources', source_id)
                text = store.source_text(source_id)
                sources[source_id] = (row, text)
            row, text = sources[source_id]
            context = located_context(text, citation.get('excerpt', ''), citation.get('locator', ''))
            evidence = {'source_id': source_id, 'title': row['name'], 'excerpt': citation['excerpt'],
                        **context, 'source_hash': hashlib.sha256(text.encode()).hexdigest()}
            if evidence not in item['evidence']:
                item['evidence'].append(evidence)
        except (OSError, ValueError, KeyError):
            item['unavailable'].append('原文不可用或摘录无法唯一定位，需要重新补依据')
    items = []
    for quote, item in grouped.items():
        # One claim may depend on several sources. Never silently drop a failed
        # source and turn an incomplete evidence set into a support judgment.
        item['id'] = hashlib.sha256(quote.encode()).hexdigest()[:24]
        item['eligible'] = bool(item['evidence']) and not item['unavailable']
        items.append(item)
    data = {'protocol': PROTOCOL, 'version_id': version_id, 'run_id': brief['run_id'],
            'brief_hash': brief['hash'], 'provider': 'TypeSafe', 'endpoint': jev.ENDPOINT,
            'model': jev.MODEL, 'instructions': jev.INSTRUCTIONS, 'criteria': jev.CRITERIA,
            'items': items, 'unbound_citations': unbound, 'scope': SCOPE}
    return {**data, 'fingerprint': hashlib.sha256(dump(data).encode()).hexdigest()}


def enqueue(store, version_id, fingerprint, *, allow_external=False):
    if allow_external is not True:
        raise ValueError('请确认将所列结论和来源摘录发送至 TypeSafe 进行 Jev 预检')
    frozen = snapshot(store, version_id)
    if fingerprint != frozen['fingerprint']:
        raise Conflict('稿件或来源已变化，请重新查看预检范围后开始')
    if not any(item['eligible'] for item in frozen['items']):
        raise ValueError('没有可预检的结论依据；请先完成本稿的补依据')
    with store.tx() as connection:
        prior = connection.execute("SELECT * FROM jobs WHERE kind='jev_check' AND json_extract(payload,'$.fingerprint')=? ORDER BY rowid DESC LIMIT 1", (fingerprint,)).fetchone()
        if prior:
            # Includes interrupted/failed jobs. Recovery is an explicit action,
            # not a repeated POST that silently spends quota a second time.
            return dict(prior)
        if not jev.key_status()['configured']:
            raise ValueError('尚未配置 TypeSafe API Key；Jev 预检未启动')
        identity = uid('job')
        payload = {'version_id': version_id, 'run_id': frozen['run_id'], 'fingerprint': fingerprint,
                   'authorization': {'requested_by': 'user', 'allow_external': True, 'provider': 'TypeSafe'},
                   'snapshot': frozen}
        connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',
                           (identity, 'jev_check', 'queued', dump(payload), None, None, now(), now()))
    store.event(identity, 'jev_requested', {'version_id': version_id,
                                         'eligible': sum(item['eligible'] for item in frozen['items'])})
    store.wake_jobs()
    return store.one('jobs', identity)


def _persist(store, job_id, result):
    # A stop may race with the HTTP result. Preserve the receipt without ever
    # turning a cancelled job back into running/complete.
    with store.tx() as connection:
        connection.execute('UPDATE jobs SET result=?,updated=? WHERE id=?', (dump(result), now(), job_id))


def run(store, job, cancelled, *, evaluate=None):
    payload = json.loads(job['payload'])
    frozen = payload['snapshot']
    body = {key: value for key, value in frozen.items() if key != 'fingerprint'}
    if (frozen.get('protocol') != PROTOCOL or payload.get('authorization', {}).get('allow_external') is not True
            or hashlib.sha256(dump(body).encode()).hexdigest() != payload['fingerprint']
            or frozen['instructions'] != jev.INSTRUCTIONS or frozen['criteria'] != jev.CRITERIA
            or frozen['endpoint'] != jev.ENDPOINT or frozen['model'] != jev.MODEL):
        raise ValueError('预检协议或冻结材料已变化，请重新确认范围')
    # Data is captured before the user's consent. Changes after admission do not
    # alter the request, and the UI marks it stale against its new snapshot.
    result = json.loads(job.get('result') or '{}') or {
        'version_id': payload['version_id'], 'fingerprint': payload['fingerprint'],
        'scope': SCOPE, 'affects_release': False, 'items': [], 'started': now()}
    by_id = {item['id']: item for item in result['items']}
    evaluate = evaluate or jev.evaluate
    for item in frozen['items']:
        if cancelled.is_set():
            raise InterruptedError('Jev 预检已停止；已返回观察保留')
        previous = by_id.get(item['id'])
        if previous and previous['execution'] in ('complete', 'not_checked'):
            continue
        if previous and previous['execution'] in ('requesting', 'uncertain'):
            # A process may have stopped after billing but before a receipt.
            # Never silently repeat that request on ordinary task resume.
            previous.update(execution='uncertain', status='not_checked',
                            reason='上次请求是否完成未知，未重复发送；请人工核查这条结论')
            _persist(store, job['id'], result)
            continue
        observation = {'id': item['id'], 'statement': item['statement'], 'status': 'not_checked',
                       'execution': 'not_checked', 'reason': '；'.join(item['unavailable'])}
        result['items'].append(observation)
        by_id[item['id']] = observation
        if not item['eligible']:
            _persist(store, job['id'], result)
            continue
        observation.update(execution='requesting', requested=now(), reason='')
        _persist(store, job['id'], result)
        start = time.monotonic()
        try:
            public = evaluate(item, frozen['model'])
        except Exception:
            observation.update(execution='uncertain', reason='请求未取得有效结果；用量可能未知，未自动重试',
                               elapsed_seconds=round(time.monotonic()-start, 3))
            _persist(store, job['id'], result)
            # Only the provider adapter's safe errors may be exposed. Unknown
            # adapters can include request/credential material in exceptions.
            raise ValueError('Jev 预检未完成；已返回结果保留，失败请求未自动重发') from None
        observation.update(public, execution='complete', elapsed_seconds=round(time.monotonic()-start, 3))
        _persist(store, job['id'], result)
        store.event(job['id'], 'jev_progress', {'completed': sum(row['execution'] == 'complete' for row in result['items']),
                                              'total': len(frozen['items'])})
    if cancelled.is_set():
        raise InterruptedError('Jev 预检已停止；已返回观察保留')
    result['finished'] = now()
    result['unchecked_count'] = sum(row['execution'] != 'complete' for row in result['items'])
    _persist(store, job['id'], result)
    return result


def view(store, version_id):
    frozen = snapshot(store, version_id)
    jobs = store.rows("SELECT id,status,payload,result,error FROM jobs WHERE kind='jev_check' AND json_extract(payload,'$.version_id')=? ORDER BY rowid DESC", (version_id,))
    records = []
    for job in jobs:
        payload = json.loads(job['payload'])
        records.append({'id': job['id'], 'status': job['status'], 'error': job['error'],
                        'stale': payload['fingerprint'] != frozen['fingerprint'],
                        'result': json.loads(job['result']) if job['result'] else None})
    return {'version_id': version_id, 'fingerprint': frozen['fingerprint'], 'provider': jev.key_status(),
            'scope': SCOPE, 'items': frozen['items'], 'unbound_citations': frozen['unbound_citations'],
            'records': records}
