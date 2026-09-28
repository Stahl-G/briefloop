"""Deterministic original-file to template conversion, without a research job.

This is deliberately separate from revision import: source-looking text belongs
to the uploaded document and must not become citations or learning feedback.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
from urllib.parse import urlsplit

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .document_model import document_markdown, markdown_document, normalize_document


class ConversionError(ValueError):
    """An unsupported original remains downloadable as an ordinary source."""
    def __init__(self, message, source_id):
        self.source_id = source_id
        self.reason = message
        super().__init__(f'{message}；原件已保留（{source_id}），未生成转换稿')


def _text(value, marks=None):
    return {'type': 'text', 'text': value, **({'marks': marks} if marks else {})}


def _decode(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = data.decode('gb18030')
        except UnicodeDecodeError as exc:
            raise ValueError('文本编码无法读取，请另存为 UTF-8') from exc
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]', text):
        raise ValueError('文本含无法写入 Word 的控制字符')
    return text


def _plain_document(text):
    # Do not send TXT through Markdown: #, *, [links] and source IDs are literal.
    return {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [_text(line)] if line else []}
        for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]}


def _markdown_document(text, notes):
    from markdown_it import MarkdownIt
    environment = {}
    tokens = MarkdownIt('commonmark').enable(['table', 'strikethrough']).parse(text, environment)
    if any(label.startswith('^') for label in environment.get('references', {})):
        raise ValueError('Markdown 脚注暂不支持转换')
    list_depth = 0
    for token in tokens:
        if token.type in ('ordered_list_open', 'bullet_list_open'):
            if list_depth:
                raise ValueError('Markdown 多级列表暂不支持转换，请先整理为单级列表')
            list_depth += 1
        elif token.type in ('ordered_list_close', 'bullet_list_close'):
            list_depth -= 1
        for child in [token, *(token.children or [])]:
            if child.type in ('html_block', 'html_inline'):
                raise ValueError('Markdown 含原始 HTML，当前不能可靠转换')
            if child.type == 'image':
                raise ValueError('Markdown 图片暂不支持转换，请保留原件并移除图片后重试')
            if child.type == 'link_open' and urlsplit(child.attrGet('href') or '').scheme.lower() not in ('http', 'https', 'mailto'):
                raise ValueError('Markdown 含相对路径或文档内部链接，当前不能可靠转换')
    document = markdown_document(text)

    def visit(node):
        if node['type'] == 'citation':
            return [_text('[@' + node['attrs']['sourceId'] + ']')]
        children = []
        for child in node.get('content', []):
            children.extend(visit(child))
        if 'content' in node:
            node['content'] = children
        if node['type'] != 'orderedList':
            return [node]
        # The Word renderer's built-in List Number style does not preserve
        # restarts. Freeze the visible values instead of silently renumbering.
        notes.append('编号列表的序号保留为正文文字，以避免 Word 自动重新编号。')
        result = []
        start = node.get('attrs', {}).get('start', 1)
        for offset, item in enumerate(children):
            blocks = item.get('content', [])
            if len(blocks) != 1 or blocks[0]['type'] not in ('paragraph', 'heading'):
                raise ValueError('Markdown 编号列表结构暂不支持转换')
            blocks[0].setdefault('content', []).insert(0, _text(f'{start + offset}. '))
            result.extend(blocks)
        return result

    return visit(document)[0]


def _word_document(data, notes):
    """Read only supported body content; reject objects we cannot preserve."""
    from .media import office_archive
    office_archive(data).close()
    try:
        doc = Document(BytesIO(data))
    except Exception as exc:
        raise ValueError('DOCX 无法读取，文件可能已损坏') from exc
    unsupported = {
        'drawing': '图片、图表或绘图对象', 'pict': '图片或形状', 'object': '嵌入对象',
        'fldSimple': '域', 'fldChar': '域', 'instrText': '域',
        'footnoteReference': '脚注', 'endnoteReference': '尾注',
        'ins': '修订标记', 'del': '修订标记', 'moveFrom': '修订标记', 'moveTo': '修订标记',
        'commentRangeStart': '批注', 'commentReference': '批注', 'sdt': '内容控件',
        'altChunk': '外部内容', 'sym': '字体符号', 'subDoc': '子文档',
    }
    for element in doc.element.body.iter():
        local = element.tag.rsplit('}', 1)[-1]
        if local == 'sectPr' and element.getparent().tag == qn('w:pPr'):
            raise ValueError('Word 含分节符，当前不能可靠转换')
        if local in unsupported:
            raise ValueError('Word 含' + unsupported[local] + '，当前不能可靠转换')
        if element.tag.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/math}'):
            raise ValueError('Word 含公式对象，当前不能可靠转换')
    # Imported running headers/footers cannot be silently replaced by the
    # chosen template when they contain authored text or other objects.
    for rel in doc.part.rels.values():
        if not rel.is_external and rel.reltype.rsplit('/', 1)[-1] in ('header', 'footer'):
            part = rel.target_part.element
            if any((e.text or '').strip() for e in part.iter(qn('w:t'))) or any(
                    e.tag.rsplit('}', 1)[-1] in unsupported or
                    e.tag.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/math}')
                    for e in part.iter()):
                raise ValueError('Word 页眉或页脚含内容，当前不能自动替换为模板')

    counters = {}

    def styles_from(style):
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            yield style
            style = style.base_style

    def style_chain(paragraph):
        return styles_from(paragraph.style)

    def font_value(run, paragraph, prop):
        # Character styles can inherit meaning-changing properties, e.g. an
        # exponent style whose base has superscript enabled. Check the same
        # effective chain for rejection and for supported basic emphasis.
        fonts = [run.font, *(s.font for s in styles_from(run.style)),
                 *(s.font for s in style_chain(paragraph))]
        return next((getattr(font, prop) for font in fonts if getattr(font, prop) is not None), None)

    def list_prefix(p):
        properties = [p._p.pPr, *(s.element.pPr for s in style_chain(p))]
        num = next((pr.find(qn('w:numPr')) for pr in properties
                    if pr is not None and pr.find(qn('w:numPr')) is not None), None)
        if num is None:
            return None
        identifier = num.find(qn('w:numId'))
        if identifier is None:
            raise ValueError('Word 列表缺少编号定义')
        identifier = identifier.get(qn('w:val'))
        if identifier == '0':
            return None
        numbering = doc.part.numbering_part.element
        level = num.find(qn('w:ilvl'))
        level = int(level.get(qn('w:val'))) if level is not None else 0
        if level:
            raise ValueError('Word 多级自动编号暂不支持转换')
        entry = next((n for n in numbering.findall(qn('w:num')) if n.get(qn('w:numId')) == identifier), None)
        if entry is None:
            raise ValueError('Word 列表编号定义缺失')
        abstract_id = entry.find(qn('w:abstractNumId')).get(qn('w:val'))
        abstract = next((n for n in numbering.findall(qn('w:abstractNum')) if n.get(qn('w:abstractNumId')) == abstract_id), None)
        if abstract is None:
            raise ValueError('Word 列表编号定义缺失')
        definition = next((n for n in abstract.findall(qn('w:lvl')) if n.get(qn('w:ilvl')) == '0'), None)
        override = next((n for n in entry.findall(qn('w:lvlOverride')) if n.get(qn('w:ilvl')) == '0'), None)
        if override is not None and override.find(qn('w:lvl')) is not None:
            definition = override.find(qn('w:lvl'))
        if definition is None:
            raise ValueError('Word 列表层级定义缺失')
        fmt = definition.find(qn('w:numFmt'))
        fmt = fmt.get(qn('w:val')) if fmt is not None else ''
        if fmt == 'bullet':
            return '• '
        if fmt != 'decimal':
            raise ValueError('Word 列表含非十进制编号，当前不能可靠转换')
        marker = definition.find(qn('w:lvlText'))
        marker = marker.get(qn('w:val')) if marker is not None else '%1.'
        if marker.count('%1') != 1 or re.search(r'%[2-9]', marker):
            raise ValueError('Word 多级自动编号暂不支持转换')
        start = definition.find(qn('w:start'))
        if override is not None and override.find(qn('w:startOverride')) is not None:
            start = override.find(qn('w:startOverride'))
        value = counters.get(identifier, int(start.get(qn('w:val'))) if start is not None else 1)
        counters[identifier] = value + 1
        notes.append('编号列表的序号保留为正文文字，以避免 Word 自动重新编号。')
        return marker.replace('%1', str(value)) + ' '

    def paragraph(element):
        p = Paragraph(element, doc)
        formats = [p.paragraph_format, *(style.paragraph_format for style in style_chain(p))]
        page_break = next((fmt.page_break_before for fmt in formats if fmt.page_break_before is not None), None)
        if page_break:
            raise ValueError('Word 段落含段前分页设置，当前不能可靠转换')
        attrs = {}
        kind = 'paragraph'
        for style in style_chain(p):
            match = re.fullmatch(r'Heading\s*([1-6])', style.name or '', re.I)
            if match or style.style_id == 'Title':
                kind = 'heading'
                attrs['level'] = int(match[1]) if match else 1
                break
        alignment = {0: 'left', 1: 'center', 2: 'right', 3: 'justify'}.get(p.alignment)
        if alignment:
            attrs['textAlign'] = alignment
        content = []
        prefix = list_prefix(p)
        if prefix:
            content.append(_text(prefix))
        allowed = {qn('w:pPr'), qn('w:bookmarkStart'), qn('w:bookmarkEnd'), qn('w:proofErr')}
        for child in element:
            if child.tag in allowed:
                continue
            link = None
            if child.tag == qn('w:hyperlink'):
                rel = doc.part.rels.get(child.get(qn('r:id')))
                if not rel or not rel.is_external or urlsplit(str(rel.target_ref)).scheme.lower() not in ('http', 'https', 'mailto'):
                    raise ValueError('Word 含内部或不支持的链接，当前不能可靠转换')
                link = str(rel.target_ref)
                runs = list(child)
            elif child.tag == qn('w:r'):
                runs = [child]
            else:
                raise ValueError('Word 含不支持的段落内容：' + child.tag.rsplit('}', 1)[-1])
            for run_element in runs:
                if run_element.tag in allowed:
                    continue
                if run_element.tag != qn('w:r'):
                    raise ValueError('Word 超链接含不支持的内容')
                run = Run(run_element, p)
                if any(font_value(run, p, prop) for prop in ('superscript', 'subscript', 'hidden', 'web_hidden')):
                    raise ValueError('Word 含上标、下标或隐藏文字，当前不能可靠转换')
                marks = []
                for prop, mark in [('bold', 'bold'), ('italic', 'italic'), ('underline', 'underline'), ('strike', 'strike')]:
                    if font_value(run, p, prop):
                        marks.append({'type': mark})
                if link:
                    marks.append({'type': 'link', 'attrs': {'href': link}})
                for item in run_element:
                    if item.tag == qn('w:t'):
                        if item.text:
                            content.append(_text(item.text, deepcopy(marks)))
                    elif item.tag == qn('w:tab'):
                        content.append(_text('\t', deepcopy(marks)))
                    elif item.tag in (qn('w:br'), qn('w:cr')):
                        if item.tag == qn('w:br') and item.get(qn('w:type'), 'textWrapping') != 'textWrapping':
                            raise ValueError('Word 含分页或分栏符，当前不能可靠转换')
                        content.append({'type': 'hardBreak'})
                    elif item.tag == qn('w:noBreakHyphen'):
                        content.append(_text('\u2011', deepcopy(marks)))
                    elif item.tag == qn('w:softHyphen'):
                        content.append(_text('\u00ad', deepcopy(marks)))
                    elif item.tag not in (qn('w:rPr'), qn('w:lastRenderedPageBreak')):
                        raise ValueError('Word 含不支持的文字对象：' + item.tag.rsplit('}', 1)[-1])
        return {'type': kind, 'attrs': attrs, 'content': content}

    def table(element):
        rows = []
        above = {}
        if any(child.tag not in (qn('w:tblPr'), qn('w:tblGrid'), qn('w:tr')) for child in element):
            raise ValueError('Word 表格含不支持的包装或附加内容')
        for tr in element.findall(qn('w:tr')):
            if any(child.tag not in (qn('w:trPr'), qn('w:tc')) for child in tr):
                raise ValueError('Word 表格行含不支持的包装或附加内容')
            # The editor grid has no omitted leading/trailing cells. Equal
            # physical cell counts do not imply matching grid positions.
            row_props = tr.find(qn('w:trPr'))
            if row_props is not None and any(
                    int(item.get(qn('w:val'), '0')) != 0
                    for name in ('w:gridBefore', 'w:gridAfter')
                    for item in row_props.findall(qn(name))):
                raise ValueError('Word 表格含省略的行首或行尾单元格，当前不能可靠保留列位置')
            cells = []
            column = 0
            continuing = {}
            header = tr.find(qn('w:trPr') + '/' + qn('w:tblHeader')) is not None
            for tc in tr.findall(qn('w:tc')):
                props = tc.find(qn('w:tcPr'))
                if props is not None and props.find(qn('w:hMerge')) is not None:
                    raise ValueError('Word 含旧式横向合并单元格，当前不能可靠转换')
                span = props.find(qn('w:gridSpan')) if props is not None else None
                colspan = int(span.get(qn('w:val'))) if span is not None else 1
                merge = props.find(qn('w:vMerge')) if props is not None else None
                if merge is not None and merge.get(qn('w:val'), 'continue') == 'continue':
                    origin = above.get(column)
                    if origin is None or origin['attrs']['colspan'] != colspan or any((t.text or '').strip() for t in tc.iter(qn('w:t'))):
                        raise ValueError('Word 表格合并单元格无法可靠对齐')
                    origin['attrs']['rowspan'] = origin['attrs'].get('rowspan', 1) + 1
                    continuing[column] = origin
                    column += colspan
                    continue
                cell = {'type': 'tableHeader' if header else 'tableCell', 'attrs': {'colspan': colspan}, 'content': []}
                for child in tc:
                    if child.tag == qn('w:p'):
                        cell['content'].append(paragraph(child))
                    elif child.tag != qn('w:tcPr'):
                        raise ValueError('Word 嵌套表格或特殊单元格内容暂不支持转换')
                if merge is not None:
                    continuing[column] = cell
                cells.append(cell)
                column += colspan
            above = continuing
            rows.append({'type': 'tableRow', 'content': cells})
        return {'type': 'table', 'content': rows}

    content = []
    for element in doc.element.body:
        if element.tag == qn('w:p'):
            content.append(paragraph(element))
        elif element.tag == qn('w:tbl'):
            content.append(table(element))
        elif element.tag != qn('w:sectPr'):
            raise ValueError('Word 含不支持的正文对象：' + element.tag.rsplit('}', 1)[-1])
    return {'type': 'doc', 'content': content}


def convert_file(store, name, data, template_id):
    """Preserve the upload, save its initial user version and queue Word export."""
    from .sources import upload
    from .templates import template
    from .export_jobs import enqueue_export
    from .store import dump

    if not isinstance(template_id, str) or not template_id:
        raise ValueError('请先选择模板')
    selected = template(store, template_id)
    if selected['status'] != 'ready':
        raise ValueError('所选模板尚未准备完成')
    name = Path(name).name
    suffix = Path(name).suffix.lower()
    if suffix not in ('.docx', '.md', '.markdown', '.txt'):
        raise ValueError('原文转换仅支持 DOCX、Markdown 和 TXT')
    source = upload(store, name, data)
    notes = ['仅将上传原文按模板排版；未改写、未调用模型、未进行事实核查。']
    sidecar = store.root / 'sources' / (source['id'] + '.provenance.json')
    provenance = json.loads(sidecar.read_text(encoding='utf-8'))
    try:
        if source['status'] != 'ready':
            raise ValueError(source.get('error') or '原文无法读取')
        if suffix == '.docx':
            document = _word_document(data, notes)
        else:
            text = _decode(data)
            document = _plain_document(text) if suffix == '.txt' else _markdown_document(text, notes)
        document = normalize_document(document)
        if not document_markdown(document).strip():
            raise ValueError('原文没有可转换的正文')
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        message = str(exc) or '原文结构无法可靠转换'
        provenance['template_conversion'] = {'status': 'unsupported', 'template_id': template_id, 'message': message}
        sidecar.write_text(dump(provenance), encoding='utf-8')
        store.event(None, 'template_conversion_rejected', {'source_id': source['id'], 'template_id': template_id, 'message': message})
        raise ConversionError(message, source['id']) from exc

    title = Path(name).stem[:200] or '导入文档'
    first = next((n for n in document.get('content', []) if n.get('content')), None)
    if first and first['type'] == 'heading':
        heading = ''.join(n.get('text', '') for n in first.get('content', [])).strip()
        if heading:
            title = heading[:200]
    run = store.create_run({'title': title, 'objective': '将用户上传原文按所选模板排版，保留正文内容。',
                            'template_id': template_id, 'allow_web': False, 'fact_check': False,
                            'writing_mode': 'general', 'workflow_id': 'general_report',
                            'workflow_variant': 'general'}, [source['id']], remember_requirements=False)
    notes = list(dict.fromkeys(notes))
    version = store.publish(run['id'], {'title': title, 'editor_document': document,
                            'research_notes': [{'kind': 'template_conversion', 'source_id': source['id'],
                                                'input_format': suffix.lstrip('.'), 'notes': notes}]}, author='user')
    provenance['template_conversion'] = {'status': 'saved', 'template_id': template_id,
                                          'run_id': run['id'], 'version_id': version['id']}
    sidecar.write_text(dump(provenance), encoding='utf-8')
    job = enqueue_export(store, version['id'], template_override=template_id)
    store.event(job['id'], 'template_file_imported', {'source_id': source['id'], 'version_id': version['id'], 'template_id': template_id})
    return {'version': version, 'job': job, 'notes': notes, 'source_id': source['id']}


def convert_request(store, name, data, template_id, request_id):
    """Persist import admission and its replay receipt in one transaction.

    This path calls only deterministic local operations, with no chat owner or
    model task. Rejected conversions commit the original and rejection receipt.
    """
    from .external_requests import _RequestStore
    from .store import Conflict, dump
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', request_id):
        raise ValueError('转换请求信息不完整，请刷新页面后重新选择原稿')
    fingerprint = hashlib.sha256(dump({'name': Path(name).name, 'template_id': template_id,
                                       'content': hashlib.sha256(data).hexdigest()}).encode()).hexdigest()
    key = 'template_convert:' + request_id
    error = None
    with store.tx() as connection:
        view = _RequestStore(store, connection)
        receipt = view.meta(key)
        if receipt:
            if receipt['fingerprint'] != fingerprint:
                raise Conflict('同一个转换请求不能更换原稿或模板，请重新选择')
            if receipt.get('error'):
                error = ConversionError(receipt['error'], receipt['source_id'])
            else:
                result = {'version': view.one('briefs', receipt['version_id']),
                          'job': view.one('jobs', receipt['job_id']),
                          'source_id': receipt['source_id'], 'notes': receipt['notes']}
        else:
            try:
                result = convert_file(view, name, data, template_id)
            except ConversionError as exc:
                error = exc
                receipt = {'error': exc.reason, 'source_id': exc.source_id}
            else:
                receipt = {'version_id': result['version']['id'], 'job_id': result['job']['id'],
                           'source_id': result['source_id'], 'notes': result['notes']}
            view.set_meta(key, {**receipt, 'fingerprint': fingerprint})
    if view.jobs_admitted:
        store.wake_jobs()
    if error:
        raise error
    return result
