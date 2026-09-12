"""Job execution through the same durable, steerable harness as user chat.

Each output folder binds to one conversation. Python observes files and public
transport events; the agent still owns all research, writing and learning work.
"""
from pathlib import Path
import json
import threading
import time
from .harness import HarnessManager
from .store import dump, now, uid

TERMINAL = {'completed', 'failed', 'interrupted', 'cancelled'}


def _write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(dump(value), encoding='utf-8')
    temporary.replace(path)


def _message(snapshot, message_id):
    return next((m for m in snapshot['messages'] if m['id'] == message_id), None)


def _usable_output(job, folder, store=None):
    """A completed model turn is not evidence that its required artifact exists."""
    from .models import BriefDraft
    if job.get('readonly_output'):
        try:return isinstance(json.loads((folder/job['readonly_output']).read_text()),dict)
        except (OSError,ValueError):return False
    if job.get('kind')=='repair_revision_metadata':
        try:return isinstance(json.loads((folder/'metadata.json').read_text()),dict)
        except (OSError,ValueError):return False
    role=job.get('runtime_role')
    if role in ('evaluator','scorer','assessor'):
        name='comparison.json' if job.get('evaluation_mode')=='pairwise' or role=='assessor' else 'assessment.json'
    elif job['kind'] in ('generate','revise'):name='draft.json'
    elif job['kind']=='assess':name='assessment.json'
    else:return True  # WikiSkill handoffs already request resume_on_complete.
    try:
        data=json.loads((folder/name).read_text())
        if name=='draft.json':BriefDraft.model_validate(data)
        elif name=='assessment.json':
            if store is None:return False
            payload=json.loads(job['payload'])
            version=payload.get('version_id')
            if job['kind']=='generate' and not version:version='brief_'+job['id'][4:]
            if not version:return False
            store.validate_assessment(version,data)
        elif not isinstance(data,dict) or not isinstance(data.get('pairs'),list):return False
        return True
    except (OSError,ValueError):return False


