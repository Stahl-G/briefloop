"""Route a chat's FIFO queue to its messages' frozen execution hosts."""
import threading

from .backends import validate_backend
from .chat_execution import ChatExecution


class ChatDispatcher:
    def __init__(self, managers):
        self.managers = managers
        self.chat = next(iter(managers.values())).chat
        self.execution = ChatExecution(self.chat)
        self.lock = threading.RLock()
        # Admission, cancel and native completion must share one lock. Drivers
        # retain their own clients, busy sets and native identity maps. Native I/O
        # must run outside this lock, retaining a busy/admission reservation.
        for manager in managers.values():
            manager._lock = self.lock
            manager.coordinator = self

    def internal(self, sid):
        return bool(self.chat.store.rows(
            "SELECT 1 FROM chat_events WHERE session_id=? AND kind='session/internal' LIMIT 1", (sid,)))

    def select(self, runtime=None, sid=None, *, sending=False):
        requested = (runtime or {}).get('backend')
        session = self.chat.session(sid) if sid else None
        owner = session['runtime'].get('backend', 'codex') if session else None
        if sid and self.internal(sid) and requested and requested != owner:
            raise ValueError('报告任务沿用冻结宿主，不允许在任务对话中切换')
        backend = requested if sending and requested else owner or requested
        backend = backend or self.chat.store.settings().get('agent_backend', 'codex')
        return self.managers[validate_backend(backend)]

    def config(self, manager, session, runtime):
        owner = session['runtime'].get('backend', 'codex')
        if owner != manager.backend:
            if self.internal(session['id']):
                raise ValueError('报告任务沿用冻结宿主，不允许在任务对话中切换')
            # Do not leak the previous host's provider, permission or model into
            # the new one. The target driver supplies its own validated defaults.
            return manager._config({**(runtime or {}), 'backend': manager.backend})
        return manager._config({**session['runtime'], **(runtime or {})})

    def schedule(self, origin, sid):
        with self.lock:
            if self.internal(sid):
                return origin._schedule_native(sid)
            if any(sid in manager._busy for manager in self.managers.values()):
                return
            queued = [m for m in self.chat.snapshot(sid)['messages']
                      if m['role'] == 'user' and m['status'] == 'queued']
            if not queued:
                return
            message = queued[0]
            manager = self.select(message['runtime'], sid, sending=True)
            try:
                segment = self.execution.admit(sid, message['id'])
            except ValueError as exc:
                # The unsent input remains visible; no model was started.
                self.chat.event(sid, 'error', {'message': str(exc), 'messageId': message['id']})
                return
            if segment is None:
                return
            if segment['first_message_id'] == message['id']:
                # Ignore old Codex notifications after changing execution segment.
                for driver in self.managers.values():
                    for name in ('_threads', '_children'):
                        bindings = getattr(driver, name, {})
                        for identity, owner in list(bindings.items()):
                            if owner == sid:
                                bindings.pop(identity, None)
                    for name in ('_items', '_reasoning', '_reasoning_target'):
                        entries = getattr(driver, name, {})
                        for key in list(entries):
                            if isinstance(key, tuple) and key[0] == sid:
                                entries.pop(key, None)
            manager._cancel_requested.discard(sid)
            manager._schedule_native(sid)

    def input(self, sid, message):
        if self.internal(sid):
            return message
        rows = self.chat.store.rows('''SELECT s.* FROM chat_execution_segments s
            JOIN chat_execution_messages m ON m.segment_id=s.id WHERE m.message_id=?''', (message['id'],))
        if not rows or rows[0]['first_message_id'] != message['id'] or not rows[0]['handoff']:
            return message
        prompt = message.get('prompt') if message.get('prompt') is not None else message['text']
        return {**message, 'prompt': rows[0]['handoff'] + '\n\n本轮用户请求：\n' + prompt}

    def bind(self, sid, mid, native_id):
        if self.internal(sid):
            self.chat.update(sid, thread_id=native_id)
            return True
        return self.execution.bind(sid, mid, native_id)

    def recover(self):
        # A crash after admission but before driver dispatch must not silently
        # replay that reservation, nor leave it forever marked queued.
        with self.chat.store.tx() as c:
            c.execute("""UPDATE chat_messages SET status='interrupted' WHERE status='queued'
                AND id IN (SELECT message_id FROM chat_execution_messages)""")

    def settle(self, sid):
        """A cancelled reservation may have no input by the time its driver runs."""
        if any(sid in driver._busy for driver in self.managers.values()):
            return
        snapshot = self.chat.snapshot(sid)
        if snapshot['session']['status'] in ('starting', 'stopping') and not any(
                message['status'] in ('queued', 'sending', 'delivered', 'streaming')
                for message in snapshot['messages']):
            self.chat.update(sid, status='interrupted', turn_id=None)
