"""Read-only writer feedback; never a score or a draft publication gate."""
import re
from .models import BriefDraft
from .length import count_brief, length_stats


_TABLE_CITATION_SAMPLE_LIMIT = 12
# Room is not a quota: a generous soft length needs cheap signals for padding.
_SENTENCE = re.compile(r'[^。！？；!?\n]+')
_HEDGE = re.compile(r'不等于|不代表|不能据此|不能证明|不足以证明|尚不能|尚未证明|不构成|不自动|仍需核对|有待核实|未经审计|无法确认|不能当作|不能视为')
_PROCESS = re.compile(r'本次检索|本轮检索|检索结果|搜索结果|搜索摘要|抓取|提交时可得|本次未发现|未发现当日|研究轮次|任务包|Scout')


def _padding_notes(markdown):
    """Repetition, stacked caveats and process narration; never a length rule."""
    text = re.sub(r'\[@[^\]\n]+\]', '', markdown)
    sentences = [s.strip(' #>*-|') for s in _SENTENCE.findall(text)]
    sentences = [s for s in sentences if len(re.sub(r'\W', '', s)) >= 16]
    grams = [{s[i:i+3] for i in range(len(s)-2)} for s in sentences]
    repeated = []
    for i in range(len(sentences)):
        for j in range(i+1, len(sentences)):
            union = grams[i] | grams[j]
            if union and len(grams[i] & grams[j]) / len(union) >= 0.55:
                repeated.append([sentences[i][:60], sentences[j][:60]])
    notes = []
    if repeated:
        notes.append({'code': 'repeated_statements', 'kind': 'advisory', 'count': len(repeated), 'samples': repeated[:5],
                      'message': '这些句子与前文几乎重复；保留信息最完整的一处，删除或合并其余，不要换说法再讲一遍。'})
    units = max(1, count_brief(markdown))
    hedges = [s[:60] for s in sentences if _HEDGE.search(s)]
    if len(hedges) >= 8 and len(hedges) * 1000 / units > 4:
        notes.append({'code': 'stacked_caveats', 'kind': 'advisory', 'count': len(hedges),
                      'per_thousand_units': round(len(hedges) * 1000 / units, 1), 'samples': hedges[:5],
                      'message': '限定句密度偏高。每个限制只在受影响的结论旁写一次，或集中到证据边界；不在每段重复同类免责，必要条件仍须保留。'})
    process = [s[:60] for s in sentences if _PROCESS.search(s)]
    if process:
        notes.append({'code': 'process_narration', 'kind': 'advisory', 'count': len(process), 'samples': process[:5],
                      'message': '正文在叙述检索或任务过程；读者只需要会影响判断的限制，过程记录放 research_notes。'})
    return notes


def _table_citation_notes(draft):
    """Describe absent cell-local markers, without inferring evidence support."""
    from .delivery_checks import quantities
    from .document_model import markdown_document, table_layout
    try:
        document = draft.editor_document or markdown_document(draft.markdown)
    except ValueError:
        # A new advisory must not reject legacy Markdown that previously saved.
        return []

    def tables(node):
        if node['type'] == 'table':
            yield node
        for child in node.get('content', []):
            yield from tables(child)

    def cell_content(node):
        kind = node['type']
        marks = node.get('marks', [])
        if kind in ('codeBlock', 'table') or any(m['type'] == 'code' for m in marks):
            # A separator prevents numeric text on either side of code joining.
            return '\ufffc', False
        cited = kind == 'citation' or any(
            m['type'] == 'link' and m.get('attrs', {}).get('href', '').startswith('#source-')
            for m in marks)
        text = node.get('text', '')
        for child in node.get('content', []):
            part, has_source = cell_content(child)
            text += part
            cited = cited or has_source
        if kind in ('paragraph', 'hardBreak'):
            text += '\n'
        return text, cited

    count, samples = 0, []
    for table_index, table in enumerate(tables(document), 1):
        # Placements enumerate each physical cell once, including merged cells.
        for row, column, rowspan, colspan, cell in table_layout(table)[2]:
            if cell['type'] != 'tableCell':
                continue
            text, cited = cell_content(cell)
            if cited or not any(value[1] not in ('scalar', 'year') for _, _, value in quantities(text)):
                continue
            count += 1
            if len(samples) < _TABLE_CITATION_SAMPLE_LIMIT:
                sample = {'table': table_index, 'row': row + 1, 'column': column + 1,
                          'rowspan': rowspan, 'colspan': colspan,
                          'text': ' '.join(text.replace('\ufffc', ' ').split())[:120]}
                if cell.get('attrs', {}).get('blockId'):
                    sample['block_id'] = cell['attrs']['blockId']
                samples.append(sample)
    if not count:
        return []
    return [{'code': 'table_numeric_cell_citation_missing', 'kind': 'advisory',
             'count': count, 'samples': samples, 'sample_limit': _TABLE_CITATION_SAMPLE_LIMIT,
             'truncated': count > len(samples),
             'message': '部分数值单元格没有单元格来源标记；若转述来源事实，请在相应单元格补引用；若为计算、计划或假设，请说明依据。不要自动复制表前引用或为消除提示编造来源。',
             'scope': '仅提醒单元格内工具可识别的带明确单位数值；跳过表头、代码、年份和裸数，单位只在表头时可能漏检。行列从1开始，合并单元格按一个物理单元格计数。标记缺失不等于无依据，有标记也不证明数字或引用支持正确。'}]