class InteractiveRuntime:
    def __init__(self, store, harness=None, *, backends=None):
        self.store = store
        if backends is None:
            harness = harness or HarnessManager(store)
            backends = {'codex': harness}
        self.backends = backends
        self.harness = backends['codex'] if 'codex' in backends else next(iter(backends.values()))
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.session_id = None
        self.session_backend = None

    def _harness_for(self, backend):
        from .backends import validate_backend
        try:
            return self.backends[validate_backend(backend)]
        except KeyError:
            raise ValueError(f'后端 {backend} 未在此服务中启用')

    @property
    def process(self):
        # This process is shared: cancellation must interrupt a turn, never kill it.
        with self.lock:
            if self.session_id is None:
                return None
            harness = self.backends.get(self.session_backend) if self.session_backend else self.harness
            return getattr(getattr(harness, 'client', None), 'process', None)

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            session_id = self.session_id
            harness = self.backends.get(self.session_backend) if self.session_backend else None
        if session_id and harness is not None:
            harness.cancel(session_id)

    def _input_source_ids(self,job,folder):
        """Only explicit/evaluator visual references become native attachments.

        Coordinators receive the task-pack index, not every PDF page's pixels.
        """
        if 'input_source_ids' in job:return list(dict.fromkeys(job['input_source_ids']))
        if job.get('runtime_role')!='evaluator':return []
        path=folder/'input.json'
        if not path.exists():return []
        packet=json.loads(path.read_text())
        ids=[]
        if isinstance(packet,dict):
            ids=[row.get('source_id') or row.get('id') for row in packet.get('sources',[])]
        elif isinstance(packet,list):
            for case in packet:
                for side in ('baseline','candidate'):
                    brief=case.get(side,{})
                    detail=brief.get('detail') or {}
                    if isinstance(detail,str):detail=json.loads(detail)
                    ids.extend(ref['source_id'] for ref in detail.get('citations',brief.get('citations',[])))
                    for row in (detail.get('report_data') or {}).get('records',[]):
                        ids.extend(row[key] for key in ('source_id','previous_source_id') if row.get(key))
        from .media import source_attachment
        visual=[]
        for sid in dict.fromkeys(sid for sid in ids if sid):
            attachment=source_attachment(self.store,sid)
            if (attachment.get('media_type') or '').startswith('image/') or attachment.get('media_type')=='application/pdf':visual.append(sid)
        return visual

    def execute(self, job, prompt, folder, on_tick=lambda: None, *, resume_on_complete=False):
        from .progress import ProgressTracker
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        resume_on_complete = resume_on_complete or not _usable_output(job, folder, self.store)
        tracker = ProgressTracker(self.store, job['id'], folder, context=job)
        deferred = [None]
        def tick():
            try:
                on_tick()  # Admit a complete draft while its evaluator is still working.
            except Exception as exc:
                # An artifact the agent is still writing cannot interrupt model work or
                # replace the real failure. The caller publishes authoritatively once
                # the turn ends and reports the reason from there.
                reason = str(exc)
                if reason != deferred[0]:
                    deferred[0] = reason
                    try:
                        self.store.event(job['id'], 'draft_admission_deferred', {'error': reason})
                    except Exception:
                        pass
            try:
                tracker.update()
            except Exception:
                pass  # A progress projection cannot interrupt model work.

        payload = json.loads(job['payload'])
        from .backends import validate_backend
        backend = validate_backend(payload.get('agent_backend', 'codex'))
        harness = self._harness_for(backend)
        configured = payload['runtime'] if 'runtime' in payload else self.store.runtime_config()
        runtime = {'model': configured['model'],
                   'effort': configured.get('reasoning_effort', configured.get('effort'))}
        if job.get('readonly_output'):
            if backend!='opencode':raise ValueError('此后端的受限 Reviewer 工具策略尚未验证；审阅未完成，不能退回普通写权限')
            runtime.update(permission='read-only',review_root=str((folder/'packet').resolve()))
            if job.get('review_id'):runtime['review_id']=job['review_id']
        if backend == 'codex' and 'service_tier' in configured:
            runtime['service_tier'] = configured['service_tier']
        if configured.get('model_provider'):
            runtime['model_provider'] = configured['model_provider']
        if configured.get('model_variant'):
            runtime['variant'] = configured['model_variant']
        saved = folder / 'execution.json'
        if saved.exists():
            previous = json.loads(saved.read_text())
            if previous.get('runtime') and previous['runtime'] != configured:
                raise ValueError('已保存执行的模型配置与本阶段不一致')
            if previous.get('backend', 'codex') != backend:
                raise ValueError('已保存执行的后端与本阶段不一致；跨后端请用新任务目录')
            if previous.get('returncode') == 0 and not resume_on_complete:
                tick()
                return previous
        if self.cancelled.is_set():
            tick()
            raise InterruptedError('任务已停止，已生成内容保留')

        marker = folder / 'conversation.json'
        binding = json.loads(marker.read_text()) if marker.exists() else None
        snapshot = None
        if binding:
            if binding['job_id'] != job['id'] or binding.get('backend', 'codex') != backend:
                raise ValueError('恢复会话的任务、后端或模型已改变；请使用新的任务目录')
            if binding['runtime'] != runtime:
                legacy = {key: value for key, value in runtime.items() if key != 'review_id'}
                if (job.get('readonly_output') != 'review.json' or not runtime.get('review_id')
                        or binding['runtime'] != legacy):
                    raise ValueError('恢复会话的任务、后端或模型已改变；请使用新的任务目录')
                # Earlier Review bindings predate this attachment identity. Add
                # only that identity after validating the same job/version and
                # fixed packet; model and native permission settings stay exact.
                from .review import get_review, validate_applicable_review
                review = get_review(self.store, runtime['review_id'])
                if (review['job_id'] != job['id'] or review['version_id'] != payload.get('version_id')
                        or (self.store.root / review['data']['packet_path']).resolve() != Path(runtime['review_root'])):
                    raise ValueError('旧 Reviewer 会话未绑定当前任务、正文与核查包')
                validate_applicable_review(self.store, review['id'], review['version_id'])
                binding['runtime'] = runtime
                _write(marker, binding)
            snapshot = harness.snapshot(binding['session_id'])
        else:
            evaluation_title='Evaluator · 比较' if job.get('evaluation_mode')=='pairwise' else 'Evaluator · 评分'
            title = {'evaluator': evaluation_title, 'scorer': 'Evaluator · 评分', 'assessor': 'Evaluator · 比较', 'maintainer': '整理反馈经验', 'proposer': '提出技能改进'}.get(job.get('runtime_role'))
            title = title or {'company_review':'维护企业背景', 'generate': '生成简报', 'assess': '核对简报评分', 'learn': '整理反馈与改进技能'}.get(job['kind'], '简报任务')
            session = harness.create_session(title, runtime, folder)
            binding = {'job_id': job['id'], 'session_id': session['id'], 'runtime': runtime,
                       'backend': backend, 'message_id': None, 'history': []}
            _write(marker, binding)

        message = _message(snapshot, binding['message_id']) if snapshot else None
        recovered = message is not None
        new_turn = message is None or message['status'] in TERMINAL
        if message and message['status'] == 'completed' and not resume_on_complete:
            new_turn = False
        if new_turn and snapshot and snapshot['session'].get('lifecycle','active')!='active':
            # An explicit job resume may need another turn, but must not undo a
            # user's archive/delete choice. Completed cached turns bypass this.
            old_sid=binding['session_id']
            session=harness.create_session('恢复简报任务',runtime,folder)
            binding.setdefault('previous_session_ids',[]).append(old_sid)
            binding['session_id']=session['id']
            if binding.get('message_id'):binding.setdefault('history',[]).append(binding['message_id'])
            binding['message_id']=None
            snapshot=None;message=None
            _write(marker,binding)
        if new_turn:
            # Persist the id before dispatch. A crash before/after send can re-enter
            # start_internal with that same id instead of duplicating the message.
            if message is not None or not binding['message_id']:
                if binding['message_id']:
                    binding['history'].append(binding['message_id'])
                binding['message_id'] = uid('msg')
            _write(marker, binding)
            from .runtime import runtime_instruction
            fixed = runtime_instruction(configured, backend)
            if binding['history']:
                fixed += '恢复这一个任务：先核对现有子 agent 和完整输出，复用已完成结果，只补未完成部分，不重新采样已完成稿件。\n'
            (folder / 'prompt.md').write_text(fixed + prompt, encoding='utf-8')
            _write(saved, {'returncode': None, 'status': 'running', 'session_id': binding['session_id'],
                           'message_id': binding['message_id'], 'runtime': configured, 'backend': backend})

        sid = binding['session_id']
        with self.lock:
            self.session_id = sid
            self.session_backend = backend
        started = time.monotonic()
        cursor = 0
        seen_messages = set()
        log_path = folder / 'events.jsonl'
        if log_path.exists():
            for line in log_path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                cursor = max(cursor, event.get('harness_seq', 0))
                if event.get('chat_message_id'):
                    seen_messages.add(event['chat_message_id'])
        try:
            if new_turn:
                if self.cancelled.is_set():
                    raise InterruptedError('任务已停止，已生成内容保留')
                label = {'company_review':'先检查并维护本轮企业背景，完成后再进入报告写作。', 'generate': '请按已保存的要求研究来源并生成简报。',
                         'assess': '请核对这份简报的要求、内容与来源并给出评分。',
                         'learn': '请继续整理反馈、更新经验并完成当前技能改进步骤。'}.get(job['kind'], '请完成当前简报任务。')
                evaluation_label='请使用 Evaluator 成对比较模式，依据任务与来源比较新旧稿件。' if job.get('evaluation_mode')=='pairwise' else '请使用 Evaluator 单稿评分模式，核对简报要求、内容与来源。'
                label = {'evaluator': evaluation_label, 'scorer': '请使用 Evaluator 单稿评分模式核对简报。', 'assessor': '请使用 Evaluator 成对比较模式核对新旧稿件。', 'maintainer': '请从反馈中整理可复用经验。', 'proposer': '请依据经验提出技能改进。'}.get(job.get('runtime_role'), label)
                harness.start_internal((folder / 'prompt.md').read_text(), session_id=sid,
                    runtime=runtime, cwd=folder, job_id=job['id'], display_text=label,
                    allow_web=bool(job.get('allow_web', False)), message_id=binding['message_id'],
                    search_provider=payload.get('search_provider','codex'),
                    source_ids=self._input_source_ids(job,folder))
                self.store.event(job['id'], 'runtime_started', {'session_id': sid,
                    'message_id': binding['message_id'], 'folder': str(folder), 'runtime': configured,
                    'backend': backend,
                    'transport': 'opencode-serve' if backend == 'opencode' else 'app-server'})
            while True:
                snapshot = harness.snapshot(sid, after=cursor)
                cursor = self._project(snapshot, log_path, cursor, seen_messages)
                tick()
                message = _message(snapshot, binding['message_id'])
                if message is None:
                    raise RuntimeError('已绑定的会话消息不存在，未自动重新发送')
                status = message['status']
                if self.cancelled.is_set():
                    harness.cancel(sid)
                    raise InterruptedError('任务已停止，已生成内容保留')
                if status in TERMINAL:
                    # Completion and its usage/message events can be persisted
                    # immediately after the status transition. Refresh and drain
                    # the paged public journal before admitting the result.
                    while True:
                        snapshot = harness.snapshot(sid, after=cursor)
                        cursor = self._project(snapshot, log_path, cursor, seen_messages)
                        if len(snapshot['events']) < 1000:
                            break
                    message = _message(snapshot, binding['message_id']) or message
                    result = {'returncode': 0 if status == 'completed' else 1, 'status': status,
                              'seconds': round(time.monotonic() - started, 2), 'finished': now(),
                              'runtime': configured, 'backend': backend, 'session_id': sid, 'message_id': binding['message_id'],
                              'recovered': recovered, 'usage': self._usage(log_path)}
                    assistant = [m['text'] for m in snapshot['messages']
                                 if m['role'] == 'assistant' and m.get('turn_id') == message.get('turn_id')]
                    if assistant:
                        (folder / 'last-message.txt').write_text('\n\n'.join(assistant), encoding='utf-8')
                    if status=='completed' and job.get('readonly_output'):
                        name=job['readonly_output']
                        if name not in ('review.json','permission-probe.json'):raise ValueError('无效只读输出文件名')
                        final='\n\n'.join(assistant).strip()
                        if final.startswith('```'):
                            final='\n'.join(final.splitlines()[1:-1])
                        data=json.loads(final)
                        if not isinstance(data,dict):raise ValueError('Reviewer 未返回 JSON 对象')
                        _write(folder/name,data)
                    _write(saved, result)
                    tick()
                    if status in ('interrupted', 'cancelled'):
                        raise InterruptedError('会话已中断，已生成内容保留，可恢复')
                    if status != 'completed':
                        raise RuntimeError('Agent 执行失败；详情保存在会话与任务日志')
                    return result
                if time.monotonic() - started > self.store.settings()['timeout_minutes'] * 60:
                    harness.cancel(sid)
                    raise TimeoutError('运行超过本轮时间上限，已保留稿件和执行记录')
                time.sleep(.5)
        except Exception as exc:
            if snapshot is not None:
                active = _message(snapshot, binding['message_id'])
                if active and active['status'] not in TERMINAL:
                    try:
                        harness.cancel(sid)
                    except Exception:
                        pass  # Preserve the original failure and bound session.
            if isinstance(exc, (InterruptedError, TimeoutError)):
                _write(saved, {'returncode': 1, 'status': 'interrupted', 'finished': now(),
                               'session_id': sid, 'message_id': binding['message_id'], 'error': str(exc),
                               'backend': backend})
            with (folder / 'stderr.log').open('a', encoding='utf-8') as errors:
                errors.write(str(exc) + '\n')
            tick()
            raise
        finally:
            with self.lock:
                self.session_id = None
                self.session_backend = None

    @staticmethod
    def _project(snapshot, log_path, cursor, seen_messages):
        with log_path.open('a', encoding='utf-8') as log:
            for event in snapshot['events']:
                if event['seq'] <= cursor:
                    continue
                cursor = event['seq']
                kind, data = event['kind'], event['data']
                value = {'type': kind.replace('/', '.'), 'harness_seq': cursor, 'data': data}
                if kind.removeprefix('child/') in ('item/started', 'item/updated', 'item/completed'):
                    item = dict(data.get('item', {}))
                    aliases = {'collabAgentToolCall': 'collab_tool_call', 'commandExecution': 'command_execution'}
                    item['type'] = aliases.get(item.get('type'), item.get('type'))
                    if 'agentsStates' in item:
                        item['agents_states'] = item.pop('agentsStates')
                    value['item'] = item
                if kind == 'thread/tokenUsage/updated':
                    value['usage'] = data.get('tokenUsage', {})
                log.write(json.dumps(value, ensure_ascii=False) + '\n')
            for message in snapshot['messages']:
                if message['role'] == 'assistant' and message['status'] == 'completed' and message['id'] not in seen_messages:
                    seen_messages.add(message['id'])
                    log.write(json.dumps({'type': 'item.completed', 'chat_message_id': message['id'],
                        'item': {'type': 'agent_message', 'text': message['text']}}, ensure_ascii=False) + '\n')
        return cursor

    @staticmethod
    def _usage(path):
        values = []
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if 'usage' in event:
                values.append(event['usage'])
        return values
