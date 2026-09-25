"""Version-scoped evidence checks, never a claim of whole-report correctness.

Drafts remain readable/saveable. Numbers are compared only at explicitly bound
body and source spans; unbound/unsupported data is not a successful check.
"""
import json
import re
from decimal import Decimal, InvalidOperation

REF_RE = re.compile(r'\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]')
ESCAPED_BOLD_RE = re.compile(r'\\\*\\\*')
_SCALES = {'': 1, 'thousand': 1000, 'million': 1000000, 'billion': 1000000000, 'trillion': 1000000000000,
           '万': 10000, '百万': 1000000, '千万': 10000000, '亿': 100000000, '十亿': 1000000000, '万亿': 1000000000000}
_CAPACITY = {'w': 1, '瓦': 1, 'kw': 1000, '千瓦': 1000, 'mw': 1000000,
             '兆瓦': 1000000, 'gw': 1000000000, '吉瓦': 1000000000}


def normalized(value, unit):
    """Exact supported units only. Currency and dimension never disappear."""
    try:
        number = Decimal(str(value).replace(',', '').replace('−', '-'))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    unit = re.sub(r'\s+', ' ', unit.strip().lower())
    if unit in ('%', '％', 'percent', '百分之'):
        return number, 'percent'
    # Percentage POINTS are a change in a percent-valued metric, not a ratio:
    # a matched 百分点 must not be interchangeable with a percent figure.
    if unit in ('百分点', '个百分点', 'percentage point', 'percentage points', 'pp'):
        return number, 'percentage_point'
    if unit in _CAPACITY:
        return number * _CAPACITY[unit], 'power'
    if unit in ('股', 'shares'):
        return number, 'shares'
    # Bare scalars — years, multipliers, counts — carry conclusions too
    # (OfficeQA evidence: years/ratios escaped both the binding trigger and
    # the checker).  Supported so a broadened binding actually checks
    # instead of piling into skipped.
    if unit in ('年', 'year', 'fy'):
        return number, 'year'
    if unit in ('倍', 'x', '×'):
        return number, 'ratio'
    if unit in ('个', '项', '次', '人', '家', '条', 'count', 'items'):
        return number, 'count'
    if unit == '':
        return number, 'scalar'
    if unit in ('$', 'usd', '美元'):
        return number, 'USD'
    if unit in ('cny', 'rmb', '人民币', '元', '元人民币'):
        return number, 'CNY'
    money = re.fullmatch(r'(thousand|millions?|billions?|trillions?) (usd|cny|rmb)', unit)
    if not money:
        reverse = re.fullmatch(r'(usd|cny|rmb) (thousand|millions?|billions?|trillions?)', unit)
        if reverse:
            money = re.fullmatch(r'(\w+) (\w+)', reverse[2] + ' ' + reverse[1])
    if money:
        return number * _SCALES[money[1].rstrip('s')], 'USD' if money[2] == 'usd' else 'CNY'
    chinese = re.fullmatch(r'(万亿|十亿|千万|百万|亿|万)(美元|元人民币|人民币|元)', unit)
    if chinese:
        return number * _SCALES[chinese[1]], 'USD' if chinese[2] == '美元' else 'CNY'
    return None


# Full numeric tokens, including sign, thousands separators and scientific form.
# Prefix/suffix currency conflicts or compound dimensions remain unsupported.
_NUM = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+\-]?\d+)?'
_UNIT = (r'(?:thousand|millions?|billions?|trillions?)(?:\s+(?:USD|CNY|RMB|EUR|GBP))?'
         r'|(?:USD|CNY|RMB|EUR|GBP)(?:\s+(?:thousand|millions?|billions?|trillions?))?'
         r'|(?:万亿|十亿|千万|百万|亿|万)?(?:美元|元人民币|人民币|元)'
         r'|百分点|百分之|percentage points?|pp|percent|％|%|GW|MW|kW|W|吉瓦|兆瓦|千瓦|瓦|shares|股'
         r'|年|倍|个百分点|个(?!百分点|月)|项|次|人|家|条')
