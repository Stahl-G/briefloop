"""Roles on the BriefLoop native engine and the runner tools each one gets.

The engine has one wire protocol and one kind of session; a role is a system
prompt layer, a set of engine-local packet tools (chosen by the engine by role
name) and the runner tools declared here. A runner tool is described to the
engine by name, guide and JSON Schema, and executed here when the model calls
it, so each role's operations are BriefLoop's own code, not a second copy in
the engine. Users see one backend; roles are internal.

reviewer  - frozen review packet; submit_review is engine-local (review.py
            admits it through the `submit` event).
evaluator - frozen assessment packet (evaluator_packet below); renders PDF
            pages on request and submits the assessment for admission. In
            pairwise mode (learning's gate) it compares trial drafts over a
            comparison packet and submits a comparison instead.
scout     - one research slot of a run. Its packet is the frozen task and
            contracts; the run's sources (which grow as it registers pages)
            are read, searched and rendered through runner tools, and the
            network is reached only through BriefLoop's metered web_search /
            add_url / extract_pages, declared only when the run allows the web.
maintainer, proposer - WikiSkill learning steps. A host-based coordinator
            spawns a subagent per handoff and binds/collects it; here the
            runner does that bookkeeping and the session gets a frozen copy
            of the handoff (learning_packet) plus one submit tool that goes
            through WikiSkill's own collect validation.
"""
import base64
import json
import os
from pathlib import Path

ROLES = ('reviewer', 'evaluator', 'maintainer', 'proposer', 'scout')


def role_of(config):
    role = config.get('native_role') or 'reviewer'
    if role not in ROLES:
        raise ValueError(f'内置引擎不支持的角色：{role}')
    return role


class ToolError(Exception):
    """A rejection the model should read and act on."""


# -- evaluator ------------------------------------------------------------

def _atomic(path, text):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def evaluator_packet(store, input_pack, schema, folder):
    """Freeze what the Evaluator may read into folder/packet.

    The same material the host-based Evaluator reads from absolute paths and
    `briefloop tool` commands, as packet-relative files: the input pack, the
    lightweight index and text of every source of the run (cited or not), the
    report as one line per block, and each report figure's image, data and
    script. Returns the packet path.
    """
    from .figure_text import figure_texts
    from .media import source_files
    from .packet_views import report_text
    packet = Path(folder) / 'packet'
    packet.mkdir(parents=True, exist_ok=True)

    def save(name, blob):
        path = packet / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob if isinstance(blob, bytes) else blob.encode('utf-8'))
        return name

    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=1)
    index = json.loads(Path(input_pack['source_index_path']).read_text(encoding='utf-8'))
    for item in index['sources']:
        sid = item.get('source_id') or item.get('id')
        for key in ('absolute_path', 'original_path', 'image_path', 'rendered_pages'):
            item.pop(key, None)
        try:
            item['text_file'] = save(f'sources/{sid}.txt', store.source_text(sid))
            _, _, original = source_files(store, sid)
            if original and original.suffix == '.xlsx':
                from .workbook_figures import workbook_text
                item['cells_file'] = save(f'sources/{sid}.cells.txt', workbook_text(original.read_bytes()))
            if item.get('media_type') == 'application/pdf':
                item['pdf_pages'] = '需要看页面时用 render_pdf_pages 渲染'
        except (ValueError, OSError) as exc:
            item['read_error'] = str(exc)
    save('source-index.json', dump(index))

    pack = dict(input_pack)
    pack['source_index_path'] = 'source-index.json'
    pack['sources'] = [{**source, 'text_file': f"sources/{source.get('id')}.txt"} for source in pack.get('sources', [])]
    figures, figure_index = [], []
    for figure in pack.get('figures', []):
        figure = dict(figure)
        figure.pop('absolute_image_path', None)
        saved = {}
        for key in ('image_path', 'data_path', 'script_path'):
            if figure.get(key):
                path = (store.root / figure[key]).resolve()
                if path.is_relative_to(store.root) and path.is_file():
                    blob = path.read_bytes()
                    saved[key] = (save(f"figures/{figure['figure_id']}/{path.name}", blob), blob)
                    figure[key] = saved[key][0]
        figures.append(figure)
        script = saved.get('script_path')
        figure_index.append({'figure_id': figure['figure_id'], 'title': figure.get('title'),
                             'image': figure.get('image_path'), 'data_file': figure.get('data_path'),
                             **figure_texts(script[0] if script else None, script[1] if script else b'')})
    pack['figures'] = figures
    if figure_index:
        save('figure-texts.json', dump(figure_index))
    if pack.get('editor_document'):
        save('report.txt', report_text(pack['editor_document']))
    save('input.json', dump(pack))
    save('assessment.schema.json', dump(schema))
    return packet


