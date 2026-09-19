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
            pages on request and submits the assessment for admission.
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

ROLES = ('reviewer', 'evaluator', 'maintainer', 'proposer')


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


def _source_ids(config):
    index = json.loads((Path(config['packet_root']) / 'source-index.json').read_text(encoding='utf-8'))
    return {item.get('source_id') or item.get('id') for item in index['sources']}


def render_pdf_pages(store, config, args):
    from .media import MAX_RENDER_PAGES, render_source_pages
    sid = args.get('source_id')
    if sid not in _source_ids(config):
        raise ToolError('source_id 不在本次任务包的来源清单（source-index.json）中')
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

RUNNER_TOOLS = {'reviewer': [], 'evaluator': EVALUATOR_TOOLS, 'maintainer': MAINTAINER_TOOLS, 'proposer': PROPOSER_TOOLS}


def runner_tool_specs(role):
    return [{key: value for key, value in tool.items() if key != 'handler'} for tool in RUNNER_TOOLS[role]]


def run_tool(store, config, name, args):
    """Execute a runner tool; returns the engine's tool_result payload."""
    tool = next((t for t in RUNNER_TOOLS[role_of(config)] if t['name'] == name), None)
    if tool is None:
        return {'ok': False, 'error': f'本角色没有工具 {name}'}
    try:
        result = tool['handler'](store, config, args if isinstance(args, dict) else {})
    except ToolError as exc:
        return {'ok': False, 'error': str(exc)[:4000]}
    except Exception as exc:
        return {'ok': False, 'error': f'{name} 执行失败：{exc}'[:4000]}
    return {'ok': True, **result}