_QUANTITY = re.compile(r'(?<![A-Za-z0-9_.,+\-−])(?P<prefix>US\$|\$|USD\s+|CNY\s+|RMB\s+|百分之)?'
                       r'(?P<number>' + _NUM + r')\s*(?P<unit>' + _UNIT + r')?'
                       r'(?P<denom>\s*/\s*[\w]+|每[\w]+)?', re.I)


def quantities(text):
    # Chinese prose adjacent to numbers is normal; only Latin/digit boundaries
    # delimit numeric tokens. Preserve offsets into the original text.
    for match in _QUANTITY.finditer(text):
        unit = (match['unit'] or '').strip()
        prefix = (match['prefix'] or '').strip()
        if match['denom']:
            continue
        # Do not accept a supported prefix of an unknown unit (e.g. MWh).
        end = match.end()
        if end < len(text) and (text[end].isascii() and (text[end].isalnum() or text[end] == '_')):
            continue
        if prefix:
            currency = 'USD' if prefix in ('$', 'US$') else prefix
            if unit.lower() in ('thousand', 'million', 'millions', 'billion', 'billions', 'trillion', 'trillions'):
                unit += ' ' + currency
            elif not unit:
                unit = currency
            else:
                continue
        result = normalized(match['number'], unit)
        if result is not None:
            yield match.start(), match.end(), result


def body_refs(markdown):
    return list(dict.fromkeys(match.replace('\\', '') for match in REF_RE.findall(markdown or '')))


def check_refs(store, markdown):
    broken = []
    for sid in body_refs(markdown):
        try:
            store.one('sources', sid)
        except ValueError:
            broken.append(sid)
    return broken


def _number_locator(value):
    """Resolve explicit locations; never infer a location from an excerpt.

    NumberBinding keeps its legacy string field. JSON uses the shared evidence
    locator schema (including workbook cells); simple line/page forms remain
    convenient for text and PDF sources. Unrecognized prose stays unchecked.
    """
    from .evidence import Locator
    if not isinstance(value, str):
        raise ValueError('数值来源定位必须是明确的行、页或 JSON 定位')
    value = value.strip()
    if value.startswith('{'):
        return Locator.model_validate(json.loads(value))
    lines = re.fullmatch(r'(?:lines?\s*|L)(\d+)(?:\s*[-–]\s*(?:L)?(\d+))?', value, re.I)
    if not lines:
        lines = re.fullmatch(r'第?\s*(\d+)(?:\s*[-–至]\s*(\d+))?\s*行', value)
    if lines:
        return Locator(kind='text', start_line=int(lines[1]), end_line=int(lines[2] or lines[1]))
    page = re.fullmatch(r'(?:page\s*|p\.?\s*)(\d+)', value, re.I)
    if not page:
        page = re.fullmatch(r'第?\s*(\d+)\s*页', value)
    if page:
        return Locator(kind='pdf', page=int(page[1]))
    raise ValueError('数值来源定位无法解析；请使用 line 1、page 1 或证据定位 JSON')


