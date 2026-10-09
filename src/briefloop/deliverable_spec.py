"""Saved reader requirements and role-specific instructions.

The original requirements remain authoritative. A reader contract is an agent's
source-bound interpretation for handoff, never a replacement for user input.
"""
from copy import deepcopy
import hashlib
import json


CONTRACT_KINDS = ('reader_content', 'research_method', 'writing_preference', 'manual_assignment')
ROLE_NAMES = ('orchestrator', 'scout', 'analyst', 'evaluator', 'reviewer', 'revision')
# Presentation, method and manual clauses are not required reader deliverables, so an
# unfinished interpretation of them is a notice. Only a reader_content clause keeps a
# requirement on the formal-delivery gate. The semantic type comes from the saved
# reader contract, never from which input field the user happened to type into.
SOFT_CONTRACT_KINDS = ('research_method', 'writing_preference', 'manual_assignment')


def requirement_severity(spec):
    """Map requirement_id -> 'hard'/'soft' using the reader contract, when present.

    Without a contract this returns an empty map; callers must then fall back to the
    raw requirement kind instead of guessing a semantic type here.
    """
    contract = spec.get('reader_contract') or {}
    kinds = {}
    for clause in contract.get('clauses', []):
        kinds.setdefault(clause['requirement_id'], set()).add(clause['kind'])
    soft = set(SOFT_CONTRACT_KINDS)
    return {identity: ('soft' if found <= soft else 'hard')
            for identity, found in kinds.items()}


def clause_items(spec):
    """Deterministic, consumption-side view of the saved reader contract clauses.

    The identity binds requirement, kind, quote and instruction, so two clauses that
    quote the same sentence but carry different jobs never merge. Nothing here is
    written into the spec, so the requirement fingerprint is unchanged; a clause is
    identified inside one frozen review packet by (packet fingerprint + clause_id).
    """
    contract = spec.get('reader_contract') or {}
    items = []
    for clause in contract.get('clauses', []):
        payload = json.dumps([clause['requirement_id'], clause['kind'], clause['source_quote'], clause['instruction']],
                             ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        items.append({'clause_id': 'clause_' + hashlib.sha256(payload.encode()).hexdigest()[:16],
                      'requirement_id': clause['requirement_id'], 'kind': clause['kind'],
                      'source_quote': clause['source_quote'], 'instruction': clause['instruction'],
                      'severity': 'soft' if clause['kind'] in SOFT_CONTRACT_KINDS else 'hard'})
    return items


def resolve(requirements, template=None, *, reader_contract=None):
    sections = requirements.get('sections') or (template or {}).get('sections', [])
    spec = {'schema_version': 2, 'title': requirements['title'],
            'objective': requirements['objective'], 'audience': requirements.get('audience', ''),
            'writing_mode': requirements.get('writing_mode', 'general'),
            'period': requirements.get('period', ''), 'report_date': requirements.get('report_date', ''),
            'target_words': requirements.get('target_words'), 'max_words': requirements.get('max_words'),
            'template_id': requirements.get('template_id'),
            'sections': deepcopy(sections), 'manual_sections': list(requirements.get('manual_sections', [])),
            'key_questions': list(requirements.get('key_questions', [])),
            'writing_preferences': list(requirements.get('writing_preferences', [])),
            'requirement_items': requirement_items(requirements),
            'references': '正文短编号，图表简注，文末精简来源表；详细核查另存',
            'interpretation_rule': 'objective及requirement_items保留用户原始要求；reader_contract是待对照原文核查的执行解释，不得降级或替换明确要求。'}
    if requirements.get('reader_profile'):
        # Only runs written for a saved reader carry it, so older contracts keep their identity.
        profile = requirements['reader_profile']
        spec['reader_profile'] = {'name': profile.get('name', ''), 'decisions': profile.get('decisions', ''),
                                  'preferences': profile.get('preferences', '')}
    if requirements.get('target_minutes') is not None:
        spec['target_minutes'] = requirements['target_minutes']
    # Legacy max_words stays advisory without changing its saved contract hash.
    # Only an explicit strict policy adds a new requirement identity.
    if requirements.get('length_mode') == 'strict':
        spec['length_mode'] = 'strict'
        spec['length_requirement'] = deepcopy(requirements.get('length_requirement'))
    # Chinese keeps the spec byte-identical to runs saved before the language
    # enum. Language stays out of the reader-contract fingerprint (it is frozen
    # per run), and review validation ignores it for packets saved without it.
    from .models import report_language
    if report_language(requirements.get('language')) == 'en':
        spec['language'] = 'en'
    if requirements.get('workflow_snapshot'):
        spec['workflow_snapshot'] = deepcopy(requirements['workflow_snapshot'])
    if reader_contract is not None:
        spec['reader_contract'] = validate_reader_contract(spec, reader_contract)
    return spec


def _source_fingerprint(spec):
    source = {key: value for key, value in spec.items() if key not in ('reader_contract', 'language')}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reader_contract_schema(spec):
    """Schema for plan.json.reader_contract; semantic interpretation stays with agents."""
    return {'type': 'object', 'additionalProperties': False,
            'properties': {
                'source_fingerprint': {'type': 'string', 'const': _source_fingerprint(spec)},
                'clauses': {'type': 'array', 'minItems': 1, 'items': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {
                        'requirement_id': {'type': 'string', 'enum': [x['requirement_id'] for x in spec['requirement_items']]},
                        'kind': {'type': 'string', 'enum': list(CONTRACT_KINDS)},
                        'source_quote': {'type': 'string', 'minLength': 1,
                                         'description': '对应 requirement_id 的原始 requirement.text 中连续逐字片段；不得拼接不连续内容。'},
                        'instruction': {'type': 'string', 'minLength': 1}},
                    'required': ['requirement_id', 'kind', 'source_quote', 'instruction']}}},
            'required': ['source_fingerprint', 'clauses']}


