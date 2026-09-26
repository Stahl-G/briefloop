"""OpenCode 2.x transport projected onto BriefLoop's existing conversation port.

Verified against the official v2.0.14 OpenAPI (tag 08462140). This module
never uses v1 fallbacks or writes native configuration/authentication files.
"""
from pathlib import Path
import urllib.parse


def _error(message, status=None):
    from .opencode_server import OpencodeError
    return OpencodeError(message, status=status)


def _data(value, kind=dict):
    data = value.get('data') if isinstance(value, dict) else None
    if not isinstance(data, kind):
        raise _error('OpenCode v2 返回了无法识别的协议结构')
    return data


def _query(values):
    return '?' + urllib.parse.urlencode(values) if values else ''


def permission_rules(rules):
    """v2 renamed bash to shell; edit still guards write/edit/patch together."""
    result = []
    supported = {'*', 'read', 'edit', 'bash', 'external_directory', 'question',
                 'plan_enter', 'plan_exit', 'webfetch', 'websearch'}
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {'permission', 'action', 'pattern'}
                or rule['permission'] not in supported or rule['action'] not in ('allow', 'deny', 'ask')
                or not isinstance(rule['pattern'], str)):
            raise _error('OpenCode v2 不支持本轮权限规则；已停止，未降级执行')
        result.append({'action': 'shell' if rule['permission'] == 'bash' else rule['permission'],
                       'resource': rule['pattern'], 'effect': rule['action']})
    # v2 exposes this mutation through CodeMode without a file permission
    # assertion. BriefLoop binds each conversation to an explicit directory.
    result.append({'action': 'opencode_session_move', 'resource': '*', 'effect': 'deny'})
    return result


def _model(value, variant=None):
    from .opencode_server import model_ref
    if isinstance(value, str):
        return model_ref(value, variant)
    if not isinstance(value, dict) or not value.get('providerID') or not (value.get('id') or value.get('modelID')):
        raise _error('OpenCode v2 模型必须包含 providerID 与模型 ID')
    result = {'providerID': value['providerID'], 'id': value.get('id') or value['modelID']}
    choice = variant if variant is not None else value.get('variant')
    if choice:
        result['variant'] = choice
    return result