def _source_ids(store, config):
    if config.get('run_id'):
        return set(store.source_ids(config['run_id']))
    index = json.loads((Path(config['packet_root']) / 'source-index.json').read_text(encoding='utf-8'))
    return {item.get('source_id') or item.get('id') for item in index['sources']}


def render_pdf_pages(store, config, args):
    from .media import MAX_RENDER_PAGES, render_source_pages
    sid = args.get('source_id')
    if sid not in _source_ids(store, config):
        raise ToolError('source_id 不在本次任务的来源清单中')
    try:
        rendered = render_source_pages(store, sid, args.get('pages'))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    content = []
    for page in rendered['pages'][:MAX_RENDER_PAGES]:
        content.append({'type': 'text', 'text': f"来源 {sid} 第 {page['page']} 页（{page['width']}×{page['height']}）："})
        content.append({'type': 'image', 'mimeType': 'image/png',
                        'data': base64.b64encode(Path(page['path']).read_bytes()).decode('ascii')})
    return {'content': content}


def submit_assessment(store, config, args):
    value = args.get('assessment')
    if not isinstance(value, dict):
        raise ToolError('assessment 必须是 JSON 对象（结构见 assessment.schema.json）')
    try:
        store.validate_assessment(config['version_id'], value)
    except Exception as exc:
        raise ToolError(f'评分未通过校验，修正后重新提交：{exc}') from exc
    from .models import missing_findings
    gap = missing_findings(value)
    if gap:
        raise ToolError('评分未通过校验，修正后重新提交：' + gap)
    text = json.dumps(value, ensure_ascii=False, indent=2)
    _atomic(Path(config['packet_root']).parent / 'assessment.json', text)
    return {'content': [{'type': 'text', 'text': '评分已通过校验并保存，本次评价结束。'}], 'settle': json.dumps(value, ensure_ascii=False)}


EVALUATOR_TOOLS = [
    {'name': 'render_pdf_pages', 'label': '渲染 PDF 页面',
     'description': '把本次来源清单中一份 PDF 来源的指定页渲染成图片返回（最多 4 页，页码从 1 开始）。',
     'guide': '把 PDF 来源的指定页渲染成图片返回（一次最多 4 页）。只在核对依赖图表或版面的内容时使用，先用文本定位页码，不要全本渲染。',
     'parameters': {'type': 'object', 'required': ['source_id', 'pages'], 'additionalProperties': False,
                    'properties': {'source_id': {'type': 'string', 'description': 'source-index.json 中的来源 ID'},
                                   'pages': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'minItems': 1, 'maxItems': 4}}},
     'handler': render_pdf_pages},
    {'name': 'submit_assessment', 'label': '提交评分', 'settles': True,
     'description': '提交最终评分对象（结构见 assessment.schema.json）。运行器当场校验结构与 brief_hash，通过即保存并结束本次评价；未通过时按返回的错误修正后重新提交。',
     'guide': '提交最终评分对象（结构见 assessment.schema.json），当场校验，通过即结束本次评价；未通过时按错误修正后重交，不要把 JSON 写进回复正文。',
     'parameters': {'type': 'object', 'required': ['assessment'], 'additionalProperties': False,
                    'properties': {'assessment': {'type': 'object', 'description': '完整评分对象'}}},
     'handler': submit_assessment},
]

# -- learning (WikiSkill maintainer / proposer) ----------------------------

