"""Start from a previous report (#858).

A user imports a report their team already delivered (Markdown, text or DOCX).
It becomes a report in the workspace whose original is the text before any
Word tracked changes and whose user revision is the text after them, so the
tracked changes enter the revision-learning loop as the first edits to
classify. The chat agent then reads the imported file and proposes the brief
contract (reader, structure, conventions, things to avoid) for the user to
confirm; nothing here guesses that contract in Python.
"""
from io import BytesIO
from pathlib import Path
import json
import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
INSERTED = (W + 'ins', W + 'moveTo')
DELETED = (W + 'del', W + 'moveFrom')
TEXT_KINDS = ('.md', '.markdown', '.txt', '.docx')


def _inside(element, tags, stop):
    parent = element.getparent()
    while parent is not None and parent is not stop:
        if parent.tag in tags:
            return True
        parent = parent.getparent()
    return False


def _paragraph_versions(paragraph):
    before, after = [], []
    for node in paragraph.iter(W + 't', W + 'delText', W + 'tab', W + 'br'):
        text = node.text or '' if node.tag in (W + 't', W + 'delText') else ('\t' if node.tag == W + 'tab' else '\n')
        if node.tag == W + 'delText' or _inside(node, DELETED, paragraph):
            before.append(text)
        elif _inside(node, INSERTED, paragraph):
            after.append(text)
        else:
            before.append(text)
            after.append(text)
    return ''.join(before).strip(), ''.join(after).strip()


def _heading_level(paragraph):
    style = paragraph.find(f'{W}pPr/{W}pStyle')
    value = style.get(W + 'val') if style is not None else ''
    match = re.search(r'(?:heading|标题)\s*(\d)', value or '', re.I)
    return int(match[1]) if match else 0


def docx_versions(data):
    """Markdown of the document before and after its tracked changes, and
    whether it had any."""
    from docx import Document
    body = Document(BytesIO(data)).element.body
    before, after, changed = [], [], False
    for block in body:
        if block.tag == W + 'p':
            old, new = _paragraph_versions(block)
            level = _heading_level(block)
            prefix = '#' * level + ' ' if level else ''
            if old:
                before.append(prefix + old)
            if new:
                after.append(prefix + new)
            changed = changed or old != new
        elif block.tag == W + 'tbl':
            for row in block.iter(W + 'tr'):
                cells = [[_paragraph_versions(p) for p in cell.iter(W + 'p')] for cell in row.iter(W + 'tc')]
                old = ' | '.join(' '.join(v[0] for v in cell if v[0]) for cell in cells)
                new = ' | '.join(' '.join(v[1] for v in cell if v[1]) for cell in cells)
                if old.strip(' |'):
                    before.append('| ' + old + ' |')
                if new.strip(' |'):
                    after.append('| ' + new + ' |')
                changed = changed or old != new
    return '\n\n'.join(before), '\n\n'.join(after), changed


def versions(name, data):
    suffix = Path(name).suffix.lower()
    if suffix not in TEXT_KINDS:
        raise ValueError('往期报告请导入 Markdown、TXT 或 DOCX')
    if suffix == '.docx':
        return docx_versions(data)
    text = data.decode('utf-8-sig')
    return text, text, False


def _title(name, markdown):
    for line in markdown.splitlines():
        if line.startswith('#'):
            return line.lstrip('#').strip()[:200]
    return Path(name).stem[:200] or '往期报告'


def import_previous(store, name, data):
    from .sources import upload
    before, after, changed = versions(name, data)
    if not before.strip():
        raise ValueError('往期报告没有可导入的正文')
    source = upload(store, name, data)
    if source['status'] != 'ready':
        raise ValueError('往期报告无法读取，原件已保留')
    provenance = store.root / 'sources' / (source['id'] + '.provenance.json')
    if provenance.is_file():
        metadata = json.loads(provenance.read_text(encoding='utf-8'))
        metadata['usage'] = 'previous_report'
        provenance.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    title = _title(name, before)
    run = store.create_run({'title': title, 'objective': '往期报告（导入）：用于反推简报约定和学习当时的改稿', 'allow_web': False,
                            'fact_check': False}, [source['id']], remember_requirements=False)
    original = store.publish(run['id'], {'title': title, 'markdown': before}, author='import')
    revision = None
    if changed and after.strip() and after != before:
        revision = store.revise(original['id'], markdown=after, allow_markdown_conversion=True)
    store.event(None, 'previous_report_imported', {'source_id': source['id'], 'run_id': run['id'],
                                                   'original': original['id'], 'revision': revision['id'] if revision else None})
    return {'status': 'imported', 'source_id': source['id'], 'run_id': run['id'], 'title': title,
            'version_id': (revision or original)['id'], 'tracked_changes': bool(revision)}