def validate_reader_contract(spec, value):
    """Validate provenance and shape, not whether interpretation is complete or correct.

    Exact quotes prevent invented user requirements. Independent review must still
    compare the clauses with the full originals to detect omissions or distortion.
    """
    if not isinstance(value, dict) or set(value) != {'source_fingerprint', 'clauses'}:
        raise ValueError('产物约定须包含 source_fingerprint 与 clauses')
    if value['source_fingerprint'] != _source_fingerprint(spec):
        raise ValueError('产物约定不属于当前要求版本')
    items = {item['requirement_id']: item for item in spec['requirement_items']}
    if not isinstance(value['clauses'], list) or not value['clauses']:
        raise ValueError('产物约定必须解释本轮要求')
    seen, clauses = set(), []
    for index, clause in enumerate(value['clauses']):
        if not isinstance(clause, dict) or set(clause) != {'requirement_id', 'kind', 'source_quote', 'instruction'}:
            raise ValueError('产物约定条目字段无效')
        if any(not isinstance(text, str) or not text.strip() for text in clause.values()):
            raise ValueError('产物约定条目必须是非空文字')
        requirement = items.get(clause['requirement_id'])
        if not requirement or clause['kind'] not in CONTRACT_KINDS:
            raise ValueError('产物约定引用未知要求或类型')
        if clause['source_quote'] not in requirement['text']:
            raise ValueError(f"clauses[{index}].source_quote（requirement_id={clause['requirement_id']}）："
                             '必须是对应原始 requirement.text 中连续逐字存在的片段；不得拼接不连续内容。')
        prescribed_kind = {'manual': 'manual_assignment', 'question': 'reader_content', 'writing': 'writing_preference'}.get(requirement['kind'])
        if prescribed_kind and clause['kind'] != prescribed_kind:
            raise ValueError('显式必答问题、写作偏好及人工分工不可被重新分类')
        seen.add(clause['requirement_id'])
        clauses.append(dict(clause))
    if seen != set(items):
        raise ValueError('产物约定遗漏原始要求：' + ','.join(sorted(set(items) - seen)))
    return {'source_fingerprint': value['source_fingerprint'], 'clauses': clauses}


