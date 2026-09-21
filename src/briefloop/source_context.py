"""Bounded navigation hints over saved text, never evidence or a verdict."""
import re

# Include changes that remove restrictions as well as restrictions themselves.
# These cues locate possible context; they do not decide relevance or truth.
_CUES = re.compile(
    r'\b(?:prerequisites?|requirements?|limitations?|caveats?|availability|'
    r'installation|pending|unmerged|under review|not yet|private preview|'
    r'early access|coming soon|deprecated|superseded|correction|erratum|'
    r'no longer|now available|generally available|merged|resolved|lifted)\b'
    r'|前提|前置|限制|例外|适用范围|适用条件|安装|尚未|待合并|内测|公测|'
    r'开放范围|可用性|更正|勘误|已合并|已解除|不再需要|正式开放|全面开放|已解决',
    re.IGNORECASE,
)


def navigation(text, *, max_ranges=16):
    """Locate nearby lines without copying or summarizing source assertions."""
    lines = text.splitlines()
    ranges = []
    matched = 0
    for number, line in enumerate(lines, 1):
        if not _CUES.search(line):
            continue
        matched += 1
        start, end = max(1, number - 3), min(len(lines), number + 6)
        if ranges and start <= ranges[-1]['end_line'] + 1:
            ranges[-1]['end_line'] = end
        else:
            ranges.append({'start_line': start, 'end_line': end})
    return {'total_lines': len(lines), 'candidate_ranges': ranges[:max_ranges],
            'matched_lines': matched, 'omitted_ranges': max(0, len(ranges) - max_ranges),
            'scope': '仅关键词定位，包含限制与解除/更正线索；不是已读记录或事实判断。无匹配不代表没有条件。'}


def navigation_note(text):
    hints = navigation(text)
    ranges = ', '.join(f"{r['start_line']}-{r['end_line']}" for r in hints['candidate_ranges']) or '无匹配'
    return (f"[全文条件/更新候选行段：{ranges}；另有 {hints['omitted_ranges']} 段未列出。"
            + hints['scope'] + '按需回读原文及后续更正。]')
