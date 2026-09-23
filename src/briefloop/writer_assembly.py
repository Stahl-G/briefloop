"""Deterministic evidence assembly shared by Native tools and the writer CLI.

The author selects claims and verbatim excerpts. This module resolves their
locations; it does not decide whether a source supports a claim.
"""
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .models import Citation, NumberBinding, TemporalClaim

MAX_ASSEMBLY_ERRORS = 8


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


def source_locator(config):
    """Resolve excerpts in this packet, sharing scope checks and the read cache."""
    root = Path(config['packet_root']).resolve()
    index = {s['source_id']: s for s in json.loads((root/'source-index.json').read_text(encoding='utf-8'))['sources']}
    cache = {}

    def locate(source_id, excerpt, locator=''):
        source = index.get(source_id)
        if source is None or source.get('reference_only'):
            raise ValueError('来源不属于本轮事实材料')
        if source_id not in cache:
            path = (root/source['text_file']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('来源路径超出冻结任务包')
            cache[source_id] = path.read_text(encoding='utf-8-sig')
        return locate_excerpt(cache[source_id], excerpt, locator)
    return locate


def resolve_record(field, value, markdown, locate, *, require_number_quote=True):
    """Normalize one author-selected record; never infer its factual meaning."""
    value = dict(value)
    excerpt = value['excerpt'] if field == 'citations' else value['source_excerpt']
    value['locator'] = locate(value['source_id'], excerpt, value.get('locator', ''))
    if field == 'number_bindings':
        quote, token = value.get('report_quote', ''), value.get('number_text', '')
        # Legacy partial bindings remain readable/unchecked. Once a body span
        # is supplied, both batch and local updates enforce the same uniqueness.
        if require_number_quote or quote or token:
            if not quote or markdown.count(quote) != 1:
                raise ValueError('report_quote 必须在保存后的正文中逐字唯一，请用 read_draft(field=body) 读取准确片段')
            if not token or quote.count(token) != 1:
                raise ValueError('number_text 必须在 report_quote 中逐字唯一；缩小到对应数值的准确正文片段')
            from .delivery_checks import complete_number_token
            complete = complete_number_token(quote, token, value.get('value'), value.get('unit', ''))
            if complete:
                value['number_text'] = complete
    if field == 'temporal_claims':
        value.pop('source_excerpt')
    return value


def assemble(config, evidence, markdown, prior_citations=()):
    """Return supplied fields only; callers guard the packet and save atomically."""
    locate = source_locator(config)
    result, errors = {}, []

    def reject(message):
        # Report independent record errors together. Nothing is saved until
        # the whole batch passes; keep recovery responses small for the model.
        errors.append(message)
        if len(errors) >= MAX_ASSEMBLY_ERRORS:
            raise ValueError('\n'.join(errors) +
                f'\n每次最多列出 {MAX_ASSEMBLY_ERRORS} 项错误；本批未保存，请修复后重试。') from None

    for field in ('citations', 'number_bindings', 'temporal_claims'):
        if field not in evidence.model_fields_set:
            continue
        items = getattr(evidence, field)
        if items is None:
            reject(f'{field}: 清空请传 []，不要传 null')
            continue
        records = []
        for i, item in enumerate(items):
            try:
                value = resolve_record(field, item.model_dump(mode='json'), markdown, locate)
                if value not in records:
                    records.append(value)
            except (ValueError, OSError) as exc:
                reject(f'{field}[{i}]: {exc}')
        result[field] = records
    if errors:
        raise ValueError('\n'.join(errors))
    # A supplied source excerpt is sufficient to construct a location record,
    # but never to insert a citation into an arbitrary body sentence.
    supporting = list(result.get('citations', prior_citations))
    for field in ('number_bindings', 'temporal_claims'):
        for item in getattr(evidence, field) or []:
            citation = {'source_id': item.source_id,
                        'locator': locate(item.source_id, item.source_excerpt, item.locator),
                        'excerpt': item.source_excerpt}
            if citation not in supporting:
                supporting.append(citation)
    if supporting:
        result['citations'] = supporting
    return result
