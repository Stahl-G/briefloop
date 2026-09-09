"""Explicit, permission-scoped audit exports and offline consistency validation.

No code in the bundle is executed. Hash validation detects changed package
contents; it is not a factual verdict or a signature against a local admin.
"""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from .store import dump
from .release import get_release, validate_release, safe_file, sha

SCHEMA_VERSION = 1


def permissions_for(source_ids, permissions):
    if not isinstance(permissions, dict) or set(permissions) - set(source_ids):
        raise ValueError('原件导出选择包含不属于本正式件的来源')
    result = {}
    for sid in source_ids:
        item = permissions.get(sid, {})
        if isinstance(item, str):
            item = {'mode': item}
        if not isinstance(item, dict) or item.get('mode', 'metadata') not in ('original', 'excerpt', 'metadata'):
            raise ValueError('来源导出方式须为 original、excerpt 或 metadata')
        mode = item.get('mode', 'metadata')
        result[sid] = {'mode': mode, 'reason': str(item.get('reason') or (
            '' if mode == 'original' else '用户仅授权定位和摘录' if mode == 'excerpt' else '未授权在此包中提供原件或证据摘录'))}
    return result


def enqueue_bundle(store, release_id, source_permissions):
    release = get_release(store, release_id)
    validate_release(store, release)
    permissions = permissions_for([s['id'] for s in release['data']['snapshot']['sources']], source_permissions)
    fingerprint = sha(dump({'release_id': release_id, 'manifest_hash': release['result']['manifest_hash'],
                            'permissions': permissions, 'schema_version': SCHEMA_VERSION}).encode())
    for job in store.rows("SELECT * FROM jobs WHERE kind='audit_bundle' ORDER BY rowid DESC"):
        payload = json.loads(job['payload'])
        if payload.get('fingerprint') != fingerprint:
            continue
        if job['status'] in ('queued', 'running'):
            return job
        if job['status'] == 'complete':
            try:
                bundle_file(store, job)
                return job
            except (ValueError, OSError):
                pass
    return store.enqueue('audit_bundle', {'release_id': release_id, 'version_id': release['version_id'],
                                          'run_id': release['data']['run_id'], 'permissions': permissions,
                                          'fingerprint': fingerprint})


def _scrub(value):
    """Defense in depth for explicit records, not a new DLP/permission system."""
    from .execution_records import sanitize
    hidden = {'reasoning_content', 'chain_of_thought', 'hidden_reasoning', 'thinking', 'analysis_trace'}
    if isinstance(value, dict):
        value = {k: _scrub(v) for k, v in value.items() if k.lower() not in hidden}
    elif isinstance(value, list):
        value = [_scrub(item) for item in value]
    return sanitize(value)


def _filter_snapshot(snapshot, permissions):
    result = deepcopy(snapshot)

    def walk(node):
        for span in node.get('evidence', []):
            mode = permissions[span['source_id']]['mode']
            if mode != 'original':
                span['data'].pop('located_text', None)
            if mode == 'metadata':
                for key in ('excerpt', 'cells', 'value'):
                    span['data'].pop(key, None)
                span['data']['omission'] = permissions[span['source_id']]['reason']
        for premise in node.get('premises', []):
            walk(premise)

    for node in result['evidence']['bindings'] + result.get('candidate_claims', []):
        walk(node)
    return result


