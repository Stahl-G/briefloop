"""Read-only writer feedback; never a score or a draft publication gate."""
import re
from .models import BriefDraft
from .length import count_brief, length_stats


def inspect_draft(value, requirements=None, *, store=None, allowed_sources=None):
    from .delivery_checks import check_numbers, quantities
    draft = BriefDraft.model_validate(value)
    req = requirements or {}
    length = length_stats(draft.markdown, target_words=req.get('target_words'), max_words=req.get('max_words'))
    length['below_target'] = bool(length['target_words'] and length['count'] < length['target_words'])
    # Count only H2 chapters, keeping nested headings inside their parent.
    sections = []
    for part in re.split(r'(?m)^## ', draft.markdown)[1:]:
        title, _, body = part.partition('\n')
        sections.append({'title': title.strip(), 'body_units': count_brief(body)})
    cited = set(re.findall(r'\[@([^\]\n]+)\]', draft.markdown))
    located = {c.source_id for c in draft.citations if c.locator.strip()}
    numbers = check_numbers(draft.markdown, [b.model_dump() for b in draft.number_bindings],
                            store=store, allowed_sources=allowed_sources)
    checked = sum(n['checked'] for n in numbers)
    quantity_count = len(list(quantities(draft.markdown)))
    warnings = []
    notes = []
    if length['over_limit']:
        warnings.append({'code': 'over_limit', 'message': '正文超过本轮上限；请保留重点并压缩重复内容。'})
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
    return {'status': 'needs_attention' if warnings else 'checks_completed',
            'review_status': 'not_reviewed', 'length': length, 'sections': sections,
            'citations': {'body_source_count': len(cited), 'missing_locator': sorted(cited - located)},
            'numbers': {'total': len(numbers), 'checked': checked,
                        'status': 'not_checked' if not checked else 'partial' if checked < len(numbers) else 'checked_bindings',
                        'body_quantity_count': quantity_count, 'results': numbers},
            'warnings': [{**w, 'kind': w.get('kind', 'repairable_error')} for w in warnings], 'notes': notes,
            'scope': '仅确定性诊断；各章是否充分、主体/期间/条件、引用支持与推断强度仍需独立审阅。'}