def check_numbers(markdown, bindings, store=None, allowed_sources=None):
    """Compare one exact body token to its original value and source excerpt.

    A matched excerpt is provenance, not proof of the entity/period/metric's
    meaning. The independent Evaluator still checks those semantic relations.
    """
    results = []
    for item in bindings or []:
        item = item if isinstance(item, dict) else {}
        row = {'label': item.get('label', ''), 'checked': False, 'found': False,
               'expected': f"{item.get('value')} {item.get('unit', '')}", 'reason': ''}
        results.append(row)
        expected = normalized(item.get('value'), item.get('unit', ''))
        if expected is None:
            row['reason'] = '单位或数值不支持，未检查'
            continue
        # A dimensionless scalar carries no unit specificity: any identical
        # bare number in the excerpt (a page number, an unrelated year)
        # satisfies the match.  Push the specificity into semantic fields.
        if expected[1] == 'scalar':
            row['dimensionless'] = True
            if not (item.get('label') and (item.get('entity') or item.get('period'))):
                row['reason'] = '裸数值绑定缺少 label 与 entity/period，特异性不足，未检查'
                continue
        quote, token = item.get('report_quote', ''), item.get('number_text', '')
        if not quote or not token or markdown.count(quote) != 1 or quote.count(token) != 1:
            row['reason'] = '正文定位缺失、重复或已改动，需重新绑定'
            continue
        if not item.get('source_id') or not item.get('locator') or not item.get('source_excerpt') or store is None:
            row['reason'] = '缺少可核对的来源定位与原文摘录'
            continue
        sid = item['source_id']
        if allowed_sources is not None and sid not in allowed_sources:
            row['reason'] = '绑定来源不属于本轮事实材料'
            continue
        try:
            source = store.one('sources', sid)
            if source['status'] != 'ready':
                raise ValueError('source not ready')
            from .evidence import EvidenceInput, _read_location
            _, location = _read_location(store, EvidenceInput(
                source_id=sid, locator=_number_locator(item['locator']), excerpt=item['source_excerpt']))
        except (ValueError, OSError):
            row['reason'] = '来源定位无法读取，或摘录不在指定位置，未检查'
            continue
        if location['location_status'] != 'located':
            row['reason'] = '来源指定位置需视觉核对或缺少计算缓存，未检查'
            continue
        excerpt = item['source_excerpt']
        if expected not in [q[2] for q in quantities(excerpt)]:
            row['reason'] = '原始数值未在指定位置的摘录中定位'
            continue
        # Require the selected token to be the complete number+unit, not a
        # substring of a larger number, and compare only this occurrence.
        start = quote.index(token)
        candidates = [value for a, b, value in quantities(quote)
                      if a == start and quote[a:b].strip() == token.strip()]
        if not candidates:
            row['reason'] = '正文数值不是完整的受支持数值与单位，未检查'
            continue
        row['checked'] = True
        row['found'] = candidates[0] == expected
        if row['found'] and row.get('dimensionless'):
            row['reason'] = '裸数值匹配（无量纲，仅证数值存在），归属与含义仍待评价'
        else:
            row['reason'] = '绑定数值匹配，含义仍待评价' if row['found'] else '绑定正文数值与原始值不一致'
    return results


_NUMERIC_OCCURRENCE_SAMPLE_LIMIT = 12
_VISIBLE_URL = re.compile(r'(?i)(?:https?://|www\.)[^\s<>]+')


