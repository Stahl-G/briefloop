"""Deterministic evidence assembly shared by Native tools and the writer CLI.

The author selects claims and verbatim excerpts. This module resolves their
locations; it does not decide whether a source supports a claim.
"""
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import Citation, NumberBinding, TemporalClaim


class CitationInput(Citation):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class NumberInput(NumberBinding):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: str = Field(min_length=1)
    source_excerpt: str = Field(min_length=1)
    report_quote: str = Field(min_length=1)
    number_text: str = Field(min_length=1)


class TimeInput(TemporalClaim):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: str = Field(min_length=1)
    source_excerpt: str = Field(min_length=1)


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    citations: list[CitationInput] | None = Field(default=None, max_length=120)
    number_bindings: list[NumberInput] | None = Field(default=None, max_length=120)
    temporal_claims: list[TimeInput] | None = Field(default=None, max_length=120)


def locate_excerpt(text, excerpt, locator=''):
    """Resolve exact text, never fuzzy-match or silently choose the first hit.

    A supplied line range disambiguates repeated text. A unique excerpt can
    correct an inaccurate line hint. Both cases return the actual line range.
    """
    if not excerpt.strip():
        raise ValueError('原文摘录不能为空或仅为空白')
    matches = []
    offset = text.find(excerpt)
    while offset >= 0:
        start = text.count('\n', 0, offset) + 1
        end = text.count('\n', 0, offset + len(excerpt) - 1) + 1
        matches.append((start, end))
        offset = text.find(excerpt, offset + 1)
    if not matches:
        raise ValueError('原文中找不到逐字摘录；请回读来源，不要改写摘录')
    if len(matches) > 1:
        hint = re.fullmatch(r'\s*lines?\s+(\d+)(?:\s*-\s*(\d+))?\s*', locator, re.I)
        if hint:
            lo, hi = int(hint[1]), int(hint[2] or hint[1])
            narrowed = [(a, b) for a, b in matches if lo <= a <= b <= hi]
        else:
            narrowed = []
        if len(narrowed) != 1:
            lines = ', '.join(str(a) for a, _ in matches[:6])
            raise ValueError(f'原文摘录命中 {len(matches)} 处（起始行 {lines}）；请提供唯一 line 范围或更长的逐字摘录')
        matches = narrowed
    start, end = matches[0]
    return f'line {start}' if start == end else f'line {start}-{end}'


def assemble(config, evidence, markdown, prior_citations=()):
    """Return only supplied evidence fields, with resolved canonical records.

    Source text comes exclusively from the frozen packet. Callers guard and
    verify its fingerprint before invoking this function, then save atomically.
    """
    root = Path(config['packet_root']).resolve()
    index = {s['source_id']: s for s in json.loads((root/'source-index.json').read_text())['sources']}
    cache, result = {}, {}
    for field in ('citations', 'number_bindings', 'temporal_claims'):
        if field not in evidence.model_fields_set:
            continue
        items = getattr(evidence, field)
        if items is None:
            raise ValueError(f'{field}: 清空请传 []，不要传 null')
        records = []
        for i, item in enumerate(items):
            try:
                source = index.get(item.source_id)
                if source is None or source.get('reference_only'):
                    raise ValueError('来源不属于本轮事实材料')
                if item.source_id not in cache:
                    path = (root/source['text_file']).resolve()
                    if not path.is_relative_to(root):
                        raise ValueError('来源路径超出冻结任务包')
                    cache[item.source_id] = path.read_text(encoding='utf-8-sig')
                excerpt = item.excerpt if field == 'citations' else item.source_excerpt
                locator = locate_excerpt(cache[item.source_id], excerpt, item.locator)
                value = item.model_dump(mode='json')
                value['locator'] = locator
                if field == 'number_bindings':
                    if markdown.count(item.report_quote) != 1:
                        raise ValueError('report_quote 必须在保存后的正文中逐字唯一，请扩大或修正正文片段')
                    if item.report_quote.count(item.number_text) != 1:
                        raise ValueError('number_text 必须在 report_quote 中逐字唯一')
                if field == 'temporal_claims':
                    # TemporalClaim has no excerpt field; retain the author's
                    # selected excerpt as a normal citation as well.
                    value.pop('source_excerpt')
                if value not in records:
                    records.append(value)
            except (ValueError, OSError) as exc:
                raise ValueError(f'{field}[{i}]: {exc}') from None
        result[field] = records
    # A supplied source excerpt is sufficient to construct a location record,
    # but never to insert a citation into an arbitrary body sentence.
    supporting = list(result.get('citations', prior_citations))
    for field in ('number_bindings', 'temporal_claims'):
        for item in getattr(evidence, field) or []:
            citation = {'source_id': item.source_id,
                        'locator': locate_excerpt(cache[item.source_id], item.source_excerpt, item.locator),
                        'excerpt': item.source_excerpt}
            if citation not in supporting:
                supporting.append(citation)
    if supporting:
        result['citations'] = supporting
    return result
