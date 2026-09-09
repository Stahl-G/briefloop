"""Validated Tiptap document content. Markdown is a compatibility projection only."""
import copy
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs

SCHEMA_VERSION = 1
BLOCKS = {'paragraph', 'heading', 'bulletList', 'orderedList', 'listItem',
          'blockquote', 'codeBlock', 'horizontalRule', 'table', 'image'}
INLINE = {'text', 'hardBreak', 'citation'}
CHILDREN = {
    'doc': BLOCKS, 'paragraph': INLINE, 'heading': INLINE, 'blockquote': BLOCKS,
    'bulletList': {'listItem'}, 'orderedList': {'listItem'}, 'listItem': BLOCKS,
    'table': {'tableRow'}, 'tableRow': {'tableCell', 'tableHeader'},
    'tableCell': BLOCKS - {'table'}, 'tableHeader': BLOCKS - {'table'},
    'codeBlock': {'text'},
}
ATTRS = {
    'paragraph': {'textAlign', 'blockId'}, 'heading': {'level', 'textAlign', 'blockId'},
    'orderedList': {'start', 'type'}, 'codeBlock': {'language'},
    'tableCell': {'colspan', 'rowspan', 'colwidth', 'backgroundColor', 'textAlign', 'align'},
    'tableHeader': {'colspan', 'rowspan', 'colwidth', 'backgroundColor', 'textAlign', 'align'},
    'image': {'src', 'alt', 'title', 'width', 'height', 'caption'},
    'citation': {'sourceId'},
}
MARKS = {'bold': set(), 'italic': set(), 'strike': set(), 'underline': set(),
         'code': set(), 'textStyle': {'color'}, 'link': {'href', 'target', 'rel', 'class'}}


def table_layout(node):
    rows = node['content']; occupied = set(); cells = []; width = 0
    for r, row in enumerate(rows):
        col = 0
        for cell in row.get('content', []):
            while (r, col) in occupied: col += 1
            attrs = cell.get('attrs', {}); rs = attrs.get('rowspan', 1); cs = attrs.get('colspan', 1)
            if r + rs > len(rows) or cs > 100 or col + cs > 100: raise ValueError('表格合并范围无效')
            region = {(i, j) for i in range(r, r + rs) for j in range(col, col + cs)}
            if region & occupied: raise ValueError('表格合并范围重叠')
            occupied.update(region); cells.append((r, col, rs, cs, cell)); col += cs; width = max(width, col)
    if not width:raise ValueError('表格没有可用列')
    if any((r, c) not in occupied for r in range(len(rows)) for c in range(width)):
        raise ValueError('表格各行列数不一致')
    return len(rows), width, cells



def figure_src(value):
    if not isinstance(value, str):
        raise ValueError('图片必须引用已登记图表')
    if value.startswith('/api/figure?'):
        value = 'briefloop-figure:' + parse_qs(urlsplit(value).query).get('id', [''])[0]
    if not re.fullmatch(r'briefloop-figure:fig_[A-Za-z0-9_-]+', value):
        raise ValueError('请先登记图片/图表，再插入报告')
    return value