def numeric_occurrence_review(document, bindings, results):
    """Locate explicit-unit numbers not uniquely covered by a checked binding.

    This is a writing aid, not a rate of factual correctness. Each repeated
    appearance is a separate occurrence; unrecognised units and table values
    whose unit exists only in a header deliberately remain outside its scope.
    """
    from .document_model import document_markdown, markdown_document, table_layout

    def visible_text(node):
        kind = node.get('type')
        if kind in ('codeBlock', 'citation', 'image', 'tableHeader'):
            return '\ufffc'
        if kind == 'text':
            if any(mark.get('type') == 'code' for mark in node.get('marks', [])):
                return '\ufffc'
            value = node.get('text', '')
            # A visible URL is a locator, even when a unit-like suffix occurs.
            return _VISIBLE_URL.sub('\ufffc', value)
        if kind == 'hardBreak':
            return '\ufffc'
        children = [visible_text(child) for child in node.get('content', [])]
        return ('\ufffc' if kind in ('tableCell', 'blockquote', 'listItem') else '').join(children)

    candidates = []
    paragraph_index = 0
    table_index = 0
    raw_blocks = {}

    def raw_scanned_block(node):
        """Render only eligible content, retaining its original Markdown marks.

        The real quote may be in a heading, code node or table header that the
        visible scanner skips. A matching plain number elsewhere must not
        inherit that binding just because formatting projects to the same text.
        """
        identity = id(node)
        if identity not in raw_blocks:
            def without_excluded(item):
                kind = item.get('type')
                if kind in ('codeBlock', 'image'):
                    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': '\ufffc'}]}
                if kind in ('citation', 'hardBreak') or (
                        kind == 'text' and any(mark.get('type') == 'code' for mark in item.get('marks', []))):
                    return {'type': 'text', 'text': '\ufffc'}
                copy = dict(item)
                if kind == 'text':
                    copy['text'] = _VISIBLE_URL.sub('\ufffc', item.get('text', ''))
                elif 'content' in item:
                    copy['content'] = [without_excluded(child) for child in item['content']]
                return copy

            blocks = node.get('content', []) if node.get('type') == 'tableCell' else [node]
            try:
                raw_blocks[identity] = document_markdown({'type': 'doc',
                    'content': [without_excluded(block) for block in blocks]})
            except ValueError:
                raw_blocks[identity] = ''
        return raw_blocks[identity]

    def scan(text, location, node):
        for start, end, (_, dimension) in quantities(text):
            if dimension in ('scalar', 'year'):
                continue
            # Ordinal labels such as 第1次 or 第2项 are not factual quantities.
            if re.search(r'第\s*$', text[max(0, start - 4):start]):
                continue
            context = text[max(0, start - 30):min(len(text), end + 30)].replace('\ufffc', ' ').strip()
            candidates.append({'text': text[start:end], 'context': context,
                               'start': start, 'end': end, 'body': text, 'node': node, **location})

    def walk(node):
        nonlocal paragraph_index, table_index
        kind = node.get('type')
        if kind in ('codeBlock', 'heading', 'tableHeader'):
            return
        if kind == 'table':
            table_index += 1
            for row, column, _, _, cell in table_layout(node)[2]:
                if cell.get('type') != 'tableCell':
                    continue
                location = {'kind': 'table_cell', 'table': table_index,
                            'row': row + 1, 'column': column + 1}
                block_id = (cell.get('attrs') or {}).get('blockId')
                if block_id:
                    location['block_id'] = block_id
                scan(visible_text(cell), location, cell)
            return
        if kind == 'paragraph':
            paragraph_index += 1
            location = {'kind': 'paragraph', 'paragraph': paragraph_index}
            block_id = (node.get('attrs') or {}).get('blockId')
            if block_id:
                location['block_id'] = block_id
            scan(visible_text(node), location, node)
            return
        for child in node.get('content', []):
            walk(child)

    walk(document)
    covered = set()
    for binding, result in zip(bindings or [], results):
        if not result.get('checked') or not result.get('found') or not isinstance(binding, dict):
            continue
        quote, token = binding.get('report_quote', ''), binding.get('number_text', '')
        if not quote or not token or quote.count(token) != 1:
            continue
        # check_numbers has already proven the original Markdown quote is
        # unique. The occurrence scan reads visible rich-text nodes instead:
        # **1200万元** and [1200万元](url) both display as 1200万元. Project the
        # quote through the same Markdown importer without relaxing uniqueness
        # of the visible position. Ambiguous projections remain review hints.
        forms = [quote]
        if any(marker in quote for marker in ('*', '_', '[', '`', '~', '\\', '>')):
            try:
                projected = visible_text(markdown_document(quote))
                if projected and projected not in forms and '\ufffc' not in projected:
                    forms.append(projected)
            except ValueError:
                pass
        matching = set()
        for form in forms:
            if form.count(token) != 1:
                continue
            for index, candidate in enumerate(candidates):
                if candidate['text'] != token or raw_scanned_block(candidate['node']).count(quote) != 1:
                    continue
                body = candidate['body']
                if body.count(form) != 1:
                    continue
                start = body.index(form) + form.index(token)
                if candidate['start'] == start:
                    matching.add(index)
        if len(matching) == 1:
            covered.update(matching)

    remaining = [candidate for index, candidate in enumerate(candidates) if index not in covered]
    samples = [{key: value for key, value in candidate.items() if key not in ('body', 'node', 'start', 'end')}
               for candidate in remaining[:_NUMERIC_OCCURRENCE_SAMPLE_LIMIT]]
    return {'candidate_count': len(candidates), 'checked_occurrences': len(covered),
            'review_candidate_count': len(remaining), 'samples': samples,
            'sample_limit': _NUMERIC_OCCURRENCE_SAMPLE_LIMIT,
            'truncated': len(remaining) > len(samples),
            'scope': '仅定位正文及数据单元格中可识别的带明确单位数值；每次出现单独计数。跳过代码、标题、网址、引用标识、独立年份与序号。单位只在表头、复杂单位或富文本跨段时可能漏检；待看候选不等于错误，已匹配也不证明事实含义或来源支持。'}