def learning_packet(store, handoff, stage):
    """Freeze one WikiSkill handoff for a native session.

    role.md and the learning context are copied with their file references
    rewritten to packet paths; the current skill, training outputs and
    feedback files are copied beside them, and sources named in feedback
    under the same relative path (sources/<id>.txt) the feedback cites.
    Execution traces stay outside: the context keeps their summaries.
    """
    packet = Path(stage) / 'packet'
    packet.mkdir(parents=True, exist_ok=True)

    def copy(path, name):
        path = Path(path)
        if not path.is_file():
            return None
        target = packet / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        return name

    payload = json.loads(Path(handoff['payload_file']).read_text(encoding='utf-8'))
    copy(handoff['prompt_file'], 'role.md')
    local = {'role': payload['role'], 'role_file': 'role.md',
             'output': '用提交工具交回结果，运行器写入 WikiSkill 输出目录'}
    if payload.get('context_file'):
        context = json.loads(Path(payload['context_file']).read_text(encoding='utf-8'))
        skill = context.get('current_skill')
        if skill and skill.get('file'):
            skill['file'] = copy(skill['file'], 'files/current-skill.md')
        for row in context.get('training_records', []):
            for key in ('output', 'trace'):
                if row.get(key) and row[key].get('file'):
                    row[key]['file'] = copy(row[key]['file'], f"files/records/{row.get('source_id', 'record')}/{key}-{Path(row[key]['file']).name}")
        sources = set()
        for row in context.get('human_feedback', []):
            if row.get('file'):
                row['file'] = copy(row['file'], f"files/feedback/{Path(row['file']).name}")
            try:
                value = json.loads(row.get('text') or '{}')
            except ValueError:
                continue
            for source in value.get('sources', []) if isinstance(value, dict) else []:
                if isinstance(source, dict) and source.get('id'):
                    sources.add(source['id'])
        for sid in sorted(sources):
            try:
                (packet / 'sources').mkdir(exist_ok=True)
                (packet / 'sources' / f'{sid}.txt').write_text(store.source_text(sid), encoding='utf-8')
            except (ValueError, OSError):
                pass
        context['packet_note'] = ('文件路径均为任务包内相对路径；反馈中 sources[].path 指向的来源正文同在 sources/ 下。'
                                  '执行记录中的 trace_file 没有复制进任务包，只能依据其中已有的摘要，不能声称读过轨迹。')
        (packet / 'learning-context.json').write_text(json.dumps(context, ensure_ascii=False, indent=1), encoding='utf-8')
        local['context_file'] = 'learning-context.json'
    if payload.get('task'):
        local['task'] = payload['task']
    (packet / 'payload.json').write_text(json.dumps(local, ensure_ascii=False, indent=1), encoding='utf-8')
    return packet


def agent_id(session_id):
    return f'briefloop-native:{session_id}'


def bind_session(config, session_id):
    """WikiSkill's protocol: record the child's actual handle as soon as it
    exists, before it runs, so an interrupted step resumes that same child.
    Each handoff gets its own engine session, so the handle is unique.

    The runtime recorded is the study's host (briefloop-native for studies
    started on this engine; an older study keeps the tag it began with)."""
    if role_of(config) not in ('maintainer', 'proposer'):
        return
    from wikiskill import native_agents
    from wikiskill import product
    request = product._load(Path(config['study']).resolve())['requests'][config['request_id']]
    delegation = request.get('delegation')
    if delegation:
        if delegation.get('agent_id') != agent_id(session_id):
            raise ValueError('该学习请求已绑定到另一个子会话，不能换会话续跑')
        return
    from .learning import study_runtime
    native_agents.bind(config['study'], config['request_id'], agent_id(session_id), study_runtime(config['study']), 'fresh')


def _collect(config, write):
    """Write result.json into the handoff's output directory and let WikiSkill
    validate and record it; its rejection goes back to the model."""
    from wikiskill import native_agents
    from wikiskill import product
    study, request_id = config['study'], config['request_id']
    request = product._load(Path(study).resolve())['requests'][request_id]
    if not request.get('delegation'):
        raise ToolError('本学习请求尚未绑定子会话，运行器不能提交')
    write(Path(request['handoff']['output_directory']))
    try:
        native_agents.collect(study, request_id)
    except ValueError as exc:
        raise ToolError(f'WikiSkill 未接受本次提交，修正后重新提交：{exc}') from exc


def submit_patterns(store, config, args):
    patterns = args.get('patterns')
    if not isinstance(patterns, list):
        raise ToolError('patterns 必须是列表')
    _collect(config, lambda output: _atomic(output / 'result.json', json.dumps({'patterns': patterns}, ensure_ascii=False, indent=1)))
    return {'content': [{'type': 'text', 'text': f'已登记 {len(patterns)} 条经验，本步骤结束。'}],
            'settle': json.dumps({'patterns': patterns}, ensure_ascii=False)}