def normalize_document(value):
    """Reject unsupported structure rather than silently dropping rich formatting."""
    count = 0
    def attrs_for(kind, values):
        if not isinstance(values, dict): raise ValueError('文档属性必须是对象')
        allowed = ATTRS.get(kind, set())
        if {k for k,v in values.items() if v is not None} - allowed: raise ValueError('不支持的文档属性：' + kind)
        result = {k: copy.deepcopy(v) for k, v in values.items() if v is not None}
        if 'align' in result:result.setdefault('textAlign',result.pop('align'))
        for key, val in list(result.items()):
            if key in {'level', 'start', 'colspan', 'rowspan', 'width', 'height'}:
                if type(val) is not int or val < 1 or val > (6 if key == 'level' else 10000):
                    raise ValueError('无效文档尺寸或层级：' + key)
            elif key == 'colwidth':
                if not isinstance(val, list) or any(type(x) is not int or x < 0 or x > 10000 for x in val):
                    raise ValueError('表格列宽无效')
            elif key == 'textAlign':
                if val not in ('left', 'center', 'right', 'justify'): raise ValueError('无效对齐方式')
            elif key == 'backgroundColor':
                if not isinstance(val, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', val): raise ValueError('颜色使用六位 HEX')
                result[key] = val.lower()
            elif not isinstance(val, str): raise ValueError('文档文字属性必须是字符串')
        if kind == 'image': result['src'] = figure_src(result.get('src'))
        if kind == 'citation' and not re.fullmatch(r'src_[A-Za-z0-9_-]+', result.get('sourceId', '')):
            raise ValueError('无效引用来源 ID')
        # Normalize editor defaults so opening a document alone does not change its hash.
        for key in ('colspan', 'rowspan', 'start'):
            if result.get(key) == 1: result.pop(key)
        return result

    def walk(node, depth=0):
        nonlocal count
        count += 1
        if count > 20000 or depth > 50: raise ValueError('报告文档过大或嵌套过深')
        if not isinstance(node, dict) or set(node) - {'type', 'attrs', 'content', 'text', 'marks'}:
            raise ValueError('文档节点格式无效')
        kind = node.get('type')
        if kind not in CHILDREN and kind not in INLINE | {'image', 'horizontalRule'}:
            raise ValueError('不支持的文档节点：' + str(kind))
        out = {'type': kind}
        if kind == 'text':
            if not isinstance(node.get('text'), str) or not node['text']: raise ValueError('文本节点不能为空')
            out['text'] = node['text']
        elif 'text' in node: raise ValueError('只有文本节点可包含 text')
        attrs = attrs_for(kind, node.get('attrs', {}))
        if attrs: out['attrs'] = attrs
        marks = node.get('marks', [])
        if not isinstance(marks, list) or marks and kind != 'text': raise ValueError('文档标记无效')
        result_marks = []
        for mark in marks:
            if not isinstance(mark, dict) or set(mark) - {'type', 'attrs'} or mark.get('type') not in MARKS:
                raise ValueError('不支持的文字格式')
            mt = mark['type']; ma = mark.get('attrs') or {}
            if not isinstance(ma, dict) or set(ma) - MARKS[mt]: raise ValueError('不支持的文字格式属性')
            ma = {k: v for k, v in ma.items() if v is not None}
            if mt == 'textStyle':
                color = ma.get('color')
                if color is None: continue
                if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color): raise ValueError('颜色使用六位 HEX')
                ma = {'color': color.lower()}
            elif mt == 'link':
                href = ma.get('href', '')
                if not isinstance(href, str) or not (urlsplit(href).scheme.lower() in ('http', 'https', 'mailto') or re.fullmatch(r'#source-src_[A-Za-z0-9_-]+', href)):
                    raise ValueError('链接地址无效')
                ma = {'href': href}  # target/rel/class are presentation defaults.
            m = {'type': mt}
            if ma: m['attrs'] = ma
            if m not in result_marks: result_marks.append(m)
        if result_marks: out['marks'] = sorted(result_marks, key=lambda x: x['type'])
        children = node.get('content', [])
        if not isinstance(children, list): raise ValueError('文档内容必须是数组')
        if children and kind not in CHILDREN: raise ValueError('该节点不能包含子节点')
        normalized = [walk(child, depth + 1) for child in children]
        if any(child['type'] not in CHILDREN.get(kind, set()) for child in normalized):
            raise ValueError('文档层级无效：' + kind)
        if normalized: out['content'] = normalized
        if kind == 'table':
            if not normalized:raise ValueError('表格不能没有行')
            table_layout(out)
        if kind in ('tableCell','tableHeader','listItem') and not normalized:out['content']=[{'type':'paragraph'}]
        return out

    result = walk(value)
    if result['type'] != 'doc': raise ValueError('报告根节点必须为 doc')
    if len(json.dumps(result, ensure_ascii=False)) > 3_000_000: raise ValueError('报告文档过大')
    return result