def check_export(markdown):
    return {'escaped_bold': bool(ESCAPED_BOLD_RE.search(markdown or '')),
            'figure_markers': sorted(set(re.findall(r'briefloop-figure:([A-Za-z0-9_-]+)', markdown or '')))}


def _node_text(node):
    def walk(item):
        if not isinstance(item, dict):return ''
        if item.get('type') == 'text':return item.get('text', '')
        return ''.join(walk(child) for child in item.get('content', []))
    return ''.join(walk(child) for child in node.get('content', []))


def check_layout(document):
    """Deterministic layout findings from the saved document, report-only.

    Heading hierarchy, empty headings and header-less tables are quality
    findings, not correctness claims; nothing here blocks delivery.
    """
    content = (document or {}).get('content', [])
    jumps = []; empty = 0; headerless = []
    previous = None
    for node in content:
        kind = node.get('type')
        if kind == 'heading':
            level = (node.get('attrs') or {}).get('level')
            text = _node_text(node).strip()
            if not text:
                empty += 1
            if isinstance(level, int):
                if previous is not None and level > previous + 1:
                    jumps.append({'after': previous, 'level': level, 'text': text[:40]})
                previous = level
        elif kind == 'table':
            rows = node.get('content') or []
            first = rows[0].get('content') if rows else []
            if first and not all(cell.get('type') == 'tableHeader' for cell in first):
                headerless.append(_node_text(first[0])[:20] if first else '')
    issues = bool(jumps or empty or headerless)
    return {'heading_jumps': jumps, 'empty_headings': empty,
            'tables_without_header': headerless,
            'status': 'issues' if issues else 'ok'}


def brief_checks(store, version_id):
    brief = store.one('briefs', version_id)
    detail = json.loads(brief.get('detail') or '{}')
    run = store.one('runs', brief['run_id'])
    references = set(json.loads(run['requirements']).get('reference_source_ids', []))
    allowed = set(store.source_ids(run['id'])) - references
    numbers = check_numbers(brief['markdown'], detail.get('number_bindings'), store, allowed)
    from .document_model import brief_document
    document = brief_document(brief)
    layout = check_layout(document)
    occurrence_review = numeric_occurrence_review(document, detail.get('number_bindings'), numbers)
    rows = store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1', (version_id,))
    checked = sum(r['checked'] for r in numbers)
    export = check_export(brief['markdown'])
    # Figure registration is checked separately from portable file delivery.
    from .figure_support import validate_figures
    try:
        validate_figures(store, run['id'], brief['markdown'])
        export['figure_error'] = None
    except (ValueError, OSError) as exc:
        export['figure_error'] = str(exc)
    records = detail.get('gap_records') or []
    legacy = detail.get('gaps') or []
    if records:
        open_records = [r for r in records if r.get('status') != 'resolved']
        total = len(records)
    else:
        # Older drafts only filled the free-text list; keep those gaps visible.
        open_records = [{'impact': str(text), 'status': 'open', 'legacy': True} for text in legacy]
        total = len(legacy)
    from .report_time import check as check_time
    temporal = check_time(json.loads(run['requirements']).get('time_context'), detail.get('temporal_claims', []))
    return {'temporal': temporal, 'version_id': version_id,
            'broken_refs': check_refs(store, brief['markdown']),
            'gaps': {'total': total, 'open': len(open_records), 'open_records': open_records,
                     'legacy': bool(legacy and not records)},
            'numbers': {'total': len(numbers), 'checked': checked,
                        'matched': sum(r['found'] for r in numbers),
                        'status': 'not_checked' if not checked else 'partial' if checked < len(numbers) else 'checked_bindings',
                        'unmatched': [r for r in numbers if r['checked'] and not r['found']],
                        'skipped': [r for r in numbers if not r['checked']],
                        # Numeric tokens the body actually carries: with zero
                        # bindings this separates "no numbers to check" from
                        # "numbers present, none ever bound" — the silent-
                        # inactive case.  A binary presence signal only: dates
                        # and line references also count, so it is NOT a
                        # matched-rate denominator.
                        'body_quantity_count': len(list(quantities(brief['markdown']))),
                        'occurrence_review': occurrence_review},
            'export': export,
            'layout': layout,
            'assessment_overall': json.loads(rows[0]['data']).get('overall') if rows else None}
