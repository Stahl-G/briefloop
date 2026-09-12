"""Button-triggered, version-bound Word file jobs. No model invocation."""
import hashlib
import json
import os
from pathlib import Path
from .store import dump
from .document_model import brief_document
from .figure_support import export_figures


def export_input(store, brief, template_override=None):
    requirements = json.loads(store.one('runs', brief['run_id'])['requirements'])
    if template_override:
        # Same saved content, different layout: the override only swaps the
        # rendering template and must change the export fingerprint.
        requirements = {**requirements, 'template_id': template_override}
    figures = export_figures(store, brief)
    identity = {'renderer': 5 if requirements.get('template_id') else 6, 'version_id': brief['id'], 'brief_hash': brief['hash'],
                'document': brief_document(brief), 'detail': json.loads(brief['detail']),
                'requirements': requirements,
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
    # Repeated clicks share only an identical immutable input, never another draft.
    for job in store.rows("SELECT * FROM jobs WHERE kind='export_docx' ORDER BY rowid DESC LIMIT 50"):
        payload = json.loads(job['payload'])
        if payload.get('fingerprint') == digest and job['status'] in ('queued', 'running', 'complete'):
            if job['status'] != 'complete':return job
            path=output_path(store,job)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==json.loads(job['result'] or '{}').get('sha256'):return job
    payload = {'version_id': version_id, 'run_id': brief['run_id'], 'fingerprint': digest}
    if template_override:payload['template_id'] = template_override
    return store.enqueue('export_docx', payload)


def output_path(store, job):
    if job['kind'] != 'export_docx': raise ValueError('不是 Word 文件任务')
    path = store.root / 'exports' / job['id'] / 'report.docx'
    if not path.resolve().is_relative_to((store.root / 'exports').resolve()): raise ValueError('无效导出路径')
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
    stage(2, '填充正文、表格和图表')
    if req.get('template_id'):
        from .templates import export_template
        blob = export_template(store, brief, identity['document'], figures, template_id=payload.get('template_id'))
    else:
        blob = docx_bytes(document=identity['document'], report_profile=req.get('report_profile', 'brief'),
                          title=detail.get('title', req.get('title', '')), report_date=req.get('report_date', ''),
                          organization=req.get('organization', ''), period=req.get('period', ''), industry=req.get('industry', ''),
                          figures=figures, source_records={sid: store.one('sources', sid) for sid in store.source_ids(brief['run_id'])},language=req.get('language'))
    stage(3, '检查 Word 文件和资源')
    from docx import Document
    from io import BytesIO
    Document(BytesIO(blob))
    destination = output_path(store, job); destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp'); temporary.write_bytes(blob)
    if cancelled.is_set(): temporary.unlink(); raise InterruptedError('Word 制作已停止')
    os.replace(temporary, destination)
    stage(4, 'Word 已生成，可以下载')
    return {'version_id': brief['id'], 'fingerprint': payload['fingerprint'],
            'path': str(destination.relative_to(store.root)), 'sha256': hashlib.sha256(blob).hexdigest(),
            'download_url': '/api/export-file?job=' + job['id']}