def submit_proposal(store, config, args):
    note = args.get('note') or ''
    if args.get('no_action') is True:
        if args.get('skill'):
            raise ToolError('no_action 与 skill 只能二选一')
        result = {'no_action': True, 'note': note}
        _collect(config, lambda output: _atomic(output / 'result.json', json.dumps(result, ensure_ascii=False)))
    else:
        skill = args.get('skill')
        if not isinstance(skill, str) or not skill.strip():
            raise ToolError('skill 需要完整的候选技能 Markdown 正文；不提议修改时用 no_action')

        def write(output):
            _atomic(output / 'SKILL.md', skill)
            _atomic(output / 'result.json', json.dumps({'skill': str(output / 'SKILL.md'), 'note': note}, ensure_ascii=False))
        _collect(config, write)
        result = {'skill_chars': len(skill), 'note': note}
    return {'content': [{'type': 'text', 'text': '提议已提交，本步骤结束。'}], 'settle': json.dumps(result, ensure_ascii=False)}


MAINTAINER_TOOLS = [
    {'name': 'submit_patterns', 'label': '提交经验', 'settles': True,
     'description': '提交整理出的经验 patterns（每条 name、content、sources）。sources 只能引用学习上下文给出的训练记录、反馈或已有经验 ID；WikiSkill 当场校验，通过即结束本步骤，未通过时按错误修正后重交。',
     'guide': '提交经验列表 [{name, content, sources}]，sources 只能引用上下文中的 ID；当场校验，通过即结束本步骤。没有有依据的经验时提交空列表。',
     'parameters': {'type': 'object', 'required': ['patterns'], 'additionalProperties': False,
                    'properties': {'patterns': {'type': 'array', 'items': {
                        'type': 'object', 'required': ['name', 'content', 'sources'],
                        'properties': {'name': {'type': 'string'}, 'content': {'type': 'string'},
                                       'sources': {'type': 'array', 'items': {'type': 'string'}}}}}}},
     'handler': submit_patterns},
]

PROPOSER_TOOLS = [
    {'name': 'submit_proposal', 'label': '提交技能提议', 'settles': True,
     'description': '提交一份完整的候选技能（Markdown 正文，含名称、说明、适用与不适用条件、具体做法）和修改说明；认为不应修改时提交 no_action=true 并说明理由。通过即结束本步骤。',
     'guide': '提交完整候选技能 Markdown（skill）与修改说明（note），或 no_action=true 加理由；通过即结束本步骤。',
     'parameters': {'type': 'object', 'required': ['note'], 'additionalProperties': False,
                    'properties': {'skill': {'type': 'string', 'description': '完整候选技能 Markdown'},
                                   'note': {'type': 'string', 'description': '改了什么、为什么'},
                                   'no_action': {'type': 'boolean'}}},
     'handler': submit_proposal},
]

# -- pairwise Evaluator (learning's gate) -----------------------------------

def comparison_packet(store, comparisons, folder):
    """Freeze a learning comparison: the input as written for every host, each
    case's two drafts as plain text, and the text of the sources the cases use."""
    packet = Path(folder) / 'packet'
    packet.mkdir(parents=True, exist_ok=True)
    (packet / 'input.json').write_text(json.dumps(comparisons, ensure_ascii=False, indent=1), encoding='utf-8')
    sources = set()
    for case in comparisons:
        directory = packet / 'cases' / case['case_id']
        directory.mkdir(parents=True, exist_ok=True)
        for side in ('baseline', 'candidate'):
            (directory / f'{side}.md').write_text(case[side].get('markdown') or '', encoding='utf-8')
        sources.update(case.get('source_ids') or [])
    for sid in sorted(sources):
        try:
            (packet / 'sources').mkdir(exist_ok=True)
            (packet / 'sources' / f'{sid}.txt').write_text(store.source_text(sid), encoding='utf-8')
        except (ValueError, OSError):
            pass
    return packet


def submit_comparison(store, config, args):
    from .learning import comparison_errors
    packet = Path(config['packet_root'])
    comparisons = json.loads((packet / 'input.json').read_text(encoding='utf-8'))
    result = {'pairs': args.get('pairs'), 'reason': args.get('reason') or ''}
    error = comparison_errors(result, comparisons)
    if error:
        raise ToolError('比较结果未通过校验，修正后重新提交：' + error)
    _atomic(packet.parent / 'comparison.json', json.dumps(result, ensure_ascii=False, indent=1))
    return {'content': [{'type': 'text', 'text': '比较结果已通过校验并保存，本次比较结束。'}], 'settle': json.dumps(result, ensure_ascii=False)}