class V2:
    def __init__(self, client):
        self.client = client

    def request(self, method, path, body=None):
        try:
            return self.client._request(method, path, body)
        except Exception as exc:
            if getattr(exc, 'status', None) == 404 and '/session/' in path:
                raise _error('OpenCode v2 会话不存在；请在 OpenCode 中完成旧会话迁移，或明确新建对话。未自动重建或重发。', 404) from None
            raise

    @staticmethod
    def path(sid, suffix=''):
        return '/api/session/' + urllib.parse.quote(sid, safe='') + suffix

    def session(self, sid, directory=None):
        session = _data(self.request('GET', self.path(sid)))
        if directory is not None:
            actual = (session.get('location') or {}).get('directory')
            if not actual or Path(actual).resolve() != Path(directory).resolve():
                raise _error('OpenCode v2 会话工作目录与本任务不一致；已停止')
        return session

    def validate_model(self, ref, directory):
        catalog = self.providers(directory)
        provider = next((p for p in catalog['providers'] if p['id'] == ref['providerID']), None)
        model = (provider or {}).get('models', {}).get(ref['id'])
        if model is None:
            raise _error('OpenCode v2 当前目录未提供所选模型；请在 OpenCode 中配置并刷新目录，未改用默认模型')
        if ref.get('variant') and ref['variant'] not in (model.get('variants') or []):
            raise _error('OpenCode v2 所选模型未公布该推理档位；未改用默认档位')

    def create_session(self, title, *, agent='build', model=None, permission=None, directory=None, require_permissions=False):
        if directory is None:
            raise _error('OpenCode v2 会话必须明确绑定工作目录')
        body = {'title': title, 'location': {'directory': str(Path(directory).resolve())}}
        if agent:
            body['agent'] = agent
        if model:
            body['model'] = _model(model)
            self.validate_model(body['model'], directory)
        if permission is not None:
            body['permissions'] = permission_rules(permission)
        elif require_permissions:
            raise _error('OpenCode v2 缺少本轮权限规则；未创建会话')
        value = _data(self.request('POST', '/api/session', body))
        # An accepted HTTP body alone is insufficient proof of preserved settings.
        saved = self.session(value['id'], directory)
        for key in ('model', 'permissions', 'agent'):
            if key in body and saved.get(key) != body[key]:
                raise _error('OpenCode v2 未保留本轮 ' + key + ' 设置；未发送任务')
        return saved

    def prompt_async(self, sid, text, *, model=None, variant=None, agent='build', files=None, system=None, directory=None):
        session = self.session(sid, directory)
        instruction_path = '/api/experimental/session/' + urllib.parse.quote(sid, safe='') + '/instructions/entries'
        entries = _data(self.request('GET', instruction_path), list)
        current = next((entry for entry in entries if entry.get('key') == 'briefloop.system'), None)
        changed = current is not None if system is None else current is None or current.get('value') != system
        if changed:
            # v2 keeps its initial system context in history. Later InstructionEntry
            # updates reach some providers as user messages, so they cannot replace
            # v1's per-prompt system contract. Reject before *any* session mutation.
            users = _data(self.request('GET', self.path(sid, '/message') + _query({'type': 'user', 'limit': '1'})), list)
            if users:
                raise _error('OpenCode v2 已开始的会话不能更换系统约定；模型、联网、日期或工作区设定变化时请明确新建对话。已有会话和设置保留，未发送本轮任务。')
        if model:
            ref = _model(model, variant)
            self.validate_model(ref, directory)
            if session.get('model') != ref:
                self.request('POST', self.path(sid, '/model'), {'model': ref})
                if self.session(sid, directory).get('model') != ref:
                    raise _error('OpenCode v2 未保留所选模型或推理档位；未发送任务')
        elif variant:
            raise _error('OpenCode v2 推理档位必须绑定明确模型；未发送任务')
        if agent and session.get('agent') != agent:
            self.request('POST', self.path(sid, '/agent'), {'agent': agent})
            if self.session(sid, directory).get('agent') != agent:
                raise _error('OpenCode v2 未保留所选 Agent；未发送任务')
        if changed and system is not None:
            self.request('PUT', instruction_path + '/briefloop.system', {'value': system})
        elif changed:
            # Only a session with no admitted user turn may clear/replace an entry.
            self.request('DELETE', instruction_path + '/briefloop.system')
        attachments = []
        for item in files or []:
            if not isinstance(item.get('url'), str) or not item['url']:
                raise _error('OpenCode v2 附件缺少可读取 URI')
            attachments.append({'uri': item['url'], 'name': item.get('filename', 'image')})
        return _data(self.request('POST', self.path(sid, '/prompt'), {'text': text, **({'files': attachments} if attachments else {})}))

    def pages(self, path, query=None):
        result, seen = [], set()
        query = dict(query or {})
        for _ in range(100):
            page = self.request('GET', path + _query(query))
            result.extend(_data(page, list))
            cursor = (page.get('cursor') or {}).get('next')
            if not cursor:
                return result
            if not isinstance(cursor, str) or cursor in seen:
                raise _error('OpenCode v2 分页游标重复或无效；未返回不完整结果')
            seen.add(cursor)
            query = {'cursor': cursor}
        raise _error('OpenCode v2 会话记录超过分页限制；未静默截断')

    def messages(self, sid, directory=None):
        self.session(sid, directory)
        items = self.pages(self.path(sid, '/message'), {'order': 'asc', 'limit': '100'})
        result = []
        turn_assistant = None
        for item in items:
            kind = item.get('type')
            if kind not in ('user', 'assistant', 'idle'):
                continue
            if kind == 'idle':
                # Idle failure can exist without a completed assistant message.
                # Preserve existing public content and expose a terminal failure.
                if item.get('outcome') in ('failed', 'interrupted'):
                    previous = turn_assistant
                    info = {'id': item['id'], 'role': 'assistant', 'time': {**item.get('time', {}), 'completed': item.get('time', {}).get('created')},
                            'finish': 'error', 'error': {'message': 'OpenCode v2 本轮执行' + ('被中断' if item['outcome'] == 'interrupted' else '失败')}}
                    if previous and previous['info'].get('error'):
                        info['error'] = previous['info']['error']
                    result.append({'info': info, 'parts': previous['parts'] if previous else []})
                turn_assistant = None
                continue
            info = {'id': item['id'], 'sessionID': sid, 'role': kind, 'time': item.get('time', {})}
            for key in ('finish', 'tokens', 'cost', 'model', 'retry'):
                if key in item:
                    info[key] = item[key]
            if item.get('error'):
                info['error'] = self.error(item['error'])
            parts = []
            if kind == 'user':
                turn_assistant = None
                parts.append({'type': 'text', 'text': item.get('text', '')})
            for part in item.get('content', []):
                if part.get('type') in ('text', 'reasoning'):
                    parts.append({'type': part['type'], 'text': part.get('text', '')})
                elif part.get('type') == 'tool':
                    state = dict(part.get('state') or {})
                    content = state.get('content') or []
                    if content:
                        state['output'] = '\n'.join(c.get('text', '') for c in content if c.get('type') == 'text')
                    if state.get('error'):
                        state['error'] = self.error(state['error'])
                    state['time'] = part.get('time', {})
                    parts.append({'type': 'tool', 'id': part['id'], 'callID': part['id'], 'tool': part['name'], 'state': state})
            message = {'info': info, 'parts': parts}
            result.append(message)
            if kind == 'assistant':
                turn_assistant = message
        return result

    @staticmethod
    def error(value):
        return {'name': value.get('type', 'Error'), 'message': value.get('message', '执行失败'),
                **({'data': {'statusCode': value['status']}} if isinstance(value.get('status'), int) else {})}

    def session_status(self, sid, directory=None):
        self.session(sid, directory)
        active = _data(self.request('GET', '/api/session/active'))
        return {'type': 'busy' if sid in active else 'idle'}

    def abort(self, sid, directory=None):
        self.session(sid, directory)
        return self.request('POST', self.path(sid, '/interrupt'))

    def children(self, sid, directory=None):
        self.session(sid, directory)
        return self.pages('/api/session', {'parentID': sid, 'order': 'asc', 'limit': '100'})

    def paths(self, directory):
        value = self.request('GET', '/api/location' + _query({'location[directory]': str(directory)}))
        project = value.get('project') or {}
        if not value.get('directory') or not project.get('directory'):
            raise _error('OpenCode v2 没有返回有效工作区位置')
        # v2 FileAccess permission resources are relative to location, not Git root.
        return {'directory': value['directory'], 'worktree': value['directory']}

    def providers(self, directory=None):
        query = _query({'location[directory]': str(directory)}) if directory is not None else ''
        providers = _data(self.request('GET', '/api/provider' + query), list)
        models = _data(self.request('GET', '/api/model' + query), list)
        result = {p['id']: {'id': p['id'], 'name': p.get('name', p['id']), 'models': {}} for p in providers}
        for m in models:
            if not m.get('enabled', False):
                continue
            pid = m['providerID']
            if pid not in result:
                raise _error('OpenCode v2 模型目录与 Provider 目录不一致；请刷新')
            capabilities = m.get('capabilities') or {}
            result[pid]['models'][m['id']] = {'name': m.get('name', m['id']), 'limit': m.get('limit') or {},
                'capabilities': capabilities, 'attachment': 'image' in capabilities.get('input', []),
                'variants': [v['id'] for v in m['variants']] if isinstance(m.get('variants'), list) else None}
        return {'providers': list(result.values())}

    def provider_settings(self):
        protocols = {'@opencode/ai/providers/openai-compatible': 'chat-completions',
                     '@opencode/ai/providers/openai': 'responses',
                     '@opencode/ai/providers/anthropic': 'anthropic-messages'}
        providers = _data(self.request('GET', '/api/provider'), list)
        models = _data(self.request('GET', '/api/model'), list)
        result = []
        for p in providers:
            protocol = protocols.get(p.get('package'))
            if not protocol:
                continue
            base = (p.get('settings') or {}).get('baseURL', '')
            url = urllib.parse.urlsplit(base)
            if url.username or url.password or url.query or url.fragment:
                base = ''
            for m in models:
                if m.get('providerID') != p['id']:
                    continue
                limits = m.get('limit') or {}
                result.append({'provider': p['id'], 'name': p.get('name', p['id']), 'model': m['id'],
                    'base_url': base, 'protocol': protocol, 'context_limit': limits.get('context'),
                    'output_limit': limits.get('output'), 'supports_images': 'image' in (m.get('capabilities') or {}).get('input', []),
                    'runtime': 'opencode', 'editable': False})
        return result