_READER_RULES = '''企业内部报告交付要求：为熟悉行业的读者完成信息取舍，每节围绕关键变化、对组织的具体影响和有依据的行动或观察节点展开。用自然段落、图表或简短条目表达，关键判断放在段首；每段提供新的事实、业务判断或行动信息，篇幅留给本期问题。
准确性直接体现在事实用词中：保留日期、单位、指标名称、预测主体和计划阶段。会改变当前判断的条件紧贴该判断说明一次；有信息量的否定、负面事实和不确定性应保留。例如“公司计划于明年一季度试产，设备到场和客户认证是近期跟踪节点”已经交代状态与行动。
正文与内部记录各有职责：来源读取失败、资料覆盖清单、未核验事项、冲突详情、计算过程和工具状态进入 research_notes/gaps 或核查结果；企业正文不附这些内部检查清单。用户明确要求把方法或核查结果作为正文专题时按该要求安排。缺口影响主要判断时，在相关位置写清具体影响，不追加整段通用免责说明。
证据不支持的判断，应在既定权限与预算内补查，或缩小判断范围、纠正、移除。把问题记入核查面板不等于已修好正文；追加免责声明不能维持原断言，也不要统一保证“以上缺口不影响结论”。研究未完成保留真实状态。
分析检查项留作思考，不抄成每段的“传导机制是／条件是／经营含义是／后续观察”等标签；研究方法约束用于执行，不逐字复述给读者。不按否定词或固定句式的出现次数机械删句、评分。'''

_EVALUATION_RULES = '''读者要求与四维评价：
证据：核对具体主张、来源及支持范围，保留准确状态和必要条件；披露缺口不能抵消错误，也不把正确表达的计划或预测判为错误。
覆盖：逐项对照原始要求与 reader_contract，查找漏项和错误归类；作者登记的条目不证明覆盖完整。必答问题只有“尚未核验”仍是未完成；明确人工填写部分按占位要求检查。
分析：看事实如何支持业务判断及可用的行动或观察节点，识别无依据的因果、外推和统一保证。列出分析标签、谨慎提醒或研究步骤不算提供分析价值。
区分事实陈述与行动建议：明确写成“建议／应／可”的动作，核查其事实前提、业务关联及可行性，不要求来源已经提出同一建议；不能仅因来源未点名执行部门就判建议为虚构事实。若正文声称部门已采取行动、已确定安排或拥有特定权限，仍须核查依据；建议也不能夹带无依据的数值、因果保证或组织事实。
表达：按目标读者、篇幅和明确写作偏好评估，核查语言挤占正文、机械标签和重复解释是具体交付问题。发现时给出正文片段、违反的要求、对阅读的影响和修订目标；不能以“披露充分／态度谨慎”为表达加分。对企业内部模式，若仍存在影响阅读的成片内部检查清单或重复标签，表达不应评为充分完成（4/5）；说明基于语义与上下文的判断，不用关键词计数代替审阅。表达分锚点：4=对目标读者直接可用，仅有很小可选优化；3=已满足要求，剩余为非必要润色；2=存在明确违约或反复噪音，需要用户实质删改；1=更像研究记录或材料拼接。表达≤2 时，总体判断不得仍为「达到要求」；出现该矛盾按实际记录，不替系统改分。
四维分数分别反映任务完成程度，不相互抵消。未核验、证据反驳、覆盖不足、审阅失败与无发现分别记录；无发现不证明全部重要主张已检查。'''