def inspect_draft(value, requirements=None, *, store=None, allowed_sources=None):
    from .delivery_checks import check_numbers, numeric_occurrence_review, quantities
    from .document_model import markdown_document
    from .report_time import check as check_time
    draft = BriefDraft.model_validate(value)
    req = requirements or {}
    length = length_stats(draft.markdown, target_words=req.get('target_words'), max_words=req.get('max_words'),
                          length_mode=req.get('length_mode', 'soft'), length_requirement=req.get('length_requirement'))
    length['below_target'] = bool(length['target_words'] and length['count'] < length['target_words'])
    # Count only H2 chapters, keeping nested headings inside their parent.
    sections = []
    for part in re.split(r'(?m)^## ', draft.markdown)[1:]:
        title, _, body = part.partition('\n')
        sections.append({'title': title.strip(), 'body_units': count_brief(body)})
    cited = set(re.findall(r'\[@([^\]\n]+)\]', draft.markdown))
    located = {c.source_id for c in draft.citations if c.locator.strip()}
    bindings = [b.model_dump() for b in draft.number_bindings]
    numbers = check_numbers(draft.markdown, bindings,
                            store=store, allowed_sources=allowed_sources)
    checked = sum(n['checked'] for n in numbers)
    quantity_count = len(list(quantities(draft.markdown)))
    try:
        occurrence_review = numeric_occurrence_review(
            draft.editor_document or markdown_document(draft.markdown), bindings, numbers)
    except ValueError:
        # A new advisory cannot make an otherwise saveable legacy draft fail.
        occurrence_review = None
    warnings = []
    notes = []
    temporal = check_time(req.get('time_context'), [claim.model_dump() for claim in draft.temporal_claims])
    notes.extend({**warning, 'kind': 'advisory'} for warning in temporal.get('warnings', []))
    if temporal.get('missing_date_count') or temporal.get('out_of_range_count'):
        notes.append({'code': 'temporal_basis_to_review', 'kind': 'needs_semantic_review',
                      'message': '部分当期动态的依据日期在范围外，或缺少日期/新闻性说明/来源定位；回读原文后按发生、首次披露或新进展登记。不要改写真实事件日期，不能仅用发布或抓取时间补齐。',
                      'missing_basis_count': temporal.get('missing_date_count', 0),
                      'out_of_range_count': temporal.get('out_of_range_count', 0)})
    if length['over_limit']:
        if length['strict_exceeded']:
            warnings.append({'code': 'over_limit', 'kind': 'explicit_requirement',
                             'over_by': length['over_by'], 'requirement': length['length_requirement'],
                             'message': f"正文超出明确严格上限 {length['over_by']} 个计数单位；请核对原要求并精简重复。保留必要条件和引用；工作稿仍可提交、编辑与下载。"})
        else:
            notes.append({'code': 'over_limit', 'kind': 'advisory', 'over_by': length['over_by'],
                          'message': f"正文超出建议范围 {length['over_by']} 个计数单位；此项为篇幅建议，不阻断草稿提交。不必仅为消除提示改稿；有必要的事实、引用和采用条件应保留。"})
    if length['below_target']:
        notes.append({'code': 'below_target', 'kind': 'advisory',
                      'message': '目标字数是偏好；核对明确范围与必答内容，不能仅因低于目标就扩写。'})
    if cited - located:
        warnings.append({'code': 'citation_location_missing', 'source_ids': sorted(cited - located),
                         'message': '正文引用缺少对应定位记录；有来源ID不代表该句得到原文支持。'})
    if quantity_count and not numbers:
        warnings.append({'code': 'number_bindings_missing', 'message': '正文含数值但没有数字绑定，尚无数值定位检查。'})
    if any(n['checked'] and not n['found'] for n in numbers):
        warnings.append({'code': 'number_mismatch', 'message': '存在绑定数值不匹配，请回到对应原文修正。'})
    if any(not n['checked'] for n in numbers):
        warnings.append({'code': 'number_unchecked', 'kind': 'needs_semantic_review',
                         'message': '部分绑定缺定位或工具无法检查。缺定位可补；不支持的单位保留原状交审阅，不删单位或反复改数值以消除提示。'})
    if occurrence_review and occurrence_review['review_candidate_count']:
        notes.append({'code': 'numeric_occurrences_to_review', 'kind': 'advisory',
                      'count': occurrence_review['review_candidate_count'],
                      'samples': occurrence_review['samples'],
                      'message': '正文中还有带明确单位的数值出现位置，未找到唯一对应的成功数值绑定；请判断是否需要核对来源或说明计算依据。候选不是事实错误。',
                      'scope': occurrence_review['scope']})
    notes.extend(_table_citation_notes(draft))
    notes.extend(_padding_notes(draft.markdown))
    return {'status': 'needs_attention' if warnings else 'checks_completed',
            'review_status': 'not_reviewed', 'length': length, 'sections': sections,
            'temporal': temporal,
            'citations': {'body_source_count': len(cited), 'missing_locator': sorted(cited - located)},
            'numbers': {'total': len(numbers), 'checked': checked,
                        'status': 'not_checked' if not checked else 'partial' if checked < len(numbers) else 'checked_bindings',
                        'body_quantity_count': quantity_count, 'results': numbers,
                        'occurrence_review': occurrence_review},
            'warnings': [{**w, 'kind': w.get('kind', 'repairable_error')} for w in warnings], 'notes': notes,
            'scope': '仅确定性诊断；各章是否充分、主体/期间/条件、引用支持与推断强度仍需独立审阅。'}
