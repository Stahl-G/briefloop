"""Model-facing writing input; rich documents remain the stored authority."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import analyst_drafts as drafts
from .document_model import markdown_document
from .store import dump
from .writer_assembly import EvidenceInput, assemble as assemble_evidence_records

PROTOCOL = 'writer_input_v1'
Text = Annotated[str, Field(min_length=1)]
SectionID = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WriteReport(EvidenceInput):
    title: Text
    markdown: Text


class Section(Input):
    section_id: SectionID
    markdown: Text
    expected_hash: str | None = None


class WriteSections(Input):
    sections: list[Section] = Field(min_length=1, max_length=12)


class AssembleReport(Input):
    title: Text
    section_ids: list[SectionID] = Field(min_length=1)
    base_revision: str | None = None


class WritingError(ValueError):
    def __init__(self, code, message, *, line=None, field='markdown', expected=None):
        self.diagnostic = {'code': code, 'field': field, 'line': line,
                           'message': message, 'expected': expected}
        super().__init__(dump(self.diagnostic))


def compile_markdown(text):
    """Reject lossy constructs before calling the shared deterministic importer."""
    if not text.strip():
        raise WritingError('empty_body', '正文不能为空')
    tokens = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough']).parse(text)
    lines = text.splitlines()
    for token_index, token in enumerate(tokens):
        line = token.map[0] + 1 if token.map else None
        if token.type in ('html_block', 'html_inline'):
            raise WritingError('unsupported_html', '请用普通 Markdown，不接受原始 HTML', line=line)
        if token.type == 'tr_open' and token.map:
            # markdown-it pads/truncates uneven table rows. Reject instead.
            raw = lines[token.map[0]].strip()
            if raw.startswith('|'): raw = raw[1:]
            if raw.endswith('|') and not raw.endswith('\\|'): raw = raw[:-1]
            from markdown_it.rules_block.table import escapedSplit
            cells = escapedSplit(raw)
            following = tokens[token_index + 1:]
            count = 0
            for t in following:
                if t.type == 'tr_close': break
                if t.type in ('th_open', 'td_open'): count += 1
            if len(cells) != count:
                raise WritingError('table_columns', '表格行的列数与表头不一致', line=line,
                                   expected=f'{count} 列；内容中的竖线请写成 \\|')
        if token.type != 'inline': continue
        for child in token.children or []:
            if child.type == 'html_inline':
                raise WritingError('unsupported_html', '不支持 HTML 内联格式', line=line)
            if child.type == 'image' and not (child.attrGet('src') or '').startswith('briefloop-figure:'):
                raise WritingError('unregistered_image', '图片需先登记为 BriefLoop 图表', line=line)
            if child.type == 'link_open':
                href = child.attrGet('href') or ''
                if urlsplit(href).scheme.lower() not in ('https', 'http', 'mailto') and not href.startswith('#source-'):
                    raise WritingError('unsupported_link', '不支持此链接目标', line=line)
            if child.type == 'text':
                if re.search(r'\[\^[^]]+\]|\$\$|\\\[|\\\(', child.content):
                    raise WritingError('unsupported_markup', '脚注或公式请改成普通正文，原始输入已保留', line=line)
                for citation in re.findall(r'\[@[^\]\n]*(?:\]|$)', child.content):
                    if not re.fullmatch(r'\[@src_[A-Za-z0-9_-]+\]', citation):
                        raise WritingError('citation_syntax', '来源标记应为 [@src_ID]', line=line)
    return markdown_document(text)


def _archive(store, config, operation, args):
    """Attempt-local immutable input, never overwrites an accepted candidate."""
    path = drafts._root(store, config) / 'writer-inputs'
    path.mkdir(parents=True, exist_ok=True)
    identity = drafts._hash({'operation': operation, 'input': args})
    target = path / (identity + '.json')
    if not target.exists(): drafts._write(target, {'operation': operation, 'input': args})
    return target


def _validated(store, config, title, document):
    from .analyst import validate_draft
    return validate_draft(store, config, {'title': title, 'editor_document': document})


def write_report(store, config, args):
    from .length import count_brief
    from .analyst import validate_draft
    with drafts.guard(store, config):
        _archive(store, config, 'write_report', args)
        request = WriteReport.model_validate(args)
        drafts._packet_hash(config)
        current = drafts._root(store, config) / 'current.json'
        value = _validated(store, config, request.title, compile_markdown(request.markdown))
        evidence = assemble_evidence_records(config, request, value['markdown'])
        if evidence:
            value = validate_draft(store, config, {**value, **evidence})
        if current.exists():
            revision = drafts._read(current)['revision']
            candidate = drafts._candidate(store, config, {'revision': revision})
            if candidate['draft'] == value:
                return {'status': 'saved', 'revision': revision, 'replayed': True,
                        'review_status': 'not_reviewed', 'body_units': count_brief(value['markdown'])}
            raise WritingError('draft_exists', '已有稿件；读取当前版本后局部修改，不覆盖已保存正文', field='revision')
        return drafts._save_locked(store, config, value)


class AssembleEvidence(EvidenceInput):
    base_revision: Text


def assemble_evidence(store, config, args):
    """One evidence batch, one version check and one atomic candidate save."""
    with drafts.guard(store, config):
        request = AssembleEvidence.model_validate(args)
        prior = drafts._candidate(store, config, {'revision': request.base_revision})['draft']
        if request.model_fields_set == {'base_revision'}:
            raise WritingError('empty_change', '请提供至少一类证据字段')
        _archive(store, config, 'assemble_evidence', args)
        changes = assemble_evidence_records(config, request, prior['markdown'], prior.get('citations', []))
        saved = drafts._save_locked(store, config, {'base_revision': request.base_revision, **changes})
        return {**saved, 'evidence_counts': {k: len(v) for k, v in changes.items()},
                'record_keys': {k: [evidence_key(r) for r in v] for k, v in changes.items()},
                'evidence_status': 'exact_locations_resolved_not_semantically_reviewed'}


def write_sections(store, config, args):
    from .analyst import _sections_file
    from .length import count_brief
    with drafts.guard(store, config):
        _archive(store, config, 'write_sections', args)
        request = WriteSections.model_validate(args)
        ids = [s.section_id for s in request.sections]
        if len(set(ids)) != len(ids): raise WritingError('duplicate_section', '章节 ID 不得重复', field='sections')
        path = _sections_file(store, config)
        ledger = drafts._read(path) if path.exists() else {}
        receipts = []
        for s in request.sections:
            value = _validated(store, config, s.section_id, compile_markdown(s.markdown))
            section = {key: value[key] for key in ('editor_document', 'citations')}
            prior = ledger.get(s.section_id)
            if prior != section and ((prior and s.expected_hash != drafts._hash(prior)) or
                                     (not prior and s.expected_hash is not None)):
                raise WritingError('section_conflict', '章节已变化，请读取当前章节 hash', field=s.section_id)
            ledger[s.section_id] = section
            receipts.append({'section_id': s.section_id, 'hash': drafts._hash(section),
                             'body_units': count_brief(value['markdown'])})
        drafts._write(path, ledger)
        return {'status': 'saved_sections', 'sections': receipts, 'section_ids': list(ledger),
                'review_status': 'not_reviewed'}


def assemble_report(store, config, args):
    with drafts.guard(store, config):
        request = AssembleReport.model_validate(args)
        current = drafts._root(store, config) / 'current.json'
        if current.exists() and request.base_revision is None:
            raise WritingError('revision_required', '重新组装必须带当前 base_revision', field='base_revision')
        return drafts._save_locked(store, config, request.model_dump(exclude_none=True))


class EvidenceChange(Input):
    record_key: str | None = None
    value: dict | None = None  # The tool schema specializes this per field below.


class UpdateEvidence(Input):
    base_revision: Text
    field: Literal['citations', 'number_bindings', 'temporal_claims']
    changes: list[EvidenceChange] = Field(min_length=1, max_length=60)


def evidence_key(value):
    # Content identity is stable across reordering; changed records get new keys.
    return drafts._hash(value)


def update_draft_evidence(store, config, args):
    from .models import Citation, NumberBinding, TemporalClaim
    types = {'citations': Citation, 'number_bindings': NumberBinding, 'temporal_claims': TemporalClaim}
    with drafts.guard(store, config):
        request = UpdateEvidence.model_validate(args)
        prior = drafts._candidate(store, config, {'revision': request.base_revision})['draft']
        records = list(prior.get(request.field) or [])
        keys = [evidence_key(r) for r in records]
        touched = set()
        for change in request.changes:
            key = change.record_key
            if key is not None:
                if key in touched or keys.count(key) != 1:
                    raise WritingError('evidence_conflict', '记录键不存在、重复或有歧义，请重新读取', field=request.field)
                touched.add(key)
            elif change.value is None:
                raise WritingError('empty_change', '新增须提供 value；删除须提供 record_key', field=request.field)
            value = types[request.field].model_validate(change.value).model_dump(mode='json') if change.value is not None else None
            if key is None:
                if evidence_key(value) not in keys:
                    records.append(value); keys.append(evidence_key(value))
            else:
                index = keys.index(key)
                if value is None:
                    records.pop(index); keys.pop(index)
                else:
                    records[index] = value; keys[index] = evidence_key(value)
        saved = drafts._save_locked(store, config, {'base_revision': request.base_revision, request.field: records})
        return {**saved, 'field': request.field, 'record_keys': [evidence_key(r) for r in records]}


class TextReplacement(Input):
    old_text: Text
    new_text: str


class PatchText(Input):
    base_revision: Text
    replacements: list[TextReplacement] = Field(min_length=1, max_length=24)


def patch_report_text(store, config, args):
    with drafts.guard(store, config):
        request = PatchText.model_validate(args)
        prior = drafts._candidate(store, config, {'revision': request.base_revision})['draft']
        section = {k: prior[k] for k in ('editor_document', 'citations')}
        patched = drafts.replace_section_text(section, [r.model_dump() for r in request.replacements], drafts._hash(section))
        return drafts._save_locked(store, config, {'base_revision': request.base_revision,
                                                  'editor_document': patched['editor_document']})


def block_keys(content):
    return [f'block-{i}-{drafts._hash(block)[:16]}' for i, block in enumerate(content)]


class ReplaceBlocks(Input):
    base_revision: Text
    block_keys: list[Text] = Field(min_length=1)
    markdown: Text


def replace_report_blocks(store, config, args):
    from copy import deepcopy
    with drafts.guard(store, config):
        _archive(store, config, 'replace_report_blocks', args)
        request = ReplaceBlocks.model_validate(args)
        prior = drafts._candidate(store, config, {'revision': request.base_revision})['draft']
        doc = deepcopy(prior['editor_document']); content = doc['content']; keys = block_keys(content)
        if len(set(request.block_keys)) != len(request.block_keys) or any(k not in keys for k in request.block_keys):
            raise WritingError('block_conflict', '块身份已变化，请读取最新正文和块键', field='block_keys')
        positions = [keys.index(k) for k in request.block_keys]
        if positions != list(range(positions[0], positions[-1]+1)):
            raise WritingError('block_range', '一次只能替换有序连续块', field='block_keys')
        def advanced(node):
            if node.get('type') == 'image': return True
            if set(node.get('attrs', {})) - {'blockId', 'level', 'start', 'language', 'sourceId'}: return True
            if any(m['type'] not in ('bold','italic','code','link') for m in node.get('marks', [])): return True
            return any(advanced(c) for c in node.get('content', []))
        if any(advanced(b) for b in content[positions[0]:positions[-1]+1]):
            raise WritingError('rich_format_preserved', '所选范围包含图片或高级排版，请精确改字或在富文本编辑器修改', field='block_keys')
        replacement = compile_markdown(request.markdown)['content']
        doc['content'][positions[0]:positions[-1]+1] = replacement
        return drafts._save_locked(store, config, {'base_revision': request.base_revision, 'editor_document': doc})


def protocol(config):
    path = Path(config['packet_root']) / 'input.json'
    return json.loads(path.read_text()).get('writer_input_protocol', 'rich_json_v1')


def operations():
    result = {
        'write_report': (WriteReport, write_report, '首次保存正文。标题和Markdown，引用用 [@src_ID]；可同时传citations、number_bindings、temporal_claims，程序按逐字摘录定位并装配，不手写富文本JSON或行号。'),
        'assemble_evidence': (AssembleEvidence, assemble_evidence, '一次装配保存多类证据。传当前base_revision与改变的证据数组；所传数组整类替换，未传字段保留。citations给source_id/excerpt；数字与日期给source_excerpt，locator可省略，重复摘录才需line范围。数字仍给value/unit、主体期间与正文report_quote/number_text。来源摘录自动登记到citations，不自动给正文加标记。不需要自己编写组装脚本。'),
        'write_sections': (WriteSections, write_sections, '长稿分章保存Markdown；修改已存章节附其expected_hash。可一批保存数章。'),
        'assemble_report': (AssembleReport, assemble_report, '按章节ID顺序组装正文，已存完整稿须带base_revision。'),
        'update_draft_details': (DraftDetails, update_draft_details, '单独更新缺口、研究说明或已计算报告数据；只传改变的字段，不重写正文。'),
        'patch_report_text': (PatchText, patch_report_text, '精确改正文文字，保留格式与引用。old_text必须在单个文字节点唯一命中。'),
        'replace_report_blocks': (ReplaceBlocks, replace_report_blocks, '仅替换连续的正文块，block_keys从read_draft取。图片/高级排版不降级，使用精确改字。'),
    }

    # Flat, field-specific tools: providers may drop arguments for root anyOf
    # schemas. Each model sees the exact record type it needs to populate.
    from .models import Citation, NumberBinding, TemporalClaim
    from pydantic import create_model
    for field, record in [('citations', Citation), ('number_bindings', NumberBinding), ('temporal_claims', TemporalClaim)]:
        item = create_model(field + '_record', __base__=record, record_key=(str | None, None))
        request = create_model(field + '_update', __base__=Input, base_revision=(Text, ...),
                               records=(list[item], Field(default_factory=list, max_length=30)),
                               remove_keys=(list[str], Field(default_factory=list, max_length=30)))
        def update(store, config, args, field=field, request=request):
            parsed = request.model_validate(args)
            changes = [{'record_key': r.record_key, 'value': r.model_dump(mode='json', exclude={'record_key'})}
                       for r in parsed.records]
            changes.extend({'record_key': key, 'value': None} for key in parsed.remove_keys)
            return update_draft_evidence(store, config, {'base_revision': parsed.base_revision,
                                                        'field': field, 'changes': changes})
        result['update_' + field] = (request, update,
            f'独立更新 {field}。records中直接写记录字段，不包装value对象；数字记录的value就是数值。修改时在该记录加read_draft返回的record_key，新记录省略键。删除仅传remove_keys。不要重交正文。')
    return result


def tool_specs():
    from .native_roles import _json_result
    result = []
    for name, (model, fn, description) in operations().items():
        def handler(store, config, args, fn=fn):
            if protocol(config) != PROTOCOL: raise ValueError('本写作任务未启用 Markdown 写作接口')
            try:
                return _json_result(fn(store, config, args))
            except ValidationError as exc:
                errors = [{'field': '.'.join(str(p) for p in e['loc']), 'code': e['type'], 'message': e['msg']}
                          for e in exc.errors(include_input=False, include_url=False)[:8]]
                raise ValueError(dump({'code': 'invalid_fields', 'errors': errors,
                                      'next': '只修这些字段；已保存正文不受影响。'})) from None
        result.append({'name': name, 'label': description.split('。')[0], 'description': description + ' 同一稿件每轮只调用一个写入工具；收到新revision后再发下一次写入，不在同一轮并列提交。',
                       'guide': description, 'sequential': True,
                       'parameters': model.model_json_schema(), 'handler': handler})
    return result


GUIDE = '''写作协议 writer_input_v1：正文使用 Markdown，普通表格使用管道表格，引用使用 [@src_ID]，图表使用已登记的 briefloop-figure:fig_ID。不要输出 editor_document、tableRow 或完整 BriefDraft JSON。
短稿一次 write_report(title, markdown)，可同次附 citations、number_bindings、temporal_claims；长稿 write_sections 后 assemble_report。也可先保存正文，再一次 assemble_evidence 登记三类证据。程序生成富文本、按逐字摘录找行号、核对数字的正文片段并装配记录，不要再自己写 Python 组装脚本。citations给source_id/excerpt；数字和日期给source_excerpt；locator唯一匹配时可省略，重复匹配才提供line范围。value/unit、主体、期间、结论与证据的关系仍由你确定，不省略这些语义字段。所传证据数组整类替换，未传的类别保留；少量记录修改继续用 update_citations/update_number_bindings/update_temporal_claims。来源归属、口径、采用条件要求不变。
同一稿件的写入有先后依赖：每轮只发一个写入调用，等返回新 revision 后再发下一个。不要把多个证据更新放在同一轮共用 base_revision；执行器串行执行也不会自动替换你传入的旧版本。
取得 revision 后 check_draft 检查；局部文字用 patch_report_text，结构改动先 read_draft(field=body) 取得 block_keys，再 replace_report_blocks。证据修改只交变更记录；每次变更使用最新 base_revision，再检查新 revision。只修明确问题，不反复重交全文。submit_draft 提交已检查的最新 revision，结束写作，不自行评分。
已有人工富文本不得整稿降级；保留未修改节点、图片和样式。工具若提示高级排版需保留，改用精确文字修改。原始输入已保存不代表接纳或核实。'''


from .models import GapRecord
from .industry_data import IndustryData


class DraftDetails(Input):
    base_revision: Text
    research_notes: list[dict] | None = None
    gap_records: list[GapRecord] | None = None
    gaps: list[str] | None = None
    report_data: IndustryData | None = None
    figures: list[str] | None = None


def update_draft_details(store, config, args):
    with drafts.guard(store, config):
        request = DraftDetails.model_validate(args)
        changes = request.model_dump(mode='json', exclude_unset=True)
        if len(changes) < 2: raise WritingError('empty_change', '请提供要修改的字段')
        if any(v is None for k,v in changes.items() if k not in ('report_data',)):
            raise WritingError('null_field', '清空列表请传 []，不要传 null')
        return drafts._save_locked(store, config, changes)


def ensure_revision_base(store, config):
    """Initialize a revision attempt from the frozen rich original, never retype it."""
    task = json.loads((Path(config['packet_root'])/'input.json').read_text())
    if task.get('mode') != 'revision': return
    with drafts.guard(store, config):
        if (drafts._root(store, config)/'current.json').exists(): return
        drafts._packet_hash(config)
        original = json.loads((Path(config['packet_root'])/'original.json').read_text())
        if original['id'] != task['base_version'] or original['hash'] != task['base_hash']:
            raise WritingError('base_identity', '冻结原稿身份不一致')
        doc = original['editor_document']
        if isinstance(doc, str): doc = json.loads(doc)
        if not doc: raise WritingError('base_format', '修订需要已有富文档；此任务不能隐式转换旧稿')
        detail = original['detail']
        if isinstance(detail, str): detail = json.loads(detail)
        from .models import BriefDraft
        # Store detail also holds derived renderer/citation metadata, not author fields.
        value = {**{k:v for k,v in detail.items() if k in BriefDraft.model_fields}, 'editor_document': doc}
        # Task-bound fields are applied by validate_draft, not copied from an old run.
        value.pop('reader_contract', None);value.pop('reconciliation_id', None)
        drafts._save_locked(store, config, value)


def with_revision_base(spec):
    handler = spec['handler']
    def wrapped(store, config, args):
        ensure_revision_base(store, config)
        return handler(store, config, args)
    return {**spec, 'handler': wrapped}
