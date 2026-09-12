"""Explicit formal delivery, independent of ordinary draft Word downloads.

The gate consumes accepted independent review and deterministic identity checks.
It does not prove semantic truth, nor allow an author or score to waive errors.
"""
import hashlib
import json
import os
from pathlib import Path

from .deliverable_spec import clause_items, requirement_severity
from .store import dump, now, uid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES briefs(id),
 job_id TEXT REFERENCES jobs(id),fingerprint TEXT NOT NULL UNIQUE,status TEXT NOT NULL,data TEXT NOT NULL,
 result TEXT,previous_id TEXT REFERENCES releases(id),change_type TEXT,change_reason TEXT NOT NULL,
 created TEXT NOT NULL,updated TEXT NOT NULL);
'''


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def safe_file(root, relative):
    root = Path(root).resolve()
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts or not name.parts:
        raise ValueError('交付文件路径无效')
    path = root / name
    cursor = root
    for part in name.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('交付文件不能通过符号链接读取')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('交付文件不存在或越界')
    return path


def _json(row):
    return {**row, 'data': json.loads(row['data']),
            'result': json.loads(row['result']) if row.get('result') else None}


def get_release(store, release_id):
    rows = store.rows('SELECT * FROM releases WHERE id=?', (release_id,))
    if not rows:
        raise ValueError('正式交付记录不存在')
    return _json(rows[0])


def list_releases(store, run_id):
    return [_json(row) for row in store.rows(
        'SELECT r.* FROM releases r JOIN briefs b ON b.id=r.version_id WHERE b.run_id=? ORDER BY r.rowid DESC',
        (run_id,))]


def decision(snapshot, review_result, findings, protocol='legacy', *, clauses=None):
    """Pure delivery rules; returns named blockers and non-blocking notices."""
    blockers = []
    notices = []

    def issue(code, message, **detail):
        blockers.append({'code': code, 'message': message, **detail})

    if not review_result or review_result.get('status') != 'complete':
        issue('review_incomplete', '本版本的独立核查尚未完成')
        return {'eligible': False, 'blockers': blockers, 'notices': notices}
    if not review_result.get('coverage_scan_complete'):
        issue('coverage_unchecked', '尚未独立检查正文是否遗漏重要主张绑定')
    # Older free-text unchecked entries contain no importance judgement. They
    # cannot silently turn into non-core exceptions during schema migration.
    for text in review_result.get('unchecked', []):
        issue('unchecked_unclassified', '旧核查项尚未确定重要性：' + text)
    for item in review_result.get('unchecked_items', []):
        if item.get('importance') == 'supporting':
            notices.append({'code': 'noncore_unchecked', 'message': item['description']})
        else:
            issue('core_unchecked', item['description'])
    requirements = snapshot['requirements']['requirement_items']
    # Frozen clause IDs survive permission-based redaction of their text.
    # Do not derive new IDs from omitted instructions in an offline audit.
    if clauses is None:
        clauses = clause_items(snapshot['requirements'])
    severity = requirement_severity({'reader_contract': {'clauses': clauses}})
    if protocol == 'clauses_v1':
        # The clause results are the only authority for requirement fulfilment; the
        # parent requirement rollup is not consulted, so a soft clause can never make
        # the whole objective hard (or hide a missing content answer).
        clause_checks = {item['clause_id']: item for item in review_result.get('clause_checks', [])}
        for clause in clauses:
            check = clause_checks.get(clause['clause_id'])
            status = check.get('status') if check else None
            if clause['kind'] == 'reader_content':
                if status in ('missing', 'partial'):
                    issue('content_unmet', '内容要求未完成：' + clause['instruction'], clause_id=clause['clause_id'])
                elif status != 'covered':
                    notices.append({'code': 'content_unverified', 'message': clause['instruction'], 'clause_id': clause['clause_id']})
            elif status not in ('covered', 'not_applicable'):
                notices.append({'code': 'clause_unmet', 'message': clause['instruction'], 'clause_id': clause['clause_id']})
    else:
        req_checks = {item['requirement_id']: item for item in review_result.get('requirement_checks', [])}
        for requirement in requirements:
            identity = requirement['requirement_id']
            if requirement.get('mode') == 'manual':
                continue
            check = req_checks.get(identity)
            if not check or check.get('status') != 'covered':
                # A writing/method/manual clause is soft even when it came from the objective;
                # only reader_content clauses are unfinished deliverables. Without a contract
                # fall back to the raw requirement kind.
                soft = severity.get(identity) == 'soft' or (
                    identity not in severity and requirement.get('kind') == 'writing')
                if soft:
                    notices.append({'code': 'writing_preference', 'message': requirement['text'], 'requirement_id': identity})
                else:
                    issue('requirement_unfinished', '必答要求尚未核实完成：' + requirement['text'], requirement_id=identity)
    checks = {item['claim_id']: item for item in review_result.get('claim_checks', [])}
    visited = set()

    def check_claim(node, core=False):
        claim = node.get('claim', {}).get('data', {})
        core = core or claim.get('importance', 'core') == 'core'
        identity = node['claim_id']
        key = (identity, core)
        if key in visited:
            return
        visited.add(key)
        result = checks.get(identity, {})
        if node.get('status') != 'unreviewed':
            issue('binding_changed', '正文或依据已变化，需重新绑定和核查', claim_id=identity)
        status = result.get('status')
        if status == 'contradicted':
            issue('contradicted_claim', '仍有与证据矛盾的正文主张', claim_id=identity)
        elif status != 'supported_for_scope':
            if core:
                issue('claim_unverified', '重要主张或其推断前提尚未获支持范围核查', claim_id=identity)
            else:
                notices.append({'code': 'supporting_claim_unverified', 'message': result.get('reason', '非核心主张待核'), 'claim_id': identity})
        if core and not node.get('evidence') and not node.get('premises'):
            issue('claim_no_evidence', '重要主张没有可追溯依据', claim_id=identity)
        for premise in node.get('premises', []):
            check_claim(premise, core)

    for binding in snapshot['evidence']['bindings']:
        check_claim(binding)
    def finding_is_soft(data):
        # Presentation findings are always soft. Only a compliance-type finding on a
        # purely soft requirement may soften; factual and evidence findings keep their
        # blocking power even when they reference a method or writing clause.
        if data.get('kind') == 'expression':
            return True
        if data.get('kind') not in ('missing_requirement', 'execution_gap'):
            return False
        identities = data.get('requirement_ids') or []
        return bool(identities) and all(severity.get(identity) == 'soft' for identity in identities)

    for finding in findings:
        data = finding['data']
        if finding['status'] in ('resolved', 'dismissed_with_evidence'):
            continue
        # A writing/method problem must not re-block through a "major" finding; a
        # concrete factual or evidence finding still blocks.
        if finding_is_soft(data) or data.get('severity') == 'minor':
            notices.append({'code': 'finding_notice', 'message': data['description'], 'finding_id': finding['id']})
        else:
            issue('finding_unresolved', data['description'], finding_id=finding['id'])
    for conflict in snapshot.get('conflicts', []):
        if conflict['status'] == 'resolved':
            continue
        if conflict['data'].get('importance', 'core') == 'supporting':
            notices.append({'code': 'noncore_conflict', 'message': conflict['data']['description'], 'conflict_id': conflict['id']})
        else:
            issue('conflict_unresolved', conflict['data']['description'], conflict_id=conflict['id'])
    detail = snapshot.get('detail') or {}
    gap_records = detail.get('gap_records') or [{'impact': str(text), 'status': 'open'} for text in (detail.get('gaps') or [])]
    for record in gap_records:
        if record.get('status') == 'resolved':
            continue
        # Unresolved gaps are surfaced but do not block on their own; a core evidence
        # gap still blocks through the claim and conflict checks.
        code = 'delivery_gap_unresolved' if record.get('status') == 'unresolved' else 'delivery_gap_open'
        notices.append({'code': code, 'message': str(record.get('impact', '')), 'related': record.get('related', '')})
    # Deterministic checks computed for this exact version: a body number that does
    # not match its located original value, or a reference to a source that is not
    # registered, blocks formal delivery. Unknown/unsupported/missing bindings are
    # recorded as notices, not as a claim that the number is correct.
    deterministic = snapshot.get('deterministic') or {}
    numbers = deterministic.get('numbers') or {}
    for item in numbers.get('unmatched', []):
        issue('number_mismatch', '正文数值与原始值不一致：' + str(item.get('label') or item.get('expected') or '未命名数值'),
              label=item.get('label', ''), expected=item.get('expected', ''), reason=item.get('reason', ''))
    for item in numbers.get('skipped', []):
        notices.append({'code': 'number_unchecked', 'message': str(item.get('label') or item.get('expected') or '未命名数值') + '：' + str(item.get('reason', '未检查'))})
    for source_id in deterministic.get('broken_refs', []):
        issue('broken_reference', '正文引用了不存在的来源：' + str(source_id), source_id=source_id)
    return {'eligible': not blockers, 'blockers': blockers, 'notices': notices}


def _applicable_review(store, review_id, version_id):
    # Shared admission/restore validation lives with Reviewer, not in this gate.
    from .review import validate_applicable_review
    return validate_applicable_review(store, review_id, version_id)


def _review_execution(store, review):
    """Freeze only this Review's saved public runtime and tool events.

    It is collected after admission, so it cannot be part of the packet the
    Reviewer originally read. Its own hash is included in the release input.
    """
    result = {'status': 'unavailable', 'sessions': []}
    if not review.get('job_id'):
        return result
    folder = (store.root / review['data']['packet_path']).parent.resolve()
    has_chat = bool(store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_events'"))
    for row in store.rows("SELECT data,created FROM events WHERE job_id=? AND kind='runtime_started' AND created<=? ORDER BY seq",
                          (review['job_id'], review['updated'])):
        event = json.loads(row['data'])
        if not event.get('folder') or Path(event['folder']).resolve() != folder:
            continue
        runtime = event.get('runtime', {})
        session = {'session_id': event['session_id'], 'started': row['created'], 'backend': event.get('backend'),
                   'configured_runtime': {key: runtime[key] for key in ('model', 'model_provider', 'reasoning_effort', 'model_variant') if key in runtime},
                   'tool_records': [], 'usage': []}
        if has_chat:
            records = store.rows("SELECT kind,data,created FROM chat_events WHERE session_id=? AND created<=? AND kind IN ('tool/record','thread/tokenUsage/updated','thread/providerChanged') ORDER BY seq",
                                 (event['session_id'], review['updated']))
            for item in records:
                data = json.loads(item['data'])
                if item['kind'] == 'tool/record':
                    session['tool_records'].append({'created': item['created'], 'record': data['record']})
                else:
                    session['usage'].append({'created': item['created'], 'kind': item['kind'], 'data': data})
        result['sessions'].append(session)
    if result['sessions']:
        result['status'] = 'captured'
    return result


def eligibility(store, version_id):
    from .export_jobs import export_input
    from .review import _snapshot, review_status
    brief = store.one('briefs', version_id)
    status = review_status(store, version_id)
    candidates = status['reviews']
    result = {'version_id': version_id, 'eligible': False, 'blockers': [], 'notices': []}
    if not candidates:
        result['blockers'].append({'code': 'review_missing', 'message': '本版本尚未完成独立核查'})
        return result
    # Never fall back to an older pass after a newer failure or unresolved review.
    newest = candidates[0]
    if newest['status'] != 'complete':
        result['blockers'].append({'code': 'review_incomplete', 'message': '最新审阅仍在进行、失败或未完成，不能沿用更早结果'})
        return result
    try:
        review = _applicable_review(store, newest['id'], version_id)
        snapshot = _snapshot(store, version_id)
        from .delivery_checks import brief_checks
        # The same deterministic result is shown on the page and re-run by the audit
        # verifier, so the gate and the audit cannot disagree.
        snapshot['deterministic'] = brief_checks(store, version_id)
        identity, _ = export_input(store, brief)
    except (ValueError, OSError) as exc:
        result['blockers'].append({'code': 'input_unverified', 'message': str(exc)})
        return result
    result.update(decision(snapshot, review['result'], status['findings'],
                           (review.get('data') or {}).get('protocol', 'legacy')))
    result['review_id'] = review['id']
    if result['eligible']:
        run = store.one('runs', brief['run_id'])
        skill = store.one('skills', run['skill_id']) if run.get('skill_id') else None
        frozen = {'schema_version': 1, 'version_id': version_id, 'run_id': brief['run_id'],
                  'brief_hash': brief['hash'], 'export_input': identity,
                  'export_fingerprint': sha(dump(identity).encode()),
                  'review_id': review['id'], 'review_fingerprint': review['fingerprint'],
                  'review_protocol': (review.get('data') or {}).get('protocol', 'legacy'),
                  'review_clauses': clause_items(snapshot['requirements']),
                  'review_result': review['result'], 'review_files': review['data']['files'],
                  'review_execution': _review_execution(store, review),
                  'packet_path': review['data']['packet_path'], 'snapshot': snapshot,
                  'findings': status['findings'], 'skill': skill}
        result['input'] = frozen
        result['fingerprint'] = sha(dump(frozen).encode())
    return result


def enqueue_release(store, version_id, *, previous_id=None, change_type=None, change_reason=''):
    if previous_id:
        previous = get_release(store, previous_id)
        if previous['status'] != 'released' or previous['version_id'] == version_id:
            raise ValueError('更正或更新须针对旧正式件建立新正文版本')
        if store.one('briefs', previous['version_id'])['run_id'] != store.one('briefs', version_id)['run_id']:
            raise ValueError('更正版本必须属于同一报告')
        if change_type not in ('correction', 'update') or not change_reason.strip():
            raise ValueError('请区分当时错误的更正与后来事实的更新，并说明原因')
    elif change_type or change_reason:
        raise ValueError('更正说明需要关联旧正式件')
    checked = eligibility(store, version_id)
    if not checked['eligible']:
        raise ValueError('正式交付条件未满足：' + '；'.join(item['message'] for item in checked['blockers']))
    data = {**checked['input'], 'notices': checked['notices'],
            'previous_id': previous_id, 'change_type': change_type, 'change_reason': change_reason}
    fingerprint = sha(dump(data).encode())
    # Release and its ordinary file job are assigned in one transaction. A
    # worker must never observe a queued job before its release owns that job.
    with store.tx() as connection:
        found = connection.execute('SELECT * FROM releases WHERE fingerprint=?', (fingerprint,)).fetchone()
        row = _json(dict(found)) if found else None
        old_job = connection.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone() if row and row['job_id'] else None
        if row and (row['status'] == 'released' or old_job and old_job['status'] in ('queued', 'running')):
            identity, job_id = row['id'], row['job_id']
        else:
            identity = row['id'] if row else uid('release')
            job_id = uid('job')
            payload = {'release_id': identity, 'version_id': version_id,
                       'run_id': data['run_id'], 'fingerprint': fingerprint}
            connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',
                               (job_id, 'release', 'queued', dump(payload), None, None, now(), now()))
            if row:
                connection.execute("UPDATE releases SET job_id=?,status='pending',updated=? WHERE id=?",
                                   (job_id, now(), identity))
            else:
                connection.execute('INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (identity, version_id, job_id, fingerprint, 'pending', dump(data), None,
                                    previous_id, change_type, change_reason, now(), now()))
    row = get_release(store, identity)
    if row['status'] == 'released':
        validate_release(store, row)
    return {'release': row, 'job': store.one('jobs', job_id)}


def validate_release(store, release):
    if isinstance(release, str):
        release = get_release(store, release)
    if release['status'] != 'released' or not release['result']:
        raise ValueError('正式文件尚未完成')
    result = release['result']
    if sha(dump(release['data']).encode()) != release['fingerprint']:
        raise ValueError('正式交付固定输入记录已变化')
    manifest_path = safe_file(store.root, result['manifest_path'])
    if sha(manifest_path.read_bytes()) != result['manifest_hash']:
        raise ValueError('正式交付清单已变化')
    manifest = json.loads(manifest_path.read_text())
    if manifest['release_id'] != release['id'] or manifest['fingerprint'] != release['fingerprint'] or manifest['version_id'] != release['version_id']:
        raise ValueError('正式件与交付记录版本不一致')
    folder = manifest_path.parent
    for name, digest in manifest['files'].items():
        if sha(safe_file(folder, name).read_bytes()) != digest:
            raise ValueError('正式交付材料已变化：' + name)
    if manifest['files'].get('report.docx') != result['sha256']:
        raise ValueError('正式 Word 与交付记录不一致')
    if result['path'] != str((folder / 'report.docx').relative_to(store.root)):
        raise ValueError('正式 Word 下载未指向该交付文件')
    if json.loads(safe_file(folder, 'records.json').read_text()) != release['data']:
        raise ValueError('正式交付输入与冻结文件不一致')
    return manifest


def release_file(store, release_id):
    release = get_release(store, release_id)
    validate_release(store, release)
    return safe_file(store.root, release['result']['path'])


def generate_release(store, job, cancelled):
    from .export_jobs import generate_word
    payload = json.loads(job['payload'])
    release = get_release(store, payload['release_id'])
    if release['status'] == 'released':
        validate_release(store, release)
        return release['result']
    if release['job_id'] != job['id'] or release['fingerprint'] != payload['fingerprint']:
        raise ValueError('正式交付任务与固定输入不一致')
    current = eligibility(store, release['version_id'])
    data = release['data']
    expected = {key: value for key, value in data.items() if key not in ('notices', 'previous_id', 'change_type', 'change_reason')}
    if not current['eligible'] or current.get('input') != expected:
        raise ValueError('正式制作前正文、依据或核查结果已变化，请重新核查并发起交付')

    def stage(message):
        if cancelled.is_set() or store.one('jobs', job['id'])['status'] in ('cancelled', 'failed'):
            raise InterruptedError('正式交付已停止')
        store.event(job['id'], 'export_progress', {'message': message})

    stage('固定本次正文、核查材料与交付版本')
    folder = store.root / 'releases' / release['id']
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ValueError('交付目录不能为符号链接')
    folder.mkdir(parents=True, exist_ok=True)
    files = {}

    def save(name, blob):
        path = folder / name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(folder)):
            raise ValueError('交付文件不能为符号链接')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        if temporary.is_symlink():
            raise ValueError('交付临时文件不能为符号链接')
        temporary.write_bytes(blob)
        os.replace(temporary, path)
        files[name] = sha(blob)

    packet = store.root / data['packet_path']
    for name, digest in data['review_files'].items():
        blob = safe_file(packet, name).read_bytes()
        if sha(blob) != digest:
            raise ValueError('核查材料已变化：' + name)
        save('packet/' + name, blob)
    save('records.json', dump(data).encode())
    # Reuse the exact deterministic Word renderer and its input validation.
    word_job = {**job, 'kind': 'export_docx', 'payload': dump({
        'version_id': release['version_id'], 'fingerprint': data['export_fingerprint']})}
    word = generate_word(store, word_job, cancelled)
    blob = safe_file(store.root, word['path']).read_bytes()
    if sha(blob) != word['sha256']:
        raise ValueError('Word 文件校验失败')
    save('report.docx', blob)
    # No release if a dependency changes while the renderer is working.
    after = eligibility(store, release['version_id'])
    if not after['eligible'] or after.get('input') != expected:
        raise ValueError('正式制作期间输入发生变化；文件保留为未交付工件')
    stage('验证正式件和独立审计材料清单')
    manifest = {'schema_version': 1, 'release_id': release['id'], 'version_id': release['version_id'],
                'run_id': data['run_id'], 'fingerprint': release['fingerprint'],
                'review_id': data['review_id'], 'review_fingerprint': data['review_fingerprint'],
                'previous_id': release['previous_id'], 'change_type': release['change_type'],
                'created': now(), 'files': files}
    encoded = dump(manifest).encode()
    target = folder / 'manifest.json'
    if target.is_symlink() or target.with_suffix('.tmp').is_symlink():
        raise ValueError('交付清单不能为符号链接')
    target.with_suffix('.tmp').write_bytes(encoded)
    os.replace(target.with_suffix('.tmp'), target)
    result = {'release_id': release['id'], 'version_id': release['version_id'],
              'path': str((folder / 'report.docx').relative_to(store.root)), 'sha256': word['sha256'],
              'manifest_path': str(target.relative_to(store.root)), 'manifest_hash': sha(encoded),
              'download_url': '/api/release-file?id=' + release['id']}
    with store.tx() as connection:
        active = connection.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if cancelled.is_set() or not active or active['status'] not in ('queued', 'running'):
            raise InterruptedError('正式交付已停止，文件未登记为正式件')
        updated = connection.execute("UPDATE releases SET status='released',result=?,updated=? WHERE id=? AND job_id=? AND status='pending'",
                                     (dump(result), now(), release['id'], job['id']))
        if updated.rowcount != 1:
            raise ValueError('正式交付任务已被新请求替代，旧任务不能登记正式件')
        settled = connection.execute("UPDATE jobs SET status='complete',result=?,error=NULL,updated=? WHERE id=? AND status IN ('queued','running')",
                                     (dump(result), now(), job['id']))
        if settled.rowcount != 1:
            raise InterruptedError('正式交付已停止，文件未登记为正式件')
    validate_release(store, release['id'])
    return result
