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
"""
import base64
import json
import os
from pathlib import Path

ROLES = ('reviewer', 'evaluator')


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

RUNNER_TOOLS = {'reviewer': [], 'evaluator': EVALUATOR_TOOLS}


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
