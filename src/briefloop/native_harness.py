"""Packet-confined sessions on the BriefLoop-owned native engine.

BriefLoop controls the role prompt, tool list and lifecycle. Interactive chat
uses scoped business tools. Background roles read only their assigned packet;
any research, publication or delegation is a separately admitted runner tool.
The independent Reviewer retains its packet-only allowlist.

The system prompt is BriefLoop's layered contract for the role (agent_prompts);
the engine appends the guide for the tools it registered. submit_review is
admitted here with the rules that later save the review; runner tools the role
declared are executed here when the engine forwards a call (tool_request).
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import queue
import re
import threading
import time
from .chat_store import ChatStore
from .execution_records import sanitize
from .harness import InternalRun
from .native_engine import NativeEngine
from .store import uid

THINKING_LEVELS = {'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
# Default when no effort is selected. On the seeded slice evaluation (2026-09-18)
# the Reviewer caught the same defects at low as at high while thinking ~40%
# less; low was faster and cheaper than Opencode at high on a held-out set.
DEFAULT_THINKING = 'low'


def _thinking(config):
    value = config.get('variant') or config.get('model_variant') or config.get('effort')
    if isinstance(value, str) and value.strip().lower() in THINKING_LEVELS:
        return value.strip().lower()
    return DEFAULT_THINKING


class NativeHarness:
    backend = 'briefloop-native'

    def __init__(self, store, engine=None):
        self.store = store
        self.chat = ChatStore(store)
        self.engine = engine or NativeEngine()
        self.client = self.engine  # InteractiveRuntime inspects client.process.
        self._lock = threading.RLock()
        self._closed = False
        self._busy = set()
        self._cancel_requested = set()
        self._engine_sessions = {}
        self._epoch = {}
        self._child_runtimes = {}

    def list_sessions(self, view='active'):
        return self.chat.sessions(view)

    def _set_lifecycle(self, sid, lifecycle):
        with self._lock:
            if lifecycle != 'active' and sid in self._busy:
                raise ValueError('会话正在启动，请先停止或等待完成')
            self.chat.set_lifecycle(sid, lifecycle)
            self.chat.event(sid, 'session/lifecycle', {'lifecycle': lifecycle})
            return self.snapshot(sid)

    def archive(self, sid):
        return self._set_lifecycle(sid, 'archived')

    def restore(self, sid):
        return self._set_lifecycle(sid, 'active')

    def delete(self, sid):
        return self._set_lifecycle(sid, 'deleted')

    def archive_completed(self, backend='briefloop-native'):
        count = 0
        with self._lock:
            for session in self.list_sessions():
                if session.get('runtime', {}).get('backend', 'codex') != backend:
                    continue
                if not self.snapshot(session['id'])['messages']:
                    continue
                try:
                    self.archive(session['id'])
                except ValueError:
                    continue
                count += 1
        return {'count': count}

    def create_session(self, title='新对话', runtime=None, cwd=None):
        return self.chat.create(title, self._config(runtime), cwd or self.store.root)

    def snapshot(self, session_id, after=0, reasoning=False):
        return self.chat.snapshot(session_id, after, reasoning=reasoning)

    def list_models(self, refresh=False):
        return self.engine.call('list_models', {}, timeout=30).get('models', [])

    @staticmethod
    def _config(runtime):
        restricted = bool((runtime or {}).get('packet_root') or (runtime or {}).get('review_root'))
        value = {'permission': 'read-only' if restricted else 'workspace-write', **(runtime or {})}
        value.setdefault('native_role', 'reviewer' if restricted else 'chat')
        if value.get('backend', 'briefloop-native') != 'briefloop-native':
            raise ValueError('内置引擎会话不能使用其他后端的模型配置')
        value['backend'] = 'briefloop-native'
        model = value.get('model')
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_./:-]+', model.strip()):
            raise ValueError('内置引擎模型需要 provider/model 形式（如 deepseek/deepseek-v4-pro）')
        value['model'] = model.strip()
        # Background roles are packet-confined (native_roles): the
        # session itself reads its packet and nothing else; whatever else a
        # role may do (a Scout's metered search and page registration) is a
        # runner tool. Refusing here keeps a misrouted generate/learn job from
        # ever reaching a model with a permission it was not designed for.
        from .native_roles import role_of
        role = role_of(value)
        if role == 'chat':
            if value.get('permission') not in ('read-only', 'workspace-write'):
                raise ValueError('内置引擎仅支持只读或读写工作区业务工具')
            return value
        if value.get('permission') != 'read-only' or not (value.get('packet_root') or value.get('review_root')):
            raise ValueError('内置引擎当前仅执行只读核查包内的角色任务')
        if role in ('orchestrator', 'fact_checker') and not (value.get('job_id') and (value.get('run_id') or value.get('template_id')) and value.get('task_kind')):
            raise ValueError('后台主 Agent 缺少冻结的任务身份')
        if role in ('scout', 'analyst') and not (value.get('run_id') and value.get('result_file')):
            raise ValueError(f'{role.title()} 任务缺少所属报告或结果文件')
        return value

    # -- messaging ------------------------------------------------------

    def send(self, session_id, text, mode='queue', source_ids=None, runtime=None,
             message_id=None, display_text=None, allow_web=False):
        if mode != 'queue':
            raise ValueError('内置引擎不支持运行中追加；请排队发送')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('请输入消息')
        for sid in source_ids or []:
            self.store.one('sources', sid)
        session = self.chat.session(session_id)
        coordinator = getattr(self, 'coordinator', None)
        config = coordinator.config(self, session, runtime) if coordinator else self._config({**session['runtime'], **(runtime or {})})
        mid = message_id or uid('msg')
        with self._lock:
            if session['lifecycle'] != 'active':
                raise ValueError('会话已归档或删除，请先恢复会话')
            prior = next((m for m in self.snapshot(session_id)['messages'] if m['id'] == mid), None)
            if prior:
                return prior
            message = self.chat.message(
                session_id, display_text if display_text is not None else text,
                source_ids=source_ids, mid=mid, runtime=config,
                prompt=text if display_text is not None else None, allow_web=allow_web)
            self.chat.event(session_id, 'message/queued', {'messageId': mid, 'mode': 'queue'})
            self._schedule(session_id)
        message.pop('prompt', None)
        return message

    def start_internal(self, text, *, session_id=None, runtime=None, cwd=None, job_id=None,
                       display_text=None, allow_web=False, message_id=None, search_provider=None,
                       search_policy=None, source_ids=None):
        runtime = {'permission': 'read-only', **(runtime or {}), 'backend': 'briefloop-native'}
        if session_id is None:
            session_id = self.create_session('简报任务', runtime, cwd)['id']
        self.chat.event(session_id, 'session/internal', {})
        if job_id:
            self.chat.event(session_id, 'job/attached', {'jobId': job_id})
        message = self.send(session_id, text, runtime=runtime, display_text=display_text,
                            allow_web=allow_web, message_id=message_id, source_ids=source_ids)
        return InternalRun(session_id, message['id'])

    def answer(self, session_id, request_id, answers):
        raise ValueError('内置引擎的工具由 BriefLoop 控制，不存在宿主授权提问')

    def _schedule(self, sid):
        coordinator = getattr(self, 'coordinator', None)
        if coordinator:
            return coordinator.schedule(self, sid)
        return self._schedule_native(sid)

    def _schedule_native(self, sid):
        if self._closed:
            return
        if self.chat.session(sid)['lifecycle'] != 'active':
            return
        if sid in self._busy or self.chat.session(sid).get('turn_id'):
            return
        if not any(m['status'] == 'queued' for m in self.snapshot(sid)['messages']):
            return
        self._cancel_requested.discard(sid)
        self._busy.add(sid)
        self._epoch[sid] = self._epoch.get(sid, 0) + 1
        threading.Thread(target=self._dispatch, args=(sid, self._epoch[sid]), daemon=True).start()

    # -- driver -----------------------------------------------------------

    def _engine_session(self, sid, config, cwd):
        # Engine sessions live in one engine process. The bridge retires an idle
        # process after IDLE_SECONDS, so a session bound to an earlier process
        # no longer exists; recreate it from its transcript instead of sending
        # a turn to an unknown session. The caller already holds an event
        # subscription, which keeps the current process from being retired.
        existing = self._engine_sessions.get(sid)
        identity = hashlib.sha256(json.dumps({k:v for k,v in config.items() if k not in ('attempt_id', 'session_id', 'allow_web', 'discuss_only')}, sort_keys=True).encode()).hexdigest()
        process = self.engine.process
        previous = self.chat.session(sid).get('thread_id')
        if existing and process is existing['process'] and (existing.get('identity') != identity or existing.get('session_file') != previous):
            self.engine.call('session_close', {'session_id': sid}, timeout=15)
            self._engine_sessions.pop(sid, None)
            existing = None
        if existing is not None and process is not None and existing['process'] is process:
            return existing
        previous = (existing or {}).get('session_file') or self.chat.session(sid).get('thread_id')
        from .agent_prompts import system_prompt
        from .native_roles import role_of, runner_tool_specs
        role = role_of(config)
        prompt = system_prompt(role, 'interactive' if role == 'chat' else 'background')
        params = {
            'system_prompt': prompt['text'],
            'session_id': sid,
            'role': role,
            'packet_root': config.get('packet_root') or config.get('review_root') or str(cwd),
            'runner_tools': runner_tool_specs(role, config.get('evaluation_mode'), config),
            'session_dir': str(cwd),
            'model': config['model'],
            'thinking': _thinking(config),
        }
        if config.get('review_id'):
            # Structure is checked by the same admission that saves the review.
            params['admission'] = 'runner'
        if isinstance(previous, str) and previous.endswith('.jsonl'):
            params['session_file'] = previous
        from .native_roles import bind_session
        # Learning steps record this session as the WikiSkill child before it runs.
        bind_session(config, sid)
        result = {**self.engine.call('session_create', params, timeout=60), 'process': self.engine.process, 'identity': identity}
        self._engine_sessions[sid] = result
        coordinator = getattr(self, 'coordinator', None)
        native_id = result.get('session_file') or sid
        if coordinator:
            coordinator.bind(sid, self.chat.session(sid).get('turn_id') or '', native_id)
        else:
            self.chat.update(sid, thread_id=native_id)
        self.chat.event(sid, 'session/bound', {
            'backend': 'briefloop-native',
            'engine_session': sid,
            'role': role,
            'session_file': result.get('session_file'),
            'resumed': bool(result.get('resumed')),
            'model': result.get('model'),
            'prompt_version': prompt['version'],
            'system_prompt_sha256': result.get('system_prompt_sha256'),
            'image_input': result.get('image_input'),
            'runtime_policy': result.get('runtime_policy'),
            'tools': result.get('tools'),
        })
        return result

    def _dispatch(self, sid, epoch):
        mid = None
        execution = None
        evidence_open = False
        status = 'failed'
        try:
            session = self.chat.session(sid)
            message = next((m for m in self.chat.snapshot(sid, private=True)['messages']
                            if m['status'] == 'queued'), None)
            if message is None:
                return
            mid = message['id']
            execution = mid
            coordinator = getattr(self, 'coordinator', None)
            config = self._config(message.get('runtime') or session['runtime'])
            config = {**config, 'attempt_id': execution}
            if config.get('native_role') == 'chat':
                config['allow_web'] = bool(message.get('allow_web', False))
                config['discuss_only'] = message['text'].lstrip().startswith('/discuss')
                from .chat_tools import chat_instructions
                context = chat_instructions(self.store, config, allow_web=config['allow_web'], backend=self.backend)
                context += '\n内置引擎没有 shell 或任意文件写入。workspace_action 直接传 request JSON。修改稿件时使用 action=revise_document、base_version 和完整 editor_document 对象，不传 document_file。普通答复直接写文字，不要求 JSON；正式报告由 generate 提交给后台。内置引擎没有宿主搜索，报告检索使用设置中已授权的受控渠道。'
            else:
                context = ''
            if config.get('native_role') == 'scout':
                from .scout_evidence import begin
                begin(self.store, config)
                evidence_open = True
            text = (coordinator.input(sid, message) if coordinator else message).get('prompt') or message['text']
            text = context + '\n\n' + text if context else text
            if message.get('source_ids'):
                text += '\n本轮附件来源 ID：' + json.dumps(message['source_ids'], ensure_ascii=False)
            sink = self.engine.subscribe(execution)
            with self._lock:
                self.chat.patch_message(mid, status='sending', turn_id=mid)
                self.chat.update(sid, turn_id=mid, status='starting', runtime=config)
                self.chat.event(sid, 'runtime/admission',
                                {'execution_id': execution, 'status': 'pending',
                                 'backend': 'briefloop-native'})
            if sid in self._cancel_requested:
                status = 'cancelled'
                return
            try:
                self._engine_session(sid, config, session['cwd'])
            except Exception as exc:
                self.chat.event(sid, 'runtime/admission',
                                {'execution_id': execution, 'status': 'failed'})
                raise RuntimeError('内置引擎会话创建失败：' + str(exc)) from exc
            self.engine.call('turn_start', {
                'session_id': sid, 'execution_id': execution, 'prompt': text,
                'expect_json': config.get('native_role') != 'chat', 'require_submit': config.get('native_role') != 'chat',
                'images': self._visual_inputs(config)}, timeout=30)
            self.chat.event(sid, 'runtime/admission',
                            {'execution_id': execution, 'status': 'accepted'})
            self.chat.patch_message(mid, status='delivered')
            self.chat.update(sid, status='running')
            assistant = self.chat.message(sid, '', role='assistant', status='streaming',
                                          turn_id=mid, runtime=config)
            output = ''
            reasoning = ''
            tools = {}
            from .execution_timing import policy
            import time
            minutes = policy(self.store, session_id=sid)['hard_timeout_minutes']
            started = time.monotonic()
            while True:
                if minutes > 0 and time.monotonic() - started > minutes * 60:
                    self.engine.call('turn_abort', {'session_id': sid}, timeout=10)
                    raise TimeoutError('运行超过明确设置的最长运行保护，已保存内容保留')
                if sid in self._cancel_requested:
                    try:
                        self.engine.call('turn_abort', {'session_id': sid}, timeout=10)
                    except Exception:
                        pass
                try:
                    event = sink.get(timeout=.5)
                except queue.Empty:
                    continue
                kind = event['kind']
                if kind == 'text_reset':
                    # A new wire turn (e.g. the engine's JSON repair prompt)
                    # replaces this assistant message's accumulated text.
                    output = ''
                    reasoning = ''
                    self.chat.patch_message(assistant['id'], text='', reasoning='')
                elif kind == 'text':
                    output += event.get('delta', '')
                    self.chat.patch_message(assistant['id'], text=output)
                elif kind == 'reasoning':
                    reasoning += event.get('delta', '')
                    self.chat.patch_message(assistant['id'], reasoning=reasoning)
                elif kind == 'tool':
                    key = event.get('tool_call_id') or uid('tool')
                    tools[key] = {**tools.get(key, {}), **event}
                    tool = tools[key]
                    complete = tool.get('status') in ('completed', 'failed')
                    item = {'id': key, 'type': 'runtime_tool',
                            'tool': tool.get('name', '工具'),
                            'status': tool.get('status', 'running'),
                            'input': sanitize(tool.get('input')),
                            'output': sanitize(tool.get('output'))}
                    self.chat.event(sid, 'item/completed' if complete else 'item/started',
                                    {'item': item, 'turnId': mid})
                    if complete:
                        from .execution_records import journal_tool
                        journal_tool(self.chat, sid, mid, key, tool.get('name', 'tool'),
                                     tool.get('input') or {}, tool.get('output', ''),
                                     status=tool['status'],
                                     native_session=sid)
                elif kind == 'performance':
                    self.chat.event(sid, 'runtime/performance', {'turnId': mid, **sanitize(event)})
                elif kind == 'usage':
                    usage = event.get('usage') or {}
                    self.chat.event(sid, 'thread/tokenUsage/updated', {'tokenUsage': {
                        'backend': 'briefloop-native',
                        'last': {'inputTokens': usage.get('input'),
                                 'outputTokens': usage.get('output'),
                                 'cachedInputTokens': usage.get('cacheRead')},
                        'raw': usage}})
                elif kind == 'submit':
                    self._admit(sid, config, event)
                elif kind == 'tool_request':
                    # A submit runs in order on this loop. Other runner tools
                    # run off it, so a Scout's batched fetches proceed side by
                    # side (Store opens a connection per call); the engine
                    # matches results by request_id.
                    if self._sequential(config, event.get('tool')) and event.get('tool') not in ('run_scouts', 'write_report'):
                        self._run_tool(sid, config, event)
                    else:
                        threading.Thread(target=self._run_tool, args=(sid, config, event), daemon=True).start()
                elif kind == 'status':
                    self.chat.event(sid, 'runtime/status',
                                    {'turnId': mid, 'message': event.get('message', '')})
                elif kind == 'end':
                    status = {'completed': 'completed', 'cancelled': 'cancelled'}.get(
                        event.get('status'), 'failed')
                    if event.get('final_text'):
                        output = event['final_text']
                        self.chat.patch_message(assistant['id'], text=output)
                    if event.get('error'):
                        self.chat.event(sid, 'error', {'message': str(event['error'])[:300]})
                    if event.get('session_file'):
                        self.chat.event(sid, 'runtime/session',
                                        {'execution_id': execution,
                                         'session_file': event['session_file'],
                                         'backend': 'briefloop-native'})
                    break
        except Exception as exc:
            if mid:
                self.chat.event(sid, 'error', {'message': str(exc)[:300]})
        finally:
            with self._lock:
                children = list(self._child_runtimes.get(sid, ()))
            for runtime in children:
                runtime.cancel()
            with self._lock:
                if evidence_open:
                    from .scout_evidence import close
                    close(self.store, config)
                if mid:
                    for message in self.snapshot(sid)['messages']:
                        if (message.get('turn_id') == mid
                                and message['status'] in ('sending', 'delivered', 'streaming')):
                            self.chat.patch_message(message['id'], status=status)
                    self.chat.update(sid, turn_id=None,
                                     status='idle' if status == 'completed' else status)
                    self.chat.event(sid, 'turn/' + status, {'turnId': mid, 'status': status})
                self._busy.discard(sid)
                if execution:
                    try:
                        self.engine.unsubscribe(execution)
                    except Exception:
                        pass
                coordinator = getattr(self, 'coordinator', None)
                if coordinator:
                    coordinator.settle(sid)
                try:
                    if status == 'completed' and self.chat.session(sid)['status'] == 'idle':
                        self._schedule(sid)
                except KeyError:
                    pass

    def _visual_inputs(self, config):
        # Packet paths and hashes only; the engine re-reads and re-hashes the
        # bytes inside the packet and decides whether the model can take them.
        if not config.get('review_id'):
            return []
        from .review import visual_input_files
        return [{k: item[k] for k in ('id', 'kind', 'title', 'file', 'mime', 'sha256') if k in item}
                for item in visual_input_files(self.store, config['review_id'], config['review_root'])
                if item.get('bytes') is not None]

    def _admit(self, sid, config, event):
        error = None
        if config.get('review_id'):
            from .review import check_review
            try:
                check_review(self.store, config['review_id'], event.get('review'))
            except Exception as exc:
                error = str(exc)[:4000]
        self.chat.event(sid, 'runtime/status', {
            'turnId': self.chat.session(sid).get('turn_id'),
            'message': '审阅结果提交校验通过' if error is None else '审阅结果提交未通过校验，已退回模型修正'})
        try:
            self.engine.call('submit_result', {
                'session_id': sid, 'request_id': event.get('request_id'),
                'ok': error is None, 'error': error}, timeout=15)
        except Exception:
            pass  # The engine times the submission out and tells the model.

    @staticmethod
    def _settles(config, name):
        from .native_roles import role_of, runner_tool_specs
        specs = runner_tool_specs(role_of(config), config.get('evaluation_mode'), config)
        return any(spec['name'] == name and spec.get('settles') for spec in specs)

    @staticmethod
    def _sequential(config, name):
        from .native_roles import role_of, runner_tool_specs
        specs = runner_tool_specs(role_of(config), config.get('evaluation_mode'), config)
        return any(spec['name'] == name and (spec.get('settles') or spec.get('sequential')) for spec in specs)

    def cancel_requested(self, sid):
        with self._lock:
            return self._closed or sid in self._cancel_requested

    @contextmanager
    def child_runtime(self, sid, runtime):
        with self._lock:
            if self.cancel_requested(sid):
                raise InterruptedError('任务已停止，未启动子任务')
            self._child_runtimes.setdefault(sid, set()).add(runtime)
        try:
            yield runtime
        finally:
            with self._lock:
                self._child_runtimes.get(sid, set()).discard(runtime)

    def _run_tool(self, sid, config, event):
        from .native_roles import run_tool
        def invoke():
            if self.cancel_requested(sid) or self.chat.session(sid).get('turn_id') != config.get('attempt_id'):
                return {'ok': False, 'error': '本轮已取消或结束，未执行迟到操作'}
            return run_tool(self.store, {**config, 'session_id': sid, '_harness': self, '_active_attempt': config.get('attempt_id')}, event.get('tool'), event.get('args'))
        # Short mutations serialize with cancellation. Long child execution must
        # leave the shared driver lock free for child events and cancellation.
        if self._sequential(config, event.get('tool')) and event.get('tool') not in ('run_scouts', 'write_report'):
            with self._lock:
                result = invoke()
        else:
            result = invoke()
        if not result['ok']:
            self.chat.event(sid, 'runtime/status', {
                'turnId': config.get('attempt_id'),
                'message': f"{event.get('tool')} 未通过：{result.get('error', '')[:200]}"})
        if self.chat.session(sid).get('turn_id') not in (None, config.get('attempt_id')):
            return
        try:
            self.engine.call('tool_result', {'session_id': sid, 'request_id': event.get('request_id'), **result}, timeout=15)
        except Exception:
            pass

    def cancel(self, session_id):
        with self._lock:
            session = self.chat.session(session_id)
            self._cancel_requested.add(session_id)
            for message in self.snapshot(session_id)['messages']:
                if message['status'] == 'queued':
                    self.chat.patch_message(message['id'], status='cancelled')
            turn = session['turn_id']
            if turn or session_id in self._busy:
                self.chat.update(session_id, status='stopping')
            if turn:
                self.chat.event(session_id, 'turn/interruptRequested', {'turnId': turn})
        for runtime in list(self._child_runtimes.get(session_id, ())):
            runtime.cancel()
        if session_id in self._engine_sessions:
            try:
                self.engine.call('turn_abort', {'session_id': session_id}, timeout=10)
            except Exception:
                pass
        return self.snapshot(session_id)

    def close(self):
        with self._lock:
            self._closed = True
        for children in list(self._child_runtimes.values()):
            for runtime in list(children):
                runtime.cancel()
        # A call would start the engine just to stop it; only a running one
        # gets the chance to dispose its sessions before the bridge closes.
        if self.engine.process is not None:
            try:
                self.engine.call('shutdown', {}, timeout=5)
            except Exception:
                pass
        self.engine.close()