COMPARISON_TOOLS = [
    {'name': 'submit_comparison', 'label': '提交比较结果', 'settles': True,
     'description': '提交成对比较结果：pairs 对每个案例各一条（case_id、verdict=better/tie/worse、reason、regressions 列表，有明确要求时加 requirement_checks），reason 为整体说明。运行器当场校验，通过即保存并结束。',
     'guide': '提交比较结果 {pairs:[{case_id, verdict, reason, regressions, requirement_checks?}], reason}；当场校验，通过即结束本次比较。',
     'parameters': {'type': 'object', 'required': ['pairs', 'reason'], 'additionalProperties': False,
                    'properties': {'pairs': {'type': 'array', 'items': {'type': 'object'}},
                                   'reason': {'type': 'string'}}},
     'handler': submit_comparison},
]

# -- scout ------------------------------------------------------------------

READ_CHARS = 24_000


def scout_packet(store, task, folder):
    """Freeze one Scout slot's task into folder/packet: the assignment, the
    scout contract, the saved reader contract, the search policy in tool
    words, the role skill and the index of sources registered so far."""
    packet = Path(folder) / 'packet'
    packet.mkdir(parents=True, exist_ok=True)
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=1)
    (packet / 'task.json').write_text(dump({key: task[key] for key in (
        'slot_id', 'assignment', 'period', 'time_context', 'created', 'allow_web',
        'search_channels', 'budget', 'research_handoff') if key in task}), encoding='utf-8')
    (packet / 'scout-contract.md').write_text(task['contract'], encoding='utf-8')
    (packet / 'reader-contract.json').write_text(dump(task.get('reader_contract')), encoding='utf-8')
    (packet / 'search-policy.md').write_text(task['search_note'], encoding='utf-8')
    if task.get('skill'):
        (packet / 'skill.md').write_text(task['skill'], encoding='utf-8')
    (packet / 'source-index.json').write_text(dump(task['sources']), encoding='utf-8')
    from .models import ScoutResult
    (packet / 'scout.schema.json').write_text(dump(ScoutResult.model_json_schema()), encoding='utf-8')
    return packet


def _run_source(store, config, sid):
    if not isinstance(sid, str) or sid not in store.source_ids(config['run_id']):
        raise ToolError('source_id 不是本轮已登记的来源；先用 add_url 登记，或查看 source-index.json')
    return sid


def source_read(store, config, args):
    from .scout_tools import read_source
    sid = _run_source(store, config, args.get('source_id'))
    start, end = args.get('start_line'), args.get('end_line')
    limit = min(int(args.get('max_chars') or READ_CHARS), READ_CHARS)
    try:
        offset = args.get('start_char')
        if offset is not None:
            lines = store.source_text(sid).splitlines(); number = start or 1
            if (type(offset) is not int or offset < 0 or number < 1 or number > len(lines)
                    or offset >= len(lines[number-1]) or (end is not None and end != number)):
                raise ValueError('start_char 从 0 开始，须在 start_line 的单行范围内；end_line 若填写须与 start_line 相同')
            line = lines[number-1]; stop = min(len(line), offset + limit)
            text = (f'[来源 {sid}：第 {number} 行，共 {len(line)} 字符；显示字符 {offset}:{stop}（从 0 开始、不含结束）；'
                    + (f'继续读取 start_line={number}, start_char={stop}' if stop < len(line) else '该行已读至结尾')
                    + f']\n{number}: ' + line[offset:stop])
        else:
            text = read_source(store, sid, start_line=start or 1, end_line=end, max_chars=limit)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    text = 'source_hash: ' + store.one('sources', sid)['hash'] + '\n' + text
    return {'content': [{'type': 'text', 'text': text}]}


def source_grep(store, config, args):
    import re
    pattern = args.get('pattern')
    if not isinstance(pattern, str) or not pattern.strip():
        raise ToolError('pattern 不能为空')
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    ids = [_run_source(store, config, args['source_id'])] if args.get('source_id') else store.source_ids(config['run_id'])
    limit = min(int(args.get('max_matches') or 40), 100)
    hits = []
    for sid in ids:
        try:
            lines = store.source_text(sid).splitlines()
        except (ValueError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            match = regex.search(line)
            if match:
                offset = max(0, match.start()-100)
                hits.append(f'{sid} {number} (start_char={offset}): {line[offset:offset+300]}')
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    text = '\n'.join(hits) if hits else '没有匹配。'
    return {'content': [{'type': 'text', 'text': text + (f'\n（已达 {limit} 条上限，缩小范围再查）' if len(hits) >= limit else '')}]}


def _json_result(value):
    return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}