def instructions(spec, role='analyst', *, include_spec=True):
    if role not in ROLE_NAMES:
        raise ValueError('未知产物约定角色：' + str(role))
    reader = (_READER_RULES if spec.get('writing_mode') == 'internal_report' else
              '围绕读者目的取舍信息，写成连贯、直接、有依据的报告。准确保留日期、单位、事实状态与必要条件；内部研究过程写入 research_notes/gaps，按用户明确需要安排正文。')
    common = '''本轮明确要求优先于模板默认，其次是兼容技能及通用默认。原始 objective 可能混合内容目标、研究方法和写作偏好，要执行各自职责，不将整段要求抄成正文。
模板主章节默认固定；用户明确要求可增删、改名或重排。sections 指定的职责、顺序和人工占位必须落实。mode=manual 只写指定 placeholder；manual_sections 放在相应已有主章节内并只留“待填充”，不开展替代研究、不作为覆盖不足。
影响交付的缺口写入 draft.gap_records，每条含 related（哪条必答问题、判断或正文位置）、impact、action、status 四字段；status 默认 open，只有独立评价或核查确认后才能标 resolved。纯执行信息（工具失败、覆盖清单、计算过程）不写入 gap_records，也不写入正文。'''
    role_text = {
        'orchestrator': '''规划交接：在已有 plan.json 的 reader_contract 字段保存本轮要求解释，按 reader_contract.schema.json 逐条写 requirement_id、逐字 source_quote、kind、instruction。objective 中混合的要求按含义拆成 reader_content（应回答的问题与内容范围）、research_method（如何核查与研究）、writing_preference（呈现方式）、manual_assignment（人工分工）；每项原始要求都必须覆盖。Python只校验出处及结构，不能替你判断语义；不得把必答内容改成可选或用方法约束替代正文任务。明确指令有冲突且影响结果时才提问，其余直接执行。
传给Scout的是具体研究任务和对应方法；传给Analyst的是同一份已保存约定、原始要求和证据索引。由你核对归类完整且没有改变用户意思，交接保留同一份约定，后续评价和修订复用。''',
        'scout': '''研究交接：使用本轮已保存的 reader_contract（由任务包或 plan.json 提供），按其中的内容问题与研究方法寻找并核对证据，返回事实、支持范围、来源定位与具体缺口。每条证据是一段连续原文：excerpt 逐字摘录（同一处的几段可用“…”连接），概括、换算与判断写进 facts；locator 用 line 12-18、page 3 或序列化的证据定位 JSON，只指向这段摘录；同一来源的不同段落各写一条。方法限制、抓取失败和研究状态进入结构化研究结果，供主Agent安排补查或调整判断；它们不是要求Analyst复制到正文的段落。''',
        'analyst': '''写作交付：reader_content 决定需要完成的回答，research_method 约束取证和推理，writing_preference 决定呈现，manual_assignment 决定保留的占位。对照原始要求检查解释是否遗漏或扭曲，用户原话优先。研究交接中标为待证、没有已登记来源引用的 learnings 不能直接写成正文事实：先按本轮证据补齐引用；补不齐就丢弃，或作为明确未核验事项记入 gaps，不冒充已核验结论。提交前通读正文，检查事实、判断、图表和读者用途是否连贯，精简重复背景与检查语言；同时确认准确状态、负面事实和必要条件没有被删。''',
        'evaluator': _EVALUATION_RULES,
        'reviewer': _EVALUATION_RULES + '\n只检查已有正文、证据、约定和记录，不自行改稿、编译新约定、重新计算或补搜。需要补查或修改时提交给主Agent。',
        'revision': '''针对性修订：复用被审版本的原始要求和 reader_contract，处理发现及受影响内容。明确错误改正或移除；覆盖不足在剩余权限与预算内补齐，仍未完成则保留未决状态。研究记录归位后通读正文，不能只把问题移到核查记录、追加提醒或删除必要条件来宣称解决。不要新增循环；提交实际修改及对应依据供独立复核。''',
    }[role]
    if role in ('evaluator', 'reviewer'):
        reader = '以下是被审报告的交付标准，用来核对产物；其中补查、改稿等动作由主Agent执行。\n' + reader
    from .length import length_instructions
    parts = [reader, common, role_text, length_instructions(spec)]
    if role in ('analyst', 'revision'):
        from .writing_guidance import ANALYST_GUIDE
        parts.append(ANALYST_GUIDE)
    if spec.get('language') == 'en':
        from .writing_guidance import ENGLISH_REPORT_GUIDE, ENGLISH_RESEARCH_NOTE
        parts.append(ENGLISH_RESEARCH_NOTE if role == 'scout' else
                     ('被审报告的正文语言为英文；按以下规范核对表达和格式，发现与理由仍用中文写。\n' + ENGLISH_REPORT_GUIDE
                      if role in ('evaluator', 'reviewer') else ENGLISH_REPORT_GUIDE))
    if role in ('orchestrator', 'analyst', 'revision'):
        from .execution_timing import instructions as timing_instructions
        parts.append(timing_instructions(spec.get('target_minutes')))
    from .document_workflows import workflow_context
    method = workflow_context(spec.get('workflow_snapshot'), role)
    if method:
        parts.append(method)
    if include_spec:
        # The full frozen asset set is stored in the input artifact. Role prompts
        # contain only their relevant instructions, plus the snapshot identity.
        projection = deepcopy(spec)
        if projection.get('workflow_snapshot'):
            projection['workflow_snapshot'] = {k: v for k, v in projection['workflow_snapshot'].items()
                                               if k not in ('role_instructions', 'methods', 'method_roles')}
        parts.append('本轮产物约定：' + json.dumps(projection, ensure_ascii=False))
    return '\n'.join(parts)


