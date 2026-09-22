"""Purpose-split read views of a review snapshot.

target.json freezes everything known about the version under review in one
file: the editor document, requirements twice, every binding with its source
excerpts, number bindings, structured data and company context. A Reviewer
needs one part at a time, and finding it in a single large JSON costs model
turns. These views are derived from the same frozen snapshot (target.json stays
the authority) and each answers one question:

- report.txt: the report as plain text, one block per line with its block id
- requirements.json: the compiled requirements and the user's original words
- claims.json: claim bindings, candidates, source statements, conflicts
- numbers.json: number bindings and structured report data
- citations.json: citation excerpts by source
- overview.json: what each packet file holds and how large it is
"""
import json

# Files derived from target.json; audit bundles treat them like its other copies.
DERIVED_VIEWS = ('report.txt', 'requirements.json', 'claims.json', 'numbers.json', 'citations.json', 'overview.json')
TABLE_ROW = ('tableRow',)
CELL = ('tableCell', 'tableHeader')


def _inline(node):
    kind = node.get('type')
    if kind == 'text':
        return node.get('text', '')
    if kind == 'citation':
        return f"[{node.get('attrs', {}).get('sourceId', '来源')}]"
    if kind == 'image':
        attrs = node.get('attrs', {})
        return f"[图：{attrs.get('caption') or attrs.get('alt') or attrs.get('src', '')}]"
    if kind == 'hardBreak':
        return ' '
    return ''.join(_inline(child) for child in node.get('content', []))


def report_text(document):
    """One line per block: [block_id] text. Headings keep their level; table
    rows are joined with ' | ' under the table's block id."""
    lines = []

    def walk(node, depth=0):
        kind = node.get('type')
        identity = node.get('attrs', {}).get('blockId')
        if kind == 'heading':
            level = node.get('attrs', {}).get('level', 1)
            lines.append(f"[{identity}] {'#' * int(level)} {_inline(node).strip()}")
            return
        if kind == 'paragraph':
            text = _inline(node).strip()
            if text:
                lines.append(f"[{identity}] {text}" if identity else text)
            return
        if kind == 'table':
            lines.append(f"[{identity}] 表格：")
            for row in node.get('content', []):
                cells = [_inline(cell).strip() for cell in row.get('content', []) if cell.get('type') in CELL]
                lines.append('  | ' + ' | '.join(cells) + ' |')
            return
        if kind == 'image':
            lines.append(f"[{identity}] {_inline(node)}")
            return
        for child in node.get('content', []):
            walk(child, depth + 1)

    walk(document)
    return '\n'.join(lines) + '\n'


def views(snapshot):
    """{file name: text} derived from one frozen snapshot."""
    detail = snapshot.get('detail') or {}
    requested = snapshot.get('requirements_input') or {}
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    out = {
        'report.txt': report_text(snapshot.get('document') or {'type': 'doc', 'content': []}),
        'requirements.json': dump({'requirements': snapshot.get('requirements'),
                                   'original_input': requested.get('raw_input'),
                                   'time_context': requested.get('time_context'),
                                   **({'length_stats':snapshot['length_stats']} if 'length_stats' in snapshot else {})}),
        'claims.json': dump({key: snapshot[key] for key in ('evidence', 'candidate_claims', 'source_statements',
                                                             'reconciliation', 'conflicts', 'fact_checks')
                             if key in snapshot}),
        'numbers.json': dump({'number_bindings': detail.get('number_bindings', []),
                              'report_data': detail.get('report_data')}),
        'citations.json': dump(detail.get('citations', [])),
    }
    return out


PURPOSE = {
    'report.txt': '被审正文的纯文本，每行一个段落，前面是段落 ID，[src_…] 为引用位置',
    'reader-preview.md': '产品阅读渲染后的同稿文本，包含短引用编号与自动来源表；不是实际 Word 排版验收',
    'requirements.json': '本轮要求（含读者约定与条款）、用户原话，以及新版核查包的已存正文确定性 length_stats',
    'claims.json': '主张与证据绑定、候选主张、来源陈述、冲突',
    'numbers.json': '数字绑定（正文原句与来源摘录）及结构化数据',
    'citations.json': '各引用对应的来源摘录与定位',
    'figure-texts.json': '各图生成脚本中的图上文字',
    'target.json': '完整冻结快照，是上面各视图的依据；需要其他字段时按 json_path 读取',
    'target-long-text.json': 'target.json 中超长字段的分块',
    'visual-inputs.json': '随消息发送的图片与包内文件的对应关系',
    'output.schema.json': '结果结构',
    'index.json': '文件清单与哈希',
}


GROUPS = {
    'sources/': '来源原文：<id>.txt 纯文本，<id>.view.json 按原始行分块，<id>.cells.txt 工作簿单元格；来源清单见 index.json 的 sources',
    'figures/': '报告图：每个 <figure_id>/ 下有图片、数据文件与生成脚本',
    'history/tools/': '历史执行中保存的工具输出，清单见 history/tools.json',
}


def overview(files, snapshot):
    """What each file holds and how large it is, so no turn is spent exploring.
    Top-level files are listed one by one; bulky folders are summarised."""
    listed = []
    grouped = {}
    for name in sorted(files):
        group = next((prefix for prefix in GROUPS if name.startswith(prefix)), None)
        if group:
            entry = grouped.setdefault(group, {'path': group, 'files': 0, 'bytes': 0, 'contains': GROUPS[group]})
            entry['files'] += 1
            entry['bytes'] += files[name]
            continue
        contains = PURPOSE.get(name) or ('本报告的历史版本、审阅、作者回应与执行记录' if name.startswith('history/') else '')
        listed.append({'path': name, 'bytes': files[name], 'contains': contains})
    sections = {key: len(json.dumps(value, ensure_ascii=False)) for key, value in snapshot.items()}
    return {'files': listed + list(grouped.values()),
            'target_json_sections_chars': dict(sorted(sections.items(), key=lambda kv: -kv[1]))}