def web_search(store, config, args):
    from . import websearch
    provider = args.get('provider')
    if provider not in (config.get('search_channels') or []):
        raise ToolError('provider 不在本轮允许的受控渠道内：' + ','.join(config.get('search_channels') or []))
    try:
        result = websearch.search(
            args.get('query') or '', provider=provider, purpose=args.get('purpose') or 'primary',
            reason=args.get('reason') or '', gap_id=args.get('gap_id'), topic=args.get('topic') or 'general',
            time_range=args.get('time_range'), start_date=args.get('start_date'), end_date=args.get('end_date'),
            include_domains=args.get('include_domains'), exclude_domains=args.get('exclude_domains'),
            max_results=args.get('max_results') or 5, search_depth=args.get('search_depth') or 'basic',
            store=store, run_id=config['run_id'])
    except websearch.SearchError as exc:
        from .cli import _search_failure
        return _json_result(_search_failure('search', exc, provider))
    keep = ('status', 'provider', 'query', 'results', 'remaining', 'unadmitted_urls', 'message', 'note', 'failure_kind', 'error')
    return _json_result({key: result[key] for key in keep if key in result})


def _source_summary(store, source):
    summary = {key: source.get(key) for key in ('id', 'name', 'url', 'status', 'error', 'reused', 'media_type')}
    summary['source_id'] = summary.pop('id')
    if source.get('status') == 'ready':
        try:
            summary['lines'] = len(store.source_text(source['id']).splitlines())
        except (ValueError, OSError):
            pass
    return summary


def add_url(store, config, args):
    from .sources import fetch_for_run
    url = args.get('url')
    if not isinstance(url, str) or not url.strip():
        raise ToolError('url 不能为空')
    try:
        result = fetch_for_run(store, config['run_id'], url)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not result.get('id'):
        return _json_result(result)  # budget_exhausted
    summary = _source_summary(store, result)
    summary['remaining'] = (result.get('budget') or {}).get('remaining')
    return _json_result(summary)


def extract_pages(store, config, args):
    from . import websearch
    urls = args.get('urls')
    if not isinstance(urls, list) or not urls:
        raise ToolError('urls 需要非空列表')
    try:
        result = websearch.extract(store, urls, run_id=config['run_id'], provider='tavily')
    except websearch.SearchError as exc:
        from .cli import _search_failure
        return _json_result(_search_failure('extract', exc))
    if isinstance(result, dict) and isinstance(result.get('sources'), list):
        result = {**result, 'sources': [_source_summary(store, source) for source in result['sources']]}
    return _json_result(result)


def submit_scout_result(store, config, args):
    from pydantic import ValidationError
    from .models import ScoutResult
    from .scout_tools import evidence_errors, join_scouts
    from .scout_evidence import collect
    try:
        if 'sources' in args:
            raise ValueError('请先用 record_evidence 逐条记录；最终只提交 gaps/search_summary/retrieval_notes')
        recorded, discarded = collect(store, config)
        data = {key: args[key] for key in ('gaps', 'search_summary', 'retrieval_notes') if key in args}
        data['sources'] = recorded
        if not recorded and not data.get('gaps'):
            raise ValueError('没有证据时须说明具体缺口，不能空提交')
        if discarded:
            data['gaps'] = list(data.get('gaps', [])) + [f'未采用证据 {eid}：{reason}' for eid, reason in discarded.items()]
        result = ScoutResult.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise ToolError(str(exc)[:1500]) from exc
    errors = evidence_errors(store, config['run_id'], result)
    if errors:
        raise ToolError('研究结果未通过证据校验，修正后重新提交：\n' + '\n'.join(errors[:20]))
    target = Path(config['result_file'])
    text = json.dumps(result.model_dump(), ensure_ascii=False, indent=1)
    _atomic(target, text)
    try:
        join_scouts(store, [target], run_id=config['run_id'])
    except ValueError as exc:
        target.unlink(missing_ok=True)
        raise ToolError('研究结果未通过合并校验，修正后重新提交：' + str(exc)) from exc
    return {'content': [{'type': 'text', 'text': f'研究结果已保存（{len(result.sources)} 条来源，{len(result.gaps)} 个缺口），本槽位结束。'}],
            'settle': json.dumps({'sources': len(result.sources), 'gaps': len(result.gaps)}, ensure_ascii=False)}