def research_record(store, brief):
    detail = json.loads(brief['detail']); refs = detail.get('citations', [])
    from .plain_isolation import public_notes
    notes = public_notes(detail.get('research_notes', []))
    return {'version_id': brief['id'], 'brief_hash': brief['hash'],
            'notes': notes, 'gaps': detail.get('gaps', []),
            'citations': [{**ref, 'source_name': store.one('sources', ref['source_id'])['name']} for ref in refs],
            'assessments': [json.loads(x['data']) for x in store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC', (brief['id'],))]}


def requirement_items(requirements):
    items = []
    for kind, texts in [('objective', [requirements['objective']]), ('question', requirements.get('key_questions', [])),
                        ('manual', requirements.get('manual_sections', [])), ('writing', requirements.get('writing_preferences', []))]:
        for text in texts:
            identity = 'req_' + hashlib.sha256((kind + '\0' + text).encode()).hexdigest()[:20]
            items.append({'requirement_id': identity, 'text': text, 'mode': 'manual' if kind == 'manual' else 'required',
                          'origin': 'user_requirements', 'kind': kind})
    return items


def save_reader_contract(store, run_id, value):
    requirements=json.loads(store.one('runs',run_id)['requirements'])
    checked=validate_reader_contract(resolve(requirements),value)
    key='reader_contract:'+run_id;old=store.meta(key)
    if old is not None and old!=checked and store.rows('SELECT id FROM briefs WHERE run_id=? LIMIT 1',(run_id,)):
        raise ValueError('这份产物约定已绑定稿件；新要求需建立新的要求/报告版本，不能覆盖历史解释')
    store.set_meta(key,checked)
    return checked


ANALYSIS_CHECKLIST_CANDIDATE = 'chapter-v1'


def analysis_check_instructions(spec):
    """Explicit experiment appendix; ordinary role instructions never include it."""
    return '''章级分析检查候选（仅本轮显式实验，不替代四维评价）：
先判断每章在本轮读者约定中的职责，再检查它是否已有职责所需的判断。不是每个段落、章节或正确事实都必须有 so-what；不按含义句比例、关键词或段落数量评分。
在 assessment.analysis_checks 为每个待检查的章写一项：chapter_quote 是可唯一定位本章的逐字片段（优先包括标题）；requirement_quote 是原始 requirement_items.text 或本章已冻结 sections.purpose 中支持职责判断的连续原话；expectation 为 required / optional / not_required / uncertain；judgment_quote 是本章已有判断的原句，没有则为空；rationale 先解释职责，再说明判断是否已有。
required 仅用于明确要求本章完成影响判断、取舍或观察节点的情况。纯新闻列表、事实说明、资料表格/输入章可为 not_required；职责不明确、要求的适用章不清楚或材料不足以确定判断应存在时为 uncertain，不强迫补写。不能用邻章判断充当本章已完成，也不能把通用“行业重要/值得关注”当具体判断。
即使判断与原句事实都正确，也先看职责；事实章可不写分析。required 且确无判断时才记录缺失；已有判断即逐字给出 judgment_quote，缺少、编造或不匹配的引用不得当检查通过。
套话 filler、重复 restatement、离题 off_topic 属独立表达问题；不要改名为 no_implication，也不要仅因类别名把可选润色升级成必修。保留原四维印象分；不要把任意分析低分自动变成修订要求。
示例（虚构，不能用作报告事实）：要求“数据章列出季度指标”时，“## 数据\n交付 100 台，上季 90 台。”为 not_required，不需强行预测；要求“影响章给出本次扩容决策的观察节点”时，同一事实只有列表而没有判断可为 required 且缺失。已写“交付比上季多 10 台；是否继续扩容，应结合下一季度订单覆盖率核对”时，原句已有有条件的观察节点，不应报缺失；不能据 100/90 台断言需求驱动或未来必然增长。职责不清楚则 uncertain。
有明确 required 缺失时仍须说明材料边界：补写材料支持的取舍/观察节点，或明确相关判断尚不能形成；不要求制造因果或预测。检查范围与不确定性保存在检查记录中。'''


def _analysis_chapter_span(markdown, quote):
    """Locate an exact unique quote and its Markdown chapter; no semantic inference."""
    import re
    if not quote.strip() or markdown.count(quote) != 1:
        return None
    start = markdown.index(quote)
    headings = list(re.finditer(r'^ {0,3}(#{1,6})[ \t]+.*$', markdown, re.MULTILINE))
    preceding = [heading for heading in headings if heading.start() <= start]
    heading = preceding[-1] if preceding else None
    lower = heading.start() if heading else 0
    level = len(heading.group(1)) if heading else 0
    following = [item for item in headings if item.start() > start
                 and (not heading or len(item.group(1)) <= level)]
    upper = following[0].start() if following else len(markdown)
    if start + len(quote) > upper:
        return None
    return lower, upper


def validate_analysis_checks(assessment, spec, markdown):
    """Validate provenance only. The model owns expectation and semantic judgment.

    Returned status cannot be supplied by the model. A missing/ambiguous reference
    remains uncertain, never passed or a compulsory repair. Raw assessment is kept.
    """
    data = assessment if isinstance(assessment, dict) else assessment.model_dump()
    requirement_items = spec.get('requirement_items', [])
    requirements = [item['text'] for item in requirement_items]
    results, spans = [], []
    for index, original in enumerate(data.get('analysis_checks', [])):
        item = original if isinstance(original, dict) else original.model_dump()
        check = {key: item.get(key, '') for key in ('chapter_quote', 'requirement_quote', 'expectation', 'judgment_quote', 'rationale')}
        errors = []
        span = _analysis_chapter_span(markdown, check['chapter_quote'])
        if span is None:
            errors.append('chapter_quote 必须连续逐字存在并可唯一定位一章。')
        # Only a purpose belonging to this quoted chapter may authorize a repair;
        # a neighboring chapter's purpose is not a blanket analysis requirement.
        import re
        chapter_title = re.sub(r'^ {0,3}#{1,6}[ \t]+|[ \t]+#+[ \t]*$', '',
                               markdown[span[0]:span[1]].split('\n', 1)[0]).strip() if span else ''
        chapter_sections = [section for section in spec.get('sections', [])
                            if span and section.get('title') == chapter_title]
        section_requirements = [section.get('purpose', '') for section in chapter_sections]
        quote = check['requirement_quote']
        if quote and not any(quote in text for text in requirements + section_requirements):
            errors.append('requirement_quote 不在原始要求或本章冻结 purpose 中。')
        if check['expectation'] == 'required' and not quote.strip():
            errors.append('required 缺少明确职责要求的原话。')
        if check['expectation'] not in ('required', 'optional', 'not_required', 'uncertain') or not check['rationale'].strip():
            errors.append('缺少有效职责判断或说明。')
        judgment = check['judgment_quote']
        if judgment and (not span or markdown[span[0]:span[1]].count(judgment) != 1):
            errors.append('judgment_quote 不在同一章中连续逐字唯一存在。')
        manual_section = any(section.get('mode') == 'manual' for section in chapter_sections)
        manual_requirement = bool(quote) and bool(chapter_title) and any(quote in requirement['text']
            and chapter_title in requirement['text']
            and requirement.get('kind') in ('manual', 'manual_assignment') for requirement in requirement_items)
        if errors or check['expectation'] == 'uncertain':
            status = 'uncertain'
        elif manual_section or manual_requirement:
            # Frozen human assignment wins over a model's required expectation.
            # Keep the original model fields; only the controller-derived action changes.
            status = 'manual'
        elif check['expectation'] == 'required':
            status = 'judgment_present' if judgment.strip() else 'missing'
        else:
            status = check['expectation']
        results.append({**check, 'id': f'analysis_chapter:{index}', 'status': status,
                        'validation_errors': errors})
        if status == 'manual':
            results[-1]['preservation_note'] = '冻结要求将本章或该条内容留给人工，仅保留占位；模型判断不能赋予自动补写权限。'
        spans.append(span)
    for index, span in enumerate(spans):
        if span and spans.count(span) > 1:
            results[index]['status'] = 'uncertain'
            results[index]['validation_errors'].append('同一章返回多个检查，不能判定职责检查完成。')
    return results


def candidate_assessment(assessment, spec, markdown):
    """Revision-only view; original scores/findings and saved draft remain untouched."""
    data = assessment if isinstance(assessment, dict) else assessment.model_dump()
    checked = validate_analysis_checks(data, spec, markdown)
    result = deepcopy(data)
    result['analysis_checks'] = checked
    result['analysis_check_coverage'] = {
        'status': 'not_checked' if not checked else ('partial' if any(check['status'] == 'uncertain' for check in checked) else 'reported'),
        'reported_chapters': len(checked),
        'uncertain_chapters': sum(check['status'] == 'uncertain' for check in checked),
        'scope': '未返回章级检查即未覆盖；已返回也不代表全部章节覆盖或语义判定通过。'}
    raw = result.get('findings', [])
    result['analysis_check_unverified_findings'] = [finding for finding in raw if finding.get('kind') == 'no_implication']
    result['findings'] = [finding for finding in raw if finding.get('kind') != 'no_implication']
    for check in checked:
        if check['status'] != 'missing':
            continue
        result['findings'].append({'dimension': 'analysis', 'severity': 'minor',
            'kind': 'no_implication', 'description': check['rationale'],
            'report_quote': check['chapter_quote'], 'requirement': check['requirement_quote'],
            'suggestion': '仅在材料支持范围内完成本章明确职责；可用有条件的取舍或观察节点，材料不足则说明相关判断尚不能形成，不编造因果或预测。',
            'check_ids': [check['id']]})
    return result
