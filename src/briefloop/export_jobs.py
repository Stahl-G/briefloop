"""Button-triggered, version-bound Word file jobs. No model invocation."""
import hashlib
import json
import os
from pathlib import Path
from .store import dump, now, uid
from .document_model import brief_document, source_ids
from .document_export import reader_source_blocks
from .figure_support import export_figures


def export_input(store, brief, template_override=None):
    requirements = json.loads(store.one('runs', brief['run_id'])['requirements'])
    if template_override:
        # Same saved content, different layout: the override only swaps the
        # rendering template and must change the export fingerprint.
        requirements = {**requirements, 'template_id': template_override}
    figures = export_figures(store, brief)
    document = brief_document(brief)
    # Both Word paths pass the run's current sources into render_document.
    # An internal source index is only replaced when all its rows resolve, so
    # determine the effective blocks before choosing metadata for the hash.
    run_sources = {sid: store.one('sources', sid) for sid in store.source_ids(brief['run_id'])}
    blocks, indexed = reader_source_blocks(document, run_sources)
    rendered_ids = dict.fromkeys(source_ids({'type': 'doc', 'content': blocks}) + indexed)
    rendered_sources = {sid: {'name': run_sources[sid]['name'], 'url': run_sources[sid]['url'] or ''}
                        if sid in run_sources else None for sid in rendered_ids}
    identity = {'renderer': 28 if requirements.get('template_id') else 29, 'version_id': brief['id'], 'brief_hash': brief['hash'],
                'document': document, 'detail': json.loads(brief['detail']),
                'requirements': requirements,
                'sources': rendered_sources,
                'figures': {fid: {**{k: v for k, v in f.items() if k != 'image_bytes'},
                                  'image_hash': hashlib.sha256(f['image_bytes']).hexdigest()} for fid, f in figures.items()}}
    if requirements.get('template_id'):
        from .templates import template
        selected=template(store,requirements['template_id'])
        if selected['status']!='ready':raise ValueError('模板尚未准备完成')
        identity['template']={'id':selected['id'],'revision':selected['revision'],'source_hash':selected['source_hash'],'spec':selected['spec']}
    return identity, figures


def enqueue_export(store, version_id, template_override=None):
    brief = store.one('briefs', version_id)
    identity, _ = export_input(store, brief, template_override)
    digest = hashlib.sha256(dump(identity).encode()).hexdigest()
    payload = {'version_id': version_id, 'run_id': brief['run_id'], 'fingerprint': digest}
    if template_override:payload['template_id'] = template_override
    # Admission is serialized by SQLite across request handlers and processes.
    # Keep lookup and insert together; Store.enqueue opens a separate transaction.
    with store.tx() as c:
        rows = c.execute("SELECT * FROM jobs WHERE kind='export_docx' "
                         "AND json_extract(payload,'$.fingerprint')=? "
                         "AND status IN ('queued','running','complete') ORDER BY rowid DESC", (digest,))
        for row in rows:
            job = dict(row)
            if job['status'] != 'complete':return job
            try:
                path = output_path(store, job)
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == json.loads(job['result'] or '{}').get('sha256'):
                    return job
            except (OSError, ValueError):
                # A missing, unreadable or invalid cached artifact can be rebuilt.
                continue
        jid = uid('job')
        c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
                  (jid, 'export_docx', 'queued', dump(payload), None, None, now(), now()))
    job = store.one('jobs', jid)
    from .task_notify import notify
    notify(store, job, 'queued')
    store.wake_jobs()
    return job


def output_path(store, job):
    if job['kind'] != 'export_docx': raise ValueError('不是 Word 文件任务')
    path = store.root / 'exports' / job['id'] / 'report.docx'
    if not path.resolve().is_relative_to((store.root / 'exports').resolve()): raise ValueError('无效导出路径')
    result = json.loads(job.get('result') or '{}')
    if result.get('path'):
        saved = (store.root / result['path']).resolve()
        if saved.parent != path.parent.resolve() or saved.suffix.lower() != '.docx':
            raise ValueError('无效导出结果路径')
        path = saved
    return path


def generate_word(store, job, cancelled):
    from .exports import docx_bytes
    payload = json.loads(job['payload']); brief = store.one('briefs', payload['version_id'])
    def stage(step, message):
        if cancelled.is_set(): raise InterruptedError('Word 制作已停止')
        store.event(job['id'], 'export_progress', {'step': step, 'total': 4, 'message': message})
    stage(1, '读取已保存的报告版本')
    identity, figures = export_input(store, brief, payload.get('template_id'))
    if hashlib.sha256(dump(identity).encode()).hexdigest() != payload['fingerprint']:
        raise ValueError('导出输入已变化，请对当前报告重新生成 Word')
    req = identity['requirements']; detail = identity['detail']
    # Render with the exact source projection that was fingerprinted. A source
    # can be attached or edited while Word is being built; re-reading it here
    # would save different content under the old cache identity. Unresolved
    # citations are absent from the mapping so render_document uses its normal
    # missing-source label instead of dereferencing a None projection entry.
    source_records = {sid: record for sid, record in identity['sources'].items() if record is not None}
    stage(2, '填充正文、表格和图表')
    if req.get('template_id'):
        from .templates import export_template
        blob = export_template(store, brief, identity['document'], figures,
                               template_id=payload.get('template_id'), source_records=source_records)
    else:
        blob = docx_bytes(document=identity['document'], report_profile=req.get('report_profile', 'brief'),
                          title=detail.get('title', req.get('title', '')), report_date=req.get('report_date', ''),
                          organization=req.get('organization', ''), period=req.get('period', ''), industry=req.get('industry', ''),
                          figures=figures, source_records=source_records, language=req.get('language'),
                          citations=detail.get('citations',[]))
    stage(3, '检查 Word 文件和资源')
    from docx import Document
    from io import BytesIO
    Document(BytesIO(blob))
    destination = output_path(store, job); destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp'); temporary.write_bytes(blob)
    if cancelled.is_set(): temporary.unlink(); raise InterruptedError('Word 制作已停止')
    try:
        os.replace(temporary, destination)
    except PermissionError:
        if os.name!='nt' or not destination.exists():raise
        # Office/WPS may hold a deny-delete handle. Keep both the old file and
        # this already-rendered version; no generation/model work is repeated.
        from uuid import uuid4
        destination = destination.with_name('report-'+uuid4().hex+'.docx')
        os.replace(temporary, destination)
        store.event(job['id'],'export_saved_as',{'path':str(destination.relative_to(store.root)),
                                               'reason':'原文件被占用或不可替换，已另存本次 Word'})
    stage(4, 'Word 已生成，可以下载')
    return {'version_id': brief['id'], 'fingerprint': payload['fingerprint'],
            'path': str(destination.relative_to(store.root)), 'sha256': hashlib.sha256(blob).hexdigest(),
            'download_url': '/api/export-file?job=' + job['id']}