def generate_bundle(store, job, cancelled):
    payload = json.loads(job['payload'])
    release = get_release(store, payload['release_id'])
    release_manifest = validate_release(store, release)
    permissions = permissions_for([s['id'] for s in release['data']['snapshot']['sources']], payload['permissions'])
    expected = sha(dump({'release_id': release['id'], 'manifest_hash': release['result']['manifest_hash'],
                         'permissions': permissions, 'schema_version': SCHEMA_VERSION}).encode())
    if payload['fingerprint'] != expected:
        raise ValueError('审计包与正式件或授权选择不一致')
    folder = safe_file(store.root, release['result']['manifest_path']).parent
    blobs = {}
    omissions = []
    transformations = []

    def stage(message):
        if cancelled.is_set() or store.one('jobs', job['id'])['status'] in ('cancelled', 'failed'):
            raise InterruptedError('审计包导出已停止')
        store.event(job['id'], 'export_progress', {'message': message})

    def copy(name):
        path = safe_file(folder, name)
        blob = path.read_bytes()
        if sha(blob) != release_manifest['files'][name]:
            raise ValueError('冻结交付材料已变化：' + name)
        blobs[name] = blob

    def json_blob(name, value):
        before = value
        value = _scrub(value)
        if value != before:
            transformations.append({'file': name, 'reason': '已移除凭据字段或隐藏推理字段'})
        blobs[name] = dump(value).encode()

    stage('按明确选择收集正式报告、依据和核查记录')
    source_index = json.loads(safe_file(folder, 'packet/index.json').read_text())['sources']
    source_files = {sid: set() for sid in permissions}
    for item in source_index:
        for key in ('text_file', 'readable_text_file', 'original_file', 'cells_file'):
            if item.get(key):
                source_files[item['id']].add('packet/' + item[key])
        for name in item.get('visual_files', []):
            source_files[item['id']].add('packet/' + name)
    all_original = all(item['mode'] == 'original' for item in permissions.values())
    target = json.loads(safe_file(folder, 'packet/target.json').read_text())
    restricted_figure_files = {}
    for figure in target.get('figures', []):
        restricted = [sid for sid in figure['source_ids'] if permissions[sid]['mode'] != 'original']
        if restricted:
            # Data and scripts may preserve entire input workbooks, extracted
            # rows or embedded source text. Follow registered provenance, not
            # guesses about strings. The report image remains downloadable.
            for key in ('data', 'script'):
                path = figure.get(key + '_path')
                if path:
                    name = 'packet/figures/' + figure['figure_id'] + '/' + Path(path).name
                    restricted_figure_files[name] = restricted
    filtered = _filter_snapshot(target, permissions)
    # Records contain both the export document and applied conflict outcomes.
    records = deepcopy(release['data'])
    records['snapshot'] = _filter_snapshot(records['snapshot'], permissions)
    if records.get('review_execution', {}).get('status') != 'captured':
        omissions.append({'kind': 'review_execution', 'reason': '没有保存可绑定到本次Reviewer的实际运行记录；不推断其模型调用或文件读取成功'})
    if not all_original:
        for session in records.get('review_execution', {}).get('sessions', []):
            for item in session.get('tool_records', []):
                record = item['record']
                item['record'] = {key: record[key] for key in ('tool_id', 'tool', 'status', 'exit_code', 'record_hash') if key in record}
                item['omission'] = '原件未全部授权打包，仅保留Reviewer查询元数据'
    if records != release['data']:
        transformations.append({'file': 'records.json', 'reason': '按来源导出选择限制固定记录中的证据摘录'})
    for name in release_manifest['files']:
        if name == 'records.json' or name == 'packet/target.json':
            continue
        matching = next((sid for sid, names in source_files.items() if name in names), None)
        if matching and permissions[matching]['mode'] != 'original':
            omissions.append({'file': name, 'source_id': matching, 'reason': permissions[matching]['reason']})
            continue
        if name in restricted_figure_files:
            omissions.append({'file': name, 'source_ids': restricted_figure_files[name],
                              'kind': 'source_derived_figure',
                              'reason': '图表引用来源未全部授权原件；保留报告图片，省略可能包含完整来源的计算数据或脚本'})
            continue
        # These duplicate full source text or raw tool output. Restricted source
        # permission must not be defeated by an alternate copy in a history log.
        if not all_original and (name == 'packet/target-long-text.json' or name.startswith('packet/history/tools/')):
            omissions.append({'file': name, 'reason': '存在未授权原件；省略可能包含原文的重复文本或工具输出，保留工具索引'})
            continue
        if name.endswith('.json'):
            value = json.loads(safe_file(folder, name).read_text())
            scrubbed = _scrub(value)
            if scrubbed != value:
                json_blob(name, value)
                continue
        copy(name)
    json_blob('records.json', records)
    json_blob('review-execution.json', records.get('review_execution', {'status': 'unavailable', 'sessions': []}))
    if filtered == target and _scrub(filtered) == filtered:
        copy('packet/target.json')
    else:
        json_blob('packet/target.json', filtered)
    if filtered != target:
        transformations.append({'file': 'packet/target.json', 'reason': '按来源导出选择限制证据摘录'})
    for item in source_index:
        sid = item['id']
        if permissions[sid]['mode'] == 'original' and not item.get('original_file') and not item.get('text_file'):
            omissions.append({'source_id': sid, 'reason': '运行时未保存独立二进制原件；包内仅有已保存来源正文和定位'})
    # Templates are not necessary to reread the fixed Word file. Their source
    # identity/specification remains in records; raw template is not silently
    # exported as an extra user-owned document.
    if records['export_input'].get('template'):
        omissions.append({'kind': 'template_original', 'reason': '仅提供冻结模板标识、样式说明及最终Word，不打包历史模板原件'})
    blobs['release-manifest.json'] = safe_file(store.root, release['result']['manifest_path']).read_bytes()
    for item in transformations:
        if item['file'] in release_manifest['files']:
            item['original_sha256'] = release_manifest['files'][item['file']]
        item['included_sha256'] = sha(blobs[item['file']])
    manifest = {'schema_version': SCHEMA_VERSION, 'release_id': release['id'], 'version_id': release['version_id'],
                'run_id': release['data']['run_id'], 'review_id': release['data']['review_id'],
                'review_fingerprint': release['data']['review_fingerprint'],
                'release_fingerprint': release['fingerprint'], 'release_manifest_hash': release['result']['manifest_hash'],
                'report_sha256': release['result']['sha256'], 'source_permissions': permissions,
                'review_files': release['data']['review_files'],
                'original_files': release_manifest['files'], 'omissions': omissions,
                'transformations': transformations,
                'complete_materials': not omissions and not transformations,
                'files': {name: sha(blob) for name, blob in blobs.items()},
                'validation_scope': '结构、文件完整性和版本关联；不判断事实真伪，不提供防本机管理员篡改保证。'}
    blobs['manifest.json'] = dump(manifest).encode()
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for name in sorted(blobs):
            archive.writestr(name, blobs[name])
    stage('离线校验文件、版本和证据关系')
    blob = buffer.getvalue()
    checked = verify_bundle(blob)
    if not checked['valid']:
        raise ValueError('审计包一致性校验失败：' + '；'.join(checked['errors']))
    destination = store.root / 'exports' / job['id'] / 'audit.zip'
    if destination.parent.is_symlink() or destination.is_symlink() or (store.root / 'exports').is_symlink():
        raise ValueError('审计包路径不能为符号链接')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    if temporary.is_symlink():
        raise ValueError('审计包临时文件不能为符号链接')
    temporary.write_bytes(blob)
    stage('审计包已制作，可单独下载')
    os.replace(temporary, destination)
    return {'release_id': release['id'], 'version_id': release['version_id'], 'fingerprint': expected,
            'path': str(destination.relative_to(store.root)), 'sha256': sha(blob),
            'validation': checked, 'download_url': '/api/audit-file?job=' + job['id']}