_DATE = {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}
SCOUT_READ_TOOLS = [
    {'name': 'source_read', 'label': '读取来源',
     'description': '读取一份本轮已登记来源的正文，带行号；一次最多 24000 字符，可用 start_line/end_line 定位。',
     'guide': '读取本轮已登记来源的正文（带行号，单次最多 24000 字符）；用返回的 source_hash、行号和短原文锚点调用 record_evidence；原文摘录由运行器截取。',
     'parameters': {'type': 'object', 'required': ['source_id'], 'additionalProperties': False,
                    'properties': {'source_id': {'type': 'string'},
                                   'start_line': {'type': 'integer', 'minimum': 1},
                                   'start_char': {'type': 'integer', 'minimum': 0, 'description': '超长单行的字符偏移，从 0 开始；只读取 start_line 这一行'},
                                   'end_line': {'type': 'integer', 'minimum': 1},
                                   'max_chars': {'type': 'integer', 'minimum': 1}}},
     'handler': source_read},
    {'name': 'source_grep', 'label': '搜索来源',
     'description': '在本轮已登记来源（或指定一份）中按正则或关键词查找，返回来源 ID、行号和该行。',
     'guide': '在已登记来源中按关键词或正则定位行号，再用 source_read 读取上下文；不要只凭匹配行下结论。',
     'parameters': {'type': 'object', 'required': ['pattern'], 'additionalProperties': False,
                    'properties': {'pattern': {'type': 'string'}, 'source_id': {'type': 'string'},
                                   'max_matches': {'type': 'integer', 'minimum': 1, 'maximum': 100}}},
     'handler': source_grep},
    {**EVALUATOR_TOOLS[0], 'description': '把本轮一份已登记 PDF 来源的指定页渲染成图片返回（最多 4 页，页码从 1 开始）。'},
]


def _scout_web_tools(channels):
    tools = [
        {'name': 'web_search', 'label': '联网搜索',
         'description': 'BriefLoop 受控搜索：计入本轮共享研究预算，失败也计次。只返回候选链接和摘要，摘要不是正文证据。',
         'guide': '受控搜索，计入所有 Scout 共享的硬预算；结果只是线索，要用 add_url 保存正文后才能引用。出现 budget_exhausted 时停止新增检索。',
         'parameters': {'type': 'object', 'required': ['provider', 'query', 'purpose', 'reason'], 'additionalProperties': False,
                        'properties': {'provider': {'type': 'string', 'enum': list(channels)},
                                       'query': {'type': 'string'},
                                       'purpose': {'type': 'string', 'enum': ['primary', 'coverage_probe', 'gap_repair']},
                                       'reason': {'type': 'string', 'description': '这次想多知道什么'},
                                       'gap_id': {'type': 'string'},
                                       'topic': {'type': 'string', 'enum': ['general', 'news']},
                                       'time_range': {'type': 'string', 'enum': ['day', 'week', 'month', 'year']},
                                       'start_date': _DATE, 'end_date': _DATE,
                                       'include_domains': {'type': 'array', 'items': {'type': 'string'}},
                                       'exclude_domains': {'type': 'array', 'items': {'type': 'string'}},
                                       'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                                       'search_depth': {'type': 'string', 'enum': ['basic', 'advanced']}}},
         'handler': web_search},
        {'name': 'add_url', 'label': '保存网页',
         'description': '抓取一个公开 URL，保存原件、抽取正文并登记为本轮来源，返回 source_id、状态和行数；计入正文页预算，同一 URL 复用已有登记。',
         'guide': '把候选 URL 保存为本轮来源并取得 source_id（计入正文页预算）；status 不是 ready 时正文不可用，换路径或记缺口。',
         'parameters': {'type': 'object', 'required': ['url'], 'additionalProperties': False,
                        'properties': {'url': {'type': 'string'}}},
         'handler': add_url},
    ]
    if 'tavily' in channels:
        tools.append({'name': 'extract_pages', 'label': '提取网页',
                      'description': '用 Tavily Extract 提取 add_url 抓取失败的页面并登记为本轮来源；提取结果不是网站原始字节。',
                      'guide': '只在 add_url 失败时用 Tavily 提取页面正文（计入预算）。',
                      'parameters': {'type': 'object', 'required': ['urls'], 'additionalProperties': False,
                                     'properties': {'urls': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 5}}},
                      'handler': extract_pages})
    return tools


def record_evidence(store, config, args):
    from .scout_evidence import record
    return _json_result(record(store, config, args))


SCOUT_RECORD = {
    'name': 'record_evidence', 'label': '记录证据', 'sequential': True,
    'description': '读完相关段落立即记录：运行器按 source_hash、行段及短原文锚点截取 excerpt。稳定 id 用于单条修正或移除；通过的证据保留，只重交 rejected 项。',
    'guide': '随读随记，互不依赖的条目可批量提交。quote 是 8–240 字符连续逐字锚点；source_hash 取 source_read 返回值。检查返回摘录是否包含单位、表头和脚注；relocated=true 时核对新行段上下文。长单行可用 start_char/end_char 明确选取所需片段（从 0 开始、不含结束字符，最多 4000 字符），不要切掉关键限定。不再整份抄写 excerpt。',
    'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
        'items': {'type': 'array', 'maxItems': 16, 'items': {'type': 'object',
            'required': ['id', 'source_id', 'source_hash', 'locator', 'quote', 'facts', 'coverage_status'],
            'additionalProperties': False, 'properties': {
                'id': {'type': 'string'}, 'source_id': {'type': 'string'}, 'source_hash': {'type': 'string'},
                'locator': {'type': 'string'}, 'quote': {'type': 'string'},
                'start_char': {'type': 'integer', 'minimum': 0, 'description': '可选：长单行摘录起点，从 0 开始；需同时给 end_char'},
                'end_char': {'type': 'integer', 'minimum': 1, 'description': '可选：长单行摘录终点，不含结束字符；摘录最多 4000 字符'},
                'facts': {'type': 'array', 'items': {'type': 'string'}},
                'conflicts': {'type': 'array', 'items': {'type': 'string'}},
                'coverage_status': {'type': 'string'},
                'claim_ids': {'type': 'array', 'items': {'type': 'string'}}}}},
        'discard': {'type': 'array', 'maxItems': 16, 'items': {'type': 'object',
            'required': ['id', 'reason'], 'additionalProperties': False,
            'properties': {'id': {'type': 'string'}, 'reason': {'type': 'string'}}}}}},
    'handler': record_evidence,
}