def document_hash(document):
    value = json.dumps(normalize_document(document), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()


def markdown_document(markdown):
    """Import legacy Markdown once into the same structure the editor saves."""
    from markdown_it import MarkdownIt
    root = {'type': 'doc', 'content': []}; stack = [root]
    mapping = {'paragraph': 'paragraph', 'heading': 'heading', 'bullet_list': 'bulletList',
               'ordered_list': 'orderedList', 'list_item': 'listItem', 'blockquote': 'blockquote',
               'table': 'table', 'tr': 'tableRow', 'th': 'tableHeader', 'td': 'tableCell'}
    def add_text(text, marks):
        for part in re.split(r'(\[@src_[A-Za-z0-9_-]+\])', text):
            if not part: continue
            if re.fullmatch(r'\[@src_[A-Za-z0-9_-]+\]', part):
                node = {'type': 'citation', 'attrs': {'sourceId': part[2:-1]}}
            else: node = {'type': 'text', 'text': part, **({'marks': copy.deepcopy(marks)} if marks else {})}
            stack[-1].setdefault('content', []).append(node)
    for token in MarkdownIt('commonmark').enable('table').parse(markdown):
        name = token.type.rsplit('_', 1)[0]
        if token.type.endswith('_open') and name in mapping:
            node = {'type': mapping[name], 'content': []}
            if name == 'heading': node['attrs'] = {'level': int(token.tag[1:])}
            if name == 'ordered_list' and token.attrGet('start'): node['attrs'] = {'start': int(token.attrGet('start'))}
            stack[-1]['content'].append(node); stack.append(node)
        elif token.type.endswith('_close') and name in mapping: stack.pop()
        elif token.type == 'inline':
            cell_inline = stack[-1]['type'] in ('tableCell', 'tableHeader')
            if cell_inline:
                paragraph = {'type': 'paragraph', 'content': []}
                stack[-1]['content'].append(paragraph); stack.append(paragraph)
            marks = []
            for child in token.children or []:
                mt = {'strong_open': 'bold', 'em_open': 'italic', 's_open': 'strike'}.get(child.type)
                if mt: marks.append({'type': mt})
                elif child.type in ('strong_close', 'em_close', 's_close'):
                    if marks: marks.pop()
                elif child.type == 'link_open':
                    href = child.attrGet('href') or ''
                    if urlsplit(href).scheme.lower() in ('http', 'https', 'mailto') or href.startswith('#source-'):
                        marks.append({'type': 'link', 'attrs': {'href': href}})
                elif child.type == 'link_close': marks = [m for m in marks if m['type'] != 'link']
                elif child.type in ('text', 'code_inline'):
                    add_text(child.content, marks + ([{'type': 'code'}] if child.type == 'code_inline' else []))
                elif child.type in ('softbreak', 'hardbreak'): stack[-1]['content'].append({'type': 'hardBreak'})
                elif child.type == 'image':
                    src = child.attrGet('src') or ''
                    if src.startswith('briefloop-figure:'):
                        # Tiptap Image is a block node; split its surrounding paragraph.
                        parent = stack[-2]; current = stack[-1]
                        if not current.get('content'): parent['content'].remove(current)
                        parent['content'].append({'type': 'image', 'attrs': {'src': src, 'alt': child.content, 'title': child.attrGet('title')}})
                        following = {'type': 'paragraph', 'content': []}; parent['content'].append(following); stack[-1] = following
                    else: add_text(child.content, marks)
            if cell_inline: stack.pop()
        elif token.type in ('fence', 'code_block'):
            stack[-1]['content'].append({'type': 'codeBlock', 'attrs': {'language': token.info or None},
                'content': [{'type': 'text', 'text': token.content.rstrip('\n')}] if token.content.rstrip('\n') else []})
        elif token.type == 'hr': stack[-1]['content'].append({'type': 'horizontalRule'})
    return normalize_document(root)


def document_markdown(document):
    """Readable compatibility export. Rich styles remain in the document itself."""
    def render(node):
        kind = node['type']; attrs = node.get('attrs', {}); children = node.get('content', [])
        if kind == 'text':
            text = re.sub(r'([\\`*_\[\]<>])', r'\\\1', node['text'])
            for mark in node.get('marks', []):
                if mark['type'] == 'bold': text = '**' + text + '**'
                elif mark['type'] == 'italic': text = '*' + text + '*'
                elif mark['type'] == 'code': text = '`' + node['text'].replace('`', '\\`') + '`'
                elif mark['type'] == 'link':
                    href = mark['attrs']['href']
                    text = '[@' + href[8:] + ']' if href.startswith('#source-') else '[' + text + '](' + href + ')'
            return text
        if kind == 'citation': return '[@' + attrs['sourceId'] + ']'
        if kind == 'hardBreak': return '\n'
        if kind == 'image':
            alt = (attrs.get('alt') or '').replace('[', '\\[').replace(']', '\\]')
            return f'![{alt}]({attrs["src"]})' + ('\n\n' + attrs['caption'] if attrs.get('caption') else '')
        if kind in ('paragraph', 'heading'):
            return ('#' * attrs.get('level', 2) + ' ' if kind == 'heading' else '') + ''.join(render(c) for c in children)
        if kind == 'codeBlock': return '```' + attrs.get('language', '') + '\n' + ''.join(c.get('text', '') for c in children) + '\n```'
        if kind == 'horizontalRule': return '---'
        if kind == 'blockquote': return '\n'.join('> ' + line for line in '\n\n'.join(render(c) for c in children).splitlines())
        if kind in ('bulletList', 'orderedList'):
            return '\n'.join((f'{i}. ' if kind == 'orderedList' else '- ') + render(c).replace('\n', '\n  ') for i, c in enumerate(children, attrs.get('start', 1)))
        if kind == 'table':
            nr,width,placements=table_layout(node)
            rows=[['']*width for _ in range(nr)]
            for r,c,rs,cs,cell in placements:
                text=render(cell).replace('|','\\|').replace('\n','<br>')
                for row_index in range(r,r+rs):rows[row_index][c]=text
            lines=[]
            for index,cells in enumerate(rows):
                lines.append('| '+' | '.join(cells)+' |')
                if index==0:lines.append('| '+' | '.join(['---']*width)+' |')
            return '\n'.join(lines)
        return '\n\n'.join(render(c) for c in children)
    return render(normalize_document(document)).strip()


def source_ids(document):
    found = []
    def walk(node):
        if node['type'] == 'citation': found.append(node['attrs']['sourceId'])
        for mark in node.get('marks', []):
            href = mark.get('attrs', {}).get('href', '')
            if href.startswith('#source-'): found.append(href[8:])
        for child in node.get('content', []): walk(child)
    walk(normalize_document(document))
    return list(dict.fromkeys(found))


def brief_document(brief):
    raw = brief.get('editor_document')
    return normalize_document(json.loads(raw) if isinstance(raw, str) else raw) if raw else markdown_document(brief['markdown'])