def bundle_file(store, job):
    if isinstance(job, str):
        job = store.one('jobs', job)
    if job['kind'] != 'audit_bundle' or job['status'] != 'complete' or not job.get('result'):
        raise ValueError('审计包尚未制作完成')
    result = json.loads(job['result'])
    path = safe_file(store.root, result['path'])
    if sha(path.read_bytes()) != result['sha256']:
        raise ValueError('审计包文件已变化，请重新导出')
    return path


def verify_bundle(value):
    """Read a ZIP or bytes without extracting or executing any packaged script."""
    errors = []
    omissions = []
    manifest = {}
    try:
        stream = BytesIO(value) if isinstance(value, bytes) else value
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(set(names)) != len(names):
                raise ValueError('包中含重复文件名')
            if sum(item.file_size for item in entries) > 2_000_000_000:
                raise ValueError('审计包超过离线校验大小上限')
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name:
                    raise ValueError('包中含越界路径')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('schema_version') != SCHEMA_VERSION:
                raise ValueError('不支持的审计包版本')
            omissions = manifest['omissions']
            if set(names) != set(manifest['files']) | {'manifest.json'}:
                raise ValueError('文件清单与实际包内容不一致')
            for name, digest in manifest['files'].items():
                if sha(archive.read(name)) != digest:
                    errors.append('文件哈希不匹配：' + name)
            records = json.loads(archive.read('records.json'))
            target = json.loads(archive.read('packet/target.json'))
            _check_package_coverage(archive, records, manifest, errors)
            report_hash = sha(archive.read('report.docx'))
            if report_hash != manifest['report_sha256']:
                errors.append('正式Word哈希不匹配')
            for key in ('version_id', 'run_id', 'review_id', 'review_fingerprint'):
                if records.get(key) != manifest.get(key):
                    errors.append('正式记录关联不一致：' + key)
            if target['version_id'] != manifest['version_id'] or target['brief_hash'] != records['brief_hash']:
                errors.append('核查正文与正式版本不一致')
            review = records['review_result']
            if json.loads(archive.read('review-execution.json')) != records.get('review_execution', {'status': 'unavailable', 'sessions': []}):
                errors.append('Reviewer查询记录与固定交付输入不一致')
            if review['version_id'] != manifest['version_id'] or review['fingerprint'] != manifest['review_fingerprint']:
                errors.append('Review绑定不一致')
            if records['export_input']['version_id'] != manifest['version_id'] or records['export_fingerprint'] != sha(dump(records['export_input']).encode()):
                errors.append('Word固定输入与版本不一致')
            transformed_files = {item['file'] for item in manifest['transformations']}
            if 'records.json' not in transformed_files and sha(dump(records).encode()) != manifest['release_fingerprint']:
                errors.append('正式交付指纹与固定输入不一致')
            # An unmodified full target allows exact reconstruction of what the
            # independent Reviewer read. Partial packages explicitly say why not.
            target_hash = sha(archive.read('packet/target.json'))
            original_target_hash = manifest['review_files'].get('target.json')
            if target_hash == original_target_hash:
                expected = sha(dump({'target': target, 'files': {k: v for k, v in manifest['review_files'].items() if k != 'index.json'}}).encode())
                if expected != manifest['review_fingerprint']:
                    errors.append('核查包fingerprint与实际核查输入不一致')
            elif not any(item.get('file') == 'packet/target.json' for item in manifest['transformations']):
                errors.append('核查目标被改写但没有省略/变换记录')
            _check_relationships(target, records, manifest, errors)
            _check_materials(archive, target, manifest, errors)
            actually_complete = not omissions and not manifest['transformations'] and all(
                item['mode'] == 'original' for item in manifest['source_permissions'].values())
            if manifest.get('complete_materials') != actually_complete:
                errors.append('完整材料标记与实际省略/变换范围不一致')
    except (ValueError, KeyError, TypeError, AttributeError, OSError, BadZipFile, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return {'valid': not errors, 'scope': 'structure_and_consistency',
            'message': '结构与一致性校验通过；未自动判断事实真伪' if not errors else '结构与一致性校验未通过',
            'errors': errors, 'omissions': omissions,
            'complete_materials': bool(manifest.get('complete_materials')) and not errors,
            'release_id': manifest.get('release_id'), 'version_id': manifest.get('version_id')}


def _check_materials(archive, target, manifest, errors):
    names = set(manifest['files'])
    sources = {item['id']: item for item in target['sources']}
    index = json.loads(archive.read('packet/index.json'))
    if index.get('files') != {key: value for key, value in manifest['review_files'].items() if key != 'index.json'} or index.get('fingerprint') != manifest['review_fingerprint'] or index.get('version_id') != manifest['version_id']:
        errors.append('核查索引与冻结Review文件清单不一致')
    if {item['id'] for item in index['sources']} != set(sources):
        errors.append('来源索引与被审来源集合不一致')
    for item in index['sources']:
        source = sources.get(item['id'], {})
        for key, hash_key in (('text_file', 'hash'), ('original_file', 'original_hash')):
            if not item.get(key):
                continue
            name = 'packet/' + item[key]
            if name in names and sha(archive.read(name)) != source.get(hash_key):
                errors.append('来源文件与证据版本不一致：' + item['id'])
            if name not in names and not any(row.get('file') == name for row in manifest['omissions']):
                errors.append('来源文件缺失且未说明：' + name)
        for relative in item.get('visual_files', []):
            name = 'packet/' + relative
            if not relative.startswith('sources/' + item['id'] + '.') or relative not in manifest['review_files']:
                errors.append('来源视觉索引未绑定所属来源或冻结文件：' + relative)
            if name in names:
                if manifest['source_permissions'][item['id']]['mode'] != 'original':
                    errors.append('受限来源的原图或页面图不应出现在包中：' + relative)
                if sha(archive.read(name)) != manifest['review_files'].get(relative):
                    errors.append('来源视觉文件与核查输入不一致：' + relative)
            elif not any(row.get('file') == name and row.get('source_id') == item['id'] for row in manifest['omissions']):
                errors.append('来源视觉文件缺失且未声明所属来源的省略：' + relative)
    for figure in target.get('figures', []):
        if figure['run_id'] != manifest['run_id']:
            errors.append('图表属于其他报告：' + figure['figure_id'])
        for sid, digest in figure['source_hashes'].items():
            if sources.get(sid, {}).get('hash') != digest:
                errors.append('图表来源与正文来源版本不一致：' + figure['figure_id'])
        for key in ('image', 'data', 'script'):
            path = figure.get(key + '_path')
            if path:
                name = 'packet/figures/' + figure['figure_id'] + '/' + Path(path).name
                omitted = any(item.get('file') == name for item in manifest['omissions'])
                transformed = any(item.get('file') == name and item.get('original_sha256') == figure['hashes'][key]
                                  for item in manifest['transformations'])
                if (name not in names and not omitted) or (name in names and sha(archive.read(name)) != figure['hashes'][key] and not transformed):
                    errors.append('图表文件与计算/图表登记不一致：' + name)
                if key == 'image' and (omitted or transformed):
                    errors.append('正式报告图像不能省略或替换：' + name)
    if 'packet/visual-inputs.json' in names:
        visual = json.loads(archive.read('packet/visual-inputs.json'))
        if visual.get('version_id') != manifest['version_id']:
            errors.append('视觉输入索引属于其他报告版本')
        source_visuals = {item['id']: set(item.get('visual_files', [])) for item in index['sources']}
        figures = {item['figure_id']: item for item in target.get('figures', [])}
        indexed = set()
        for item in visual.get('images', []):
            relative = item.get('file')
            if not relative:
                if not item.get('unavailable'):
                    errors.append('视觉输入既无固定文件也没有不可用说明')
                continue
            indexed.add(relative)
            name = 'packet/' + relative
            if relative not in manifest['review_files'] or item.get('sha256') != manifest['review_files'].get(relative):
                errors.append('视觉输入索引与冻结文件哈希不一致：' + relative)
            if item.get('source_id') and relative not in source_visuals.get(item['source_id'], set()):
                errors.append('视觉输入索引未绑定所属来源：' + relative)
            if item.get('figure_id'):
                figure = figures.get(item['figure_id'], {})
                expected = 'figures/' + item['figure_id'] + '/' + Path(figure.get('image_path', '')).name
                if relative != expected or item.get('sha256') != figure.get('hashes', {}).get('image'):
                    errors.append('视觉输入索引未绑定报告图表：' + relative)
            if name not in names and not any(row.get('file') == name for row in manifest['omissions']):
                errors.append('视觉输入索引引用文件缺失且未说明：' + relative)
        if any(not paths.issubset(indexed) for paths in source_visuals.values()):
            errors.append('来源视觉文件未列入实际视觉输入索引')
    if 'packet/history/tools.json' in names:
        tool_index = json.loads(archive.read('packet/history/tools.json'))
        executions = (json.loads(archive.read('packet/history/executions.json'))
                      if 'packet/history/executions.json' in names else None)
        job_ids = {item['job_id'] for item in executions} if executions is not None else None
        for item in tool_index:
            relative = item['file']
            name = 'packet/' + relative
            if not relative.startswith('history/tools/') or relative not in manifest['review_files']:
                errors.append('工具索引引用不属于本次冻结核查包：' + relative)
                continue
            if job_ids is not None and item['job_id'] not in job_ids:
                errors.append('工具索引引用了未记录的执行任务：' + relative)
            if name not in names:
                if not any(row.get('file') == name for row in manifest['omissions']):
                    errors.append('工具索引引用的记录缺失且未说明：' + relative)
                continue
            saved = json.loads(archive.read(name))
            if saved.get('job_id') != item['job_id'] or saved['record'].get('tool') != item['tool'] or saved['record'].get('status') != item['status']:
                errors.append('工具记录与执行索引不一致：' + relative)
    for name in names:
        if not name.startswith('packet/history/tools/'):
            continue
        tool = json.loads(archive.read(name))['record']
        if tool.get('record_hash') and sha(dump({k: v for k, v in tool.items() if k != 'record_hash'}).encode()) != tool['record_hash']:
            if not any(row.get('file') == name for row in manifest['transformations']):
                errors.append('实际工具记录哈希不一致：' + name)


def _check_package_coverage(archive, records, manifest, errors):
    """Every frozen input is present intact, explicitly transformed, or omitted."""
    raw = archive.read('release-manifest.json')
    release = json.loads(raw)
    if sha(raw) != manifest['release_manifest_hash']:
        errors.append('原始正式交付清单哈希不一致')
    for key in ('release_id', 'version_id', 'run_id', 'review_id', 'review_fingerprint'):
        if release.get(key) != manifest.get(key):
            errors.append('原始正式交付清单关联不一致：' + key)
    if release.get('fingerprint') != manifest['release_fingerprint'] or release.get('files') != manifest['original_files']:
        errors.append('原始正式交付输入或文件清单不一致')
    if manifest['review_files'] != records['review_files']:
        errors.append('Review文件清单与固定交付记录不一致')
    expected = dict(release['files'])
    for relative, digest in manifest['review_files'].items():
        name = 'packet/' + relative
        if expected.get(name) != digest:
            errors.append('Review文件哈希与原始正式件清单不一致：' + name)
    present = set(manifest['files'])
    omissions = {item['file']: item for item in manifest['omissions'] if item.get('file')}
    transformed = {}
    for item in manifest['transformations']:
        name = item['file']
        transformed[name] = item
        if name not in present or not item.get('reason') or item.get('included_sha256') != manifest['files'].get(name):
            errors.append('文件变换记录未绑定实际输出：' + name)
        if name in expected and item.get('original_sha256') != expected[name]:
            errors.append('文件变换记录未绑定原始输入：' + name)
    for name, item in omissions.items():
        if name not in expected or name in present or not item.get('reason'):
            errors.append('文件省略记录与冻结输入不一致：' + name)
    for name, digest in expected.items():
        if name not in present:
            if name not in omissions:
                errors.append('冻结交付文件缺失且没有明确省略记录：' + name)
        elif manifest['files'][name] != digest and name not in transformed:
            errors.append('冻结交付文件改变且没有绑定变换记录：' + name)


def _check_relationships(target, records, manifest, errors):
    sources = {item['id']: item for item in target['sources']}
    claims = {}
    spans = {}

    def collect(node):
        identity = node['claim_id']
        claim = node.get('claim', {})
        if claim.get('id') != identity or claim.get('run_id') != manifest['run_id']:
            errors.append('主张ID或报告关系不一致：' + identity)
        claims[identity] = claim
        for span in node.get('evidence', []):
            spans[span['id']] = span
            source = sources.get(span['source_id'])
            if not source or source['hash'] != span['source_hash']:
                errors.append('证据片段未绑定该来源版本：' + span['id'])
        for child in node.get('premises', []):
            collect(child)

    for binding in target['evidence']['bindings'] + target.get('candidate_claims', []):
        collect(binding)
    for identity, claim in claims.items():
        for premise in claim.get('data', {}).get('premise_claim_ids', []):
            if premise not in claims:
                errors.append('推断前提不在证据闭包：' + identity)
        for support in claim.get('data', {}).get('supports', []):
            if support['span_id'] not in spans:
                errors.append('主张引用了不存在的证据片段：' + identity)
    for binding in target['evidence']['bindings']:
        if binding.get('target_version') != manifest['version_id']:
            errors.append('正文绑定目标版本不一致：' + binding['id'])
    # Re-run only deterministic delivery rules against the accepted frozen state.
    # This never promotes an incomplete review to semantic support.
    from .release import decision
    gate = decision(records['snapshot'], records['review_result'], records['findings'])
    if not gate['eligible']:
        errors.extend('包内仍有正式交付阻断：' + item['message'] for item in gate['blockers'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='离线校验 BriefLoop 审计包结构及版本一致性；不执行包内脚本')
    parser.add_argument('path')
    args = parser.parse_args()
    result = verify_bundle(Path(args.path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['valid'] else 1)