SCOUT_SUBMIT = {
    'name': 'submit_scout_result', 'label': '提交研究结果', 'settles': True,
    'description': '结束本槽位，只交 gaps、search_summary、retrieval_notes。运行器合并本次 record_evidence 已保存的证据，校验后落盘为原有 ScoutResult 并结束。',
    'guide': '确认所有证据已记录且 pending_ids 为空；不要再交 sources 或重复摘录。缺失内容写进 gaps；研究状态与覆盖不等于事实真实性已核实。',
    'parameters': {'type': 'object', 'required': ['gaps'], 'additionalProperties': False,
                   'properties': {'gaps': {'type': 'array', 'items': {'type': 'string'}},
                                  'search_summary': {'type': 'string'},
                                  'retrieval_notes': {'type': 'array', 'items': {'type': 'object'}}}},
    'handler': submit_scout_result,
}


def _scout_tools(config):
    channels = config.get('search_channels') or []
    web = _scout_web_tools(channels) if config.get('allow_web') else []
    if config.get('allow_web') and not channels:
        web = [tool for tool in web if tool['name'] == 'add_url']
    return [*SCOUT_READ_TOOLS, *web, SCOUT_RECORD, SCOUT_SUBMIT]


RUNNER_TOOLS = {'reviewer': [], 'evaluator': EVALUATOR_TOOLS, 'maintainer': MAINTAINER_TOOLS, 'proposer': PROPOSER_TOOLS}


def _tools(role, mode=None, config=None):
    if role == 'evaluator' and mode == 'pairwise':
        return COMPARISON_TOOLS
    if role == 'scout':
        return _scout_tools(config or {})
    return RUNNER_TOOLS[role]


def runner_tool_specs(role, mode=None, config=None):
    return [{key: value for key, value in tool.items() if key != 'handler'} for tool in _tools(role, mode, config)]


def run_tool(store, config, name, args):
    """Execute a runner tool; returns the engine's tool_result payload."""
    tool = next((t for t in _tools(role_of(config), config.get('evaluation_mode'), config) if t['name'] == name), None)
    if tool is None:
        return {'ok': False, 'error': f'本角色没有工具 {name}'}
    try:
        result = tool['handler'](store, config, args if isinstance(args, dict) else {})
    except ToolError as exc:
        return {'ok': False, 'error': str(exc)[:4000]}
    except Exception as exc:
        return {'ok': False, 'error': f'{name} 执行失败：{exc}'[:4000]}
    return {'ok': True, **result}
