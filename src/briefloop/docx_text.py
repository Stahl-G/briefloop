"""Readable DOCX source text with explicit table coordinates, not a rich import."""


def _name(element):
    return element.tag.rsplit('}', 1)[-1]


def _blocks(parent, names):
    # Content controls may wrap blocks. Stop at each block so nested tables are
    # handled in their own cell, rather than collecting their paragraphs twice.
    pending = list(reversed(parent))
    while pending:
        element = pending.pop()
        if _name(element) in names:
            yield element
        else:
            pending.extend(reversed(element))


def _paragraph_text(paragraph):
    parts = []
    pending = [(paragraph, False)]
    while pending:
        element, tail = pending.pop()
        if tail:
            parts.append(element.tail or '')
            continue
        kind = _name(element)
        if kind in ('br', 'cr'): parts.append('\n')
        elif kind == 'tab': parts.append('\t')
        parts.append(element.text or '')
        for child in reversed(element):
            pending.extend(((child, True), (child, False)))
    return ''.join(parts)


def _property(element, group, name):
    properties = next((child for child in element if _name(child) == group), None)
    if properties is None: return None
    prop = next((child for child in properties if _name(child) == name), None)
    if prop is None: return None
    return next((value for key, value in prop.attrib.items() if key.rsplit('}', 1)[-1] == 'val'), '')


def _columns(element, group, name, default):
    value = _property(element, group, name)
    result = default if value is None else int(value)
    if result < (1 if name == 'gridSpan' else 0):
        raise ValueError('DOCX 表格列范围无效')
    return result


def document_text(document):
    """Keep cell text verbatim on its own lines and label its structural scope.

    Coordinates/merge markers describe extraction, not original prose or an
    inferred value. Existing saved source snapshots are never rewritten.
    """
    lines = []
    readable = False

    def paragraph(element, label=None):
        nonlocal readable
        value = _paragraph_text(element)
        readable = readable or bool(value.strip())
        if label: lines.append('[DOCX ' + label + ('' if value.strip() else ' empty') + ']')
        lines.append(value)

    def blocks(parent, path='', depth=0):
        table_index = paragraph_index = 0
        for element in _blocks(parent, {'p', 'tbl'}):
            if _name(element) == 'p':
                paragraph_index += 1
                paragraph(element, f'{path} paragraph {paragraph_index}' if path else None)
            else:
                table_index += 1
                table(element, f'{path}/table {table_index}' if path else f'table {table_index}', depth + 1)

    def table(element, path, depth):
        if depth > 50: raise ValueError('DOCX 表格嵌套过深')
        lines.append('[DOCX ' + path + ']')
        for row_index, row in enumerate(_blocks(element, {'tr'}), 1):
            before = _columns(row, 'trPr', 'gridBefore', 0)
            after = _columns(row, 'trPr', 'gridAfter', 0)
            lines.append(f'[DOCX {path} row {row_index} gridBefore={before} gridAfter={after}]')
            column = before + 1
            for cell_index, cell in enumerate(_blocks(row, {'tc'}), 1):
                span = _columns(cell, 'tcPr', 'gridSpan', 1)
                cell_path = f'{path}/row {row_index}/cell {cell_index}'
                merge = ''.join(f' {key}={value or "continue"}' for key in ('vMerge', 'hMerge')
                                if (value := _property(cell, 'tcPr', key)) is not None)
                lines.append(f'[DOCX {cell_path} columns={column}:{column + span - 1} gridSpan={span}{merge}]')
                start = len(lines)
                blocks(cell, cell_path, depth)
                if len(lines) == start: lines.append('[DOCX ' + cell_path + ' empty]')
                lines.append('[DOCX end ' + cell_path + ']')
                column += span
        lines.append('[DOCX end ' + path + ']')

    blocks(document)
    # Coordinates must not make an otherwise empty document a readable source.
    return '\n'.join(lines) if readable else ''
